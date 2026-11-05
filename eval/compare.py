"""对比两条研究线(例如 fwcore 与 Pi)在同一目标上的结论与证据。

为什么先做工具:与 Pi 线双跑需要外部条件(暂阻塞),但**对比口径**可以先固定下来,
否则将来跑完只能"看感觉"。本工具只吃结构化输入,不做解释:
  { "target": str, "version": str,
    "conclusions": [{"title","verdict","scope","evidence": [{"kind","sha256"|"capture"}]}] }
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


def load(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"读不到对比输入 {path}: {e}") from e
    data.setdefault("conclusions", [])
    return data


@dataclass
class Diff:
    only_a: list = field(default_factory=list)       # A 有、B 无(按 title 归一)
    only_b: list = field(default_factory=list)
    verdict_mismatch: list = field(default_factory=list)
    evidence_gap: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"only_a": self.only_a, "only_b": self.only_b,
                "verdict_mismatch": self.verdict_mismatch, "evidence_gap": self.evidence_gap}


def _key(c: dict) -> str:
    return (c.get("title") or "").strip().lower()[:80]


def _ev_count(c: dict) -> int:
    return len(c.get("evidence") or [])


def compare(a: dict, b: dict) -> dict:
    """返回结构化差异 + 一行判定(便于写进报告/证据)。"""
    A = {_key(c): c for c in a.get("conclusions") or []}
    B = {_key(c): c for c in b.get("conclusions") or []}
    d = Diff(only_a=sorted(set(A) - set(B)), only_b=sorted(set(B) - set(A)))
    for k in sorted(set(A) & set(B)):
        va, vb = (A[k].get("verdict") or "").lower(), (B[k].get("verdict") or "").lower()
        if va != vb:
            d.verdict_mismatch.append({"title": k, "a": va, "b": vb})
        ea, eb = _ev_count(A[k]), _ev_count(B[k])
        if ea == 0 and eb > 0:
            d.evidence_gap.append({"title": k, "a_evidence": ea, "b_evidence": eb})
    out = d.as_dict()
    out["same_verdict"] = sum(1 for k in set(A) & set(B)
                              if (A[k].get("verdict") or "").lower() == (B[k].get("verdict") or "").lower())
    out["summary"] = (f"A={len(A)} 条 / B={len(B)} 条;仅A={len(out['only_a'])} 仅B={len(out['only_b'])} "
                      f"判定分歧={len(out['verdict_mismatch'])} 证据缺口={len(out['evidence_gap'])}")
    return out
