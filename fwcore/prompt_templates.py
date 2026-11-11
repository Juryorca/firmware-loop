"""Pi-compatible Markdown prompt templates for the fw-core TUI.

A direct child ``.pi/prompts/<name>.md`` becomes ``/<name>``.  This is the
same lightweight contract as Pi's prompt templates: YAML-ish frontmatter for
description/argument-hint, shell-like positional arguments, and no executable
behavior.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class PromptTemplate:
    name: str
    description: str
    arg_hint: str
    body: str
    path: str


def _frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end < 0:
        # An opened frontmatter block must be closed; otherwise this is not a
        # usable template (and must not become a command with a bogus body).
        return {}, ""
    header = text[4:end]
    body = text[end + 4:].lstrip("\n")
    meta: dict[str, str] = {}
    for line in header.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, body


def discover(root: str) -> dict[str, PromptTemplate]:
    """Load direct Markdown children; invalid templates are ignored."""
    out: dict[str, PromptTemplate] = {}
    directory = os.path.join(root, ".pi", "prompts")
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return out
    for filename in names:
        if not filename.endswith(".md"):
            continue
        path = os.path.join(directory, filename)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                raw = f.read()
        except OSError:
            continue
        meta, body = _frontmatter(raw)
        if not body.strip():
            continue
        name = os.path.splitext(filename)[0]
        description = meta.get("description") or next(
            (line.strip() for line in body.splitlines() if line.strip()), name)
        out[name] = PromptTemplate(
            name=name, description=description,
            arg_hint=meta.get("argument-hint", ""),
            body=body, path=path)
    return out


def _matching_brace(text: str, start: int) -> int:
    """Return the ``}`` matching the ``${`` at *start* (nested-aware)."""
    depth = 1
    i = start + 2
    while i < len(text):
        if text.startswith("${", i):
            depth += 1
            i += 2
            continue
        if text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _eval_expr(expr: str, args: list[str]) -> str:
    all_args = " ".join(args)

    def pos(n: int) -> str:
        return args[n - 1] if 0 < n <= len(args) else ""

    if expr == "@":
        return all_args
    if expr.startswith("@:"):
        rest = expr[2:]
        if rest.startswith("-"):
            return all_args or _replace(rest[1:], args)
        parts = rest.split(":")
        try:
            start = int(parts[0])
            length = int(parts[1]) if len(parts) > 1 and parts[1] else None
        except ValueError:
            return "${" + expr + "}"
        tail = args[start - 1:]
        return " ".join(tail if length is None else tail[:length])
    if ":-" in expr:
        head, default = expr.split(":-", 1)
        if head.isdigit():
            return pos(int(head)) or _replace(default, args)
    if ":+" in expr:
        head, replacement = expr.split(":+", 1)
        if head.isdigit():
            return _replace(replacement, args) if pos(int(head)) else ""
    if expr.isdigit():
        return pos(int(expr))
    return "${" + expr + "}"


def _replace(text: str, args: list[str]) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if text[i] != "$":
            out.append(text[i])
            i += 1
            continue
        if text.startswith("$ARGUMENTS", i):
            out.append(" ".join(args))
            i += len("$ARGUMENTS")
            continue
        if text.startswith("$@", i):
            out.append(" ".join(args))
            i += 2
            continue
        if text.startswith("${", i):
            end = _matching_brace(text, i)
            if end < 0:
                out.append(text[i:])
                break
            out.append(_eval_expr(text[i + 2:end], args))
            i = end + 1
            continue
        if i + 1 < len(text) and text[i + 1].isdigit():
            j = i + 1
            while j < len(text) and text[j].isdigit():
                j += 1
            n = int(text[i + 1:j])
            out.append(args[n - 1] if 0 < n <= len(args) else "")
            i = j
            continue
        out.append("$")
        i += 1
    return "".join(out)


def expand(template: PromptTemplate, argument: str) -> str:
    """Expand Pi-style ``$1``/``$@``/``${1:-default}`` placeholders."""
    try:
        args = shlex.split(argument or "")
    except ValueError:
        args = [argument or ""]
    return _replace(template.body, args)
