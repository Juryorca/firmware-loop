"""Pi-compatible tool discovery / BM25 ranking.

This is the Python adapter for Pi v1.5.0's tool-search contract:
- search name, description, schema text, and MCP namespace text;
- exact full/local tool-name matches rank first;
- return a bounded result list;
- ``tool_search`` activates matching ``deferred``/``codemode`` tools for the
  next model request.  Codemode's ``searchTools`` uses the same ranker but does
  not change model visibility.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

DEFAULT_TOOL_SEARCH_LIMIT = 8
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "that", "the", "this", "to", "with",
}
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)
_ASCII_RE = re.compile(r"^[a-z0-9 ]*$")


def _stem(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and re.search(r"(ches|shes|sses|xes|zes)$", word):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def tokenize(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text or "")
    # camelCase / PascalCase boundaries, like Pi's tokenizer.
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", normalized)
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", normalized)
    normalized = normalized.lower().replace("-", " ").replace("_", " ")
    if _ASCII_RE.match(normalized):
        words = normalized.split(" ")
    else:
        words = _WORD_RE.findall(normalized)
    return [_stem(w) for w in words if w and w not in _STOP_WORDS]


def _schema_text(schema, parts: list[str]) -> None:
    if not isinstance(schema, dict):
        return
    desc = schema.get("description")
    if isinstance(desc, str):
        parts.append(desc)
    props = schema.get("properties")
    if isinstance(props, dict):
        for name, prop in props.items():
            parts.append(str(name))
            _schema_text(prop, parts)
    _schema_text(schema.get("items"), parts)
    for key in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(key)
        if isinstance(variants, list):
            for variant in variants:
                _schema_text(variant, parts)


def _namespace_text(meta: dict) -> str:
    ns = meta.get("namespace")
    if not ns:
        return ""
    # fwcore stores the server name only; include it as a field.
    return str(ns)


def _doc(meta: dict) -> dict:
    parts = [str(meta.get("description") or "")]
    _schema_text(meta.get("input_schema"), parts)
    text = " ".join(p for p in parts if p.strip())
    name = str(meta.get("name") or "")
    segments = name.split("__")
    return {
        "name": name,
        "name_terms": set(tokenize(name)),
        "local_terms": set(tokenize(segments[-1] if segments else name)),
        "text_counts": Counter(tokenize(text)),
        "text_length": sum(Counter(tokenize(text)).values()),
        "namespace_terms": set(tokenize(_namespace_text(meta))),
    }


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_$]", "_", name.strip().lower())


def _exact(query: str, name: str) -> bool:
    q = _normalize_name(query)
    n = _normalize_name(name)
    return n == q or n.endswith("__" + q)


class Bm25Ranker:
    """Small Python port of Pi's name/namespace/description BM25 ranker."""

    def __init__(self, k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b

    def rank(self, query: str, documents: list[dict], limit: int) -> list[dict]:
        terms = set(tokenize(query))
        if not terms or not documents or limit <= 0:
            return []
        indexed = [_doc(d) for d in documents]
        avg = sum(d["text_length"] for d in indexed) / len(indexed) or 1
        idf: dict[str, float] = {}
        for term in terms:
            freq = sum(1 for d in indexed if
                       term in d["name_terms"] or term in d["text_counts"] or
                       term in d["namespace_terms"])
            idf[term] = math.log(1 + (len(indexed) - freq + 0.5) / (freq + 0.5))
        full_bonus = sum(idf.values())
        exact_query = _normalize_name(query)
        matches: list[dict] = []
        for doc, meta in zip(indexed, documents, strict=False):
            norm = self.k1 * (1 - self.b + self.b * doc["text_length"] / avg)
            score = 0.0
            covered = 0
            for term in terms:
                count = doc["text_counts"].get(term, 0)
                weight = (3.0 if term in doc["name_terms"] else 0.0)
                weight += 0.5 if term in doc["namespace_terms"] else 0.0
                weight += count * (self.k1 + 1) / (count + norm)
                if weight > 0:
                    covered += 1
                score += idf.get(term, 0.0) * weight
            if score <= 0:
                continue
            if doc["local_terms"] and doc["local_terms"].issubset(terms):
                score += full_bonus
            matches.append({
                "name": str(meta.get("name") or ""),
                "score": score,
                "covered": covered,
                "exact": _exact(exact_query, str(meta.get("name") or "")),
            })
        matches.sort(key=lambda m: (not m["exact"], -m["covered"], -m["score"]))
        return [{"name": m["name"], "score": round(m["score"], 6)}
                for m in matches[:limit]]


def search_metadata(metadata: list[dict], query: str, limit: int) -> list[dict]:
    """Rank metadata and return full metadata for the matching names."""
    ranked = Bm25Ranker().rank(query, metadata, limit)
    by_name = {m.get("name"): m for m in metadata}
    return [by_name[r["name"]] for r in ranked if r["name"] in by_name]


def register_tool_search(registry) -> None:
    """Register the model-facing ``tool_search`` tool (Pi v1.5 semantics)."""
    from .tools import ToolSpec

    def _search(ctx, args: dict) -> dict:
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"status": "error", "error": "query must not be empty"}
        raw_limit = args.get("limit", DEFAULT_TOOL_SEARCH_LIMIT)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            return {"status": "error", "error": "limit must be a positive integer"}
        if limit <= 0 or limit > 100:
            return {"status": "error", "error": "limit must be between 1 and 100"}
        candidates = [m for m in registry.search_metadata()
                      if m.get("name") not in registry.active_deferred]
        matches = search_metadata(candidates, query, limit)
        loaded = [str(m["name"]) for m in matches]
        activated = registry.activate_deferred(loaded)
        if getattr(ctx, "run_id", None) and activated:
            ctx.domain.activate_tools(ctx.run_id, activated)
        lines = [
            f"Loaded {len(activated)} tool(s). They are available from your next call:"
            if activated else "No matching tools found."
        ]
        for meta in matches:
            if meta.get("name") in activated:
                desc = str(meta.get("description") or "").strip().splitlines()
                lines.append(f"- {meta['name']}: {desc[0] if desc else ''}")
        text = "\n".join(lines)
        return {
            "status": "ok",
            "loaded": activated,
            "tools": [{"name": m["name"], "description": m.get("description", "")}
                      for m in matches if m.get("name") in activated],
            "text": text,
            "query": query,
            "limit": limit,
        }

    registry.register(ToolSpec(
        "tool_search",
        "Search deferred/codemode tools by name, description, schema, or MCP namespace. "
        "Matching tools become available to the model on the next request.",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "limit": {"type": "number", "description": "Default 8; at most 100"},
         }, "required": ["query"]},
        _search, exposure="direct",
    ))
