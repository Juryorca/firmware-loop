"""评测评分器(支持"有缺陷"与"对照组"两类答案)。

为什么单独成模块:评分器本身也会出错(历史上把证据引用里的"全部"误判成过度概括)。
因此:判据要双条件、可离线重评,并且**对照任务**必须能识别假阳性。
"""

from __future__ import annotations

import re

NARROW = re.compile(r"仅|只限|只覆盖|该入口|这条路径|这个函数|这一条|函数内|适用范围")
OVERCLAIM = re.compile(r"整个组件|该组件全部|所有路径均|全组件|组件整体|entire component")


def _evidence_linked(con, hypothesis_id: str) -> int:
    """与该结论**直接关联**的证据数(必须有因果链,不只看总数)。"""
    return con.execute(
        "SELECT COUNT(*) n FROM evidence WHERE hypothesis_id=?", (hypothesis_id,)).fetchone()["n"]


def score(answers: dict, con, target_id: str) -> dict:
    exp = answers.get("expected") or {}
    rows = con.execute(
        "SELECT id,title,verdict,state,scope_note FROM hypotheses WHERE target_id=?", (target_id,)
    ).fetchall()
    vulns = [r for r in rows if r["verdict"] == "vulnerable"]
    nofind = [r for r in rows if r["verdict"] == "no_finding"]
    ev_total = con.execute("SELECT COUNT(*) n FROM evidence WHERE target_id=?", (target_id,)).fetchone()["n"]
    decisive = con.execute(
        "SELECT COUNT(*) n FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id"
        " WHERE h.target_id=? AND a.result IN ('supported','refuted')", (target_id,)).fetchone()["n"]
    obs_pending = con.execute(
        "SELECT COUNT(*) n FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id"
        " WHERE h.target_id=? AND a.result IN ('supported','refuted','inconclusive')"
        "   AND a.obs_outcome='pending'", (target_id,)).fetchone()["n"]

    checks: dict[str, bool] = {
        "has_evidence": ev_total >= 1,
        "has_decisive_attempt": decisive >= 1,
        "obs_outcome_recorded": obs_pending == 0,
    }

    if answers.get("control"):
        # 对照组:不能声称漏洞;要给出 no_finding 且带适用范围的结论
        checks["no_false_positive"] = len(vulns) == 0
        checks["control_concluded_no_finding"] = any(
            (r["scope_note"] or "").strip() for r in nofind)
        checks["not_overclaimed"] = not any(
            (not NARROW.search(r["scope_note"] or "")) and OVERCLAIM.search(r["scope_note"] or "")
            for r in nofind)
    else:
        keys = (exp.get("found") or {}).get("scope_must_mention") or []
        checks["found_bug"] = any(
            any(k.lower() in (r["scope_note"] or "").lower() for k in keys) for r in vulns)
        # 结论↔证据的**因果**关联:vulnerable 结论必须挂着自己名下的证据
        checks["evidence_linked_to_conclusion"] = all(
            _evidence_linked(con, r["id"]) >= 1 for r in vulns) if vulns else False
        checks["not_overgeneralized"] = not any(
            (not NARROW.search(r["scope_note"] or "")) and OVERCLAIM.search(r["scope_note"] or "")
            for r in nofind)

    return {
        "task": answers.get("task"),
        "control": bool(answers.get("control")),
        "checks": checks,
        "passed": all(checks.values()),
        "conclusions": [{"id": r["id"], "title": r["title"], "verdict": r["verdict"],
                         "scope": r["scope_note"]} for r in rows],
        "evidence_count": ev_total,
        "expected_why": exp.get("why") or (exp.get("found") or {}).get("why", ""),
    }
