"""Gate(权威状态版):从 SQLite 事实算派生量与收尾前置。

与旧 `tools/derived_check.sh` 的关系
- 旧版**解析 Markdown 正则**判断生命周期(会漂、可被文字绕过);
- 新版只读权威状态(目标/面/假设/尝试/证据/结论/镜像/运行),Markdown 仅作人读视图。

迁移期两者并存:旧 gate 仍跑(兼容),新 gate 用于新目标;`fw-core gates` 输出可比对。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .domain import Domain


@dataclass
class GateReport:
    target: str
    version: str | None = None
    surfaces: dict = field(default_factory=dict)
    coverage: str = "0/0"
    hypotheses: dict = field(default_factory=dict)
    attempts: dict = field(default_factory=dict)
    evidence: int = 0
    observations: int = 0
    mirror_due: bool = False
    rounds_since_mirror: int = 0
    material_missing: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    findings: list = field(default_factory=list)
    evidence_version_mismatch: list = field(default_factory=list)
    provenance: dict = field(default_factory=dict)     # direct / weak / unlinked 计数
    advisories: list = field(default_factory=list)      # 不阻断、但报告必须如实标注

    def ok(self) -> bool:
        return not self.blockers

    def as_dict(self) -> dict:
        return {
            "target": self.target,
            "version": self.version,
            "ok": self.ok(),
            "surfaces": self.surfaces,
            "coverage": self.coverage,
            "hypotheses": self.hypotheses,
            "attempts": self.attempts,
            "evidence": self.evidence,
            "observations": self.observations,
            "mirror_due": self.mirror_due,
            "rounds_since_mirror": self.rounds_since_mirror,
            "material_missing": self.material_missing,
            "blockers": self.blockers,
            "findings": self.findings,
            "evidence_version_mismatch": self.evidence_version_mismatch,
            "provenance": self.provenance,
            "advisories": self.advisories,
        }


def evaluate(con, root: str, target: str) -> GateReport:
    """只读权威状态,算一份 gate 报告。"""
    dom = Domain(con, root)
    t = dom.target(target)
    rep = GateReport(target=t.name, version=t.version)

    # ① 攻击面覆盖(A2 的权威版本)
    rep.surfaces = {r["disposition"]: r["n"] for r in con.execute(
        "SELECT disposition, COUNT(*) n FROM surfaces WHERE target_id=? GROUP BY disposition", (t.id,))}
    total = sum(rep.surfaces.values())
    rep.coverage = f"{total - rep.surfaces.get('unjudged', 0)}/{total}"

    # ② 假设 / 尝试 / 证据 / 观察
    rep.hypotheses = {r["state"]: r["n"] for r in con.execute(
        "SELECT state, COUNT(*) n FROM hypotheses WHERE target_id=? GROUP BY state", (t.id,))}
    rep.attempts = {r["result"]: r["n"] for r in con.execute(
        "SELECT a.result, COUNT(*) n FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id"
        " WHERE h.target_id=? GROUP BY a.result", (t.id,))}
    rep.evidence = con.execute("SELECT COUNT(*) n FROM evidence WHERE target_id=?", (t.id,)).fetchone()["n"]
    rep.observations = con.execute("SELECT COUNT(*) n FROM observations WHERE target_id=?", (t.id,)).fetchone()["n"]

    # ③ 三镜(按目标计轮;暂停/恢复不计轮)
    last = con.execute(
        "SELECT created_at FROM mirror_checks WHERE target_id=? ORDER BY created_at DESC LIMIT 1", (t.id,)
    ).fetchone()
    since = last["created_at"] if last else None
    rep.rounds_since_mirror = con.execute(
        "SELECT COUNT(*) n FROM events WHERE kind='round.ended' AND ref=?"
        "   AND (? IS NULL OR ts > ?)", (t.id, since, since)).fetchone()["n"]
    rep.mirror_due = rep.rounds_since_mirror >= 3

    # ④ 材料 + 收尾阻塞(全部来自 domain,不解析文本)
    mat = dom.material_check(target)
    rep.material_missing = list(mat["missing"])
    rep.blockers = dom.closure_blockers(target)
    if rep.mirror_due:
        rep.blockers.append(f"三镜刷新超期({rep.rounds_since_mirror} 轮,下限 3)")

    # ④b 证据强度:版本必须与目标版本一致;findings 必须挂自己名下的证据
    rep.evidence_version_mismatch = [
        {"id": r["id"], "version": r["version"]} for r in con.execute(
            "SELECT id, version FROM evidence WHERE target_id=? AND version IS NOT ? AND version IS NOT NULL",
            (t.id, rep.version))]
    if rep.evidence_version_mismatch:
        rep.blockers.append(f"{len(rep.evidence_version_mismatch)} 条证据的版本与目标版本({rep.version})不一致")
    unlinked = [r["id"] for r in con.execute(
        "SELECT h.id FROM hypotheses h WHERE h.target_id=? AND h.verdict='vulnerable'"
        "   AND (SELECT COUNT(*) FROM evidence e WHERE e.hypothesis_id=h.id)=0", (t.id,))]
    if unlinked:
        rep.blockers.append(f"{len(unlinked)} 条 vulnerable 结论没有挂自己的证据: {unlinked[:3]}")
    # 工具跑成功 ≠ 漏洞成立:证据必须经研究复核(verified=1)才能支撑结论
    unverified = [r["id"] for r in con.execute(
        "SELECT h.id FROM hypotheses h WHERE h.target_id=? AND h.verdict='vulnerable'"
        "   AND (SELECT COUNT(*) FROM evidence e WHERE e.hypothesis_id=h.id AND e.verified=1)=0", (t.id,))]
    integrity_only = [r["id"] for r in con.execute(
        "SELECT h.id FROM hypotheses h WHERE h.target_id=? AND h.verdict='vulnerable'"
        "   AND EXISTS (SELECT 1 FROM evidence e WHERE e.hypothesis_id=h.id AND e.verified=1)"
        "   AND NOT EXISTS (SELECT 1 FROM evidence_checks c JOIN evidence e ON e.id=c.evidence_id"
        "                   WHERE c.method IN ('reproduce-attempt','cross-check') AND e.verified=1"
        "                     AND (e.hypothesis_id=h.id OR e.attempt_id IN"
        "                          (SELECT id FROM attempts WHERE hypothesis_id=h.id)))", (t.id,))]
    if integrity_only:
        rep.blockers.append(f"{len(integrity_only)} 条 vulnerable 结论只有**产物完整性**复核"
                            f"(哈希没变 ≠ 漏洞成立): {integrity_only[:3]}")
    if unverified:
        rep.blockers.append(f"{len(unverified)} 条 vulnerable 结论只有未复核证据(工具输出)"
                            f"⇒ 先复核再收: {unverified[:3]}")

    # ④b 来源强度(报告要能分开呈现:直接产出 vs 命令里提到过)
    prov = {"direct": 0, "weak": 0, "unlinked": 0}
    for r in con.execute("SELECT link_relation FROM evidence WHERE target_id=?", (t.id,)):
        rel = r["link_relation"] or ""
        if rel.startswith("direct"):
            prov["direct"] += 1
        elif rel.startswith("command-write"):
            prov["weak"] += 1
        else:
            prov["unlinked"] += 1
    rep.provenance = prov
    # no_finding 允许完整性复核,但如果**只有**完整性复核,报告要标注(不阻断)
    integrity_nf = [r["id"] for r in con.execute(
        "SELECT h.id FROM hypotheses h WHERE h.target_id=? AND h.verdict='no_finding'"
        "   AND EXISTS (SELECT 1 FROM evidence e WHERE e.hypothesis_id=h.id AND e.verified=1)"
        "   AND NOT EXISTS (SELECT 1 FROM evidence_checks c JOIN evidence e ON e.id=c.evidence_id"
        "                   WHERE c.method IN ('reproduce-attempt','cross-check') AND e.verified=1"
        "                     AND (e.hypothesis_id=h.id OR e.attempt_id IN"
        "                          (SELECT id FROM attempts WHERE hypothesis_id=h.id)))", (t.id,))]
    if integrity_nf:
        rep.advisories.append(
            f"{len(integrity_nf)} 条 no_finding 只有**完整性**复核(recheck-sha256):"
            f"哈希没变 ≠ 阴性结论已独立验证;报告里要如实标注: {integrity_nf[:3]}")

    # ⑤ findings = 判为 vulnerable 且带证据的假设
    for r in con.execute(
        "SELECT h.id, h.title, h.scope_note,"
        "   (SELECT COUNT(*) FROM evidence e WHERE e.hypothesis_id=h.id) ev,"
        "   (SELECT COUNT(*) FROM evidence e WHERE e.hypothesis_id=h.id AND e.verified=1) evv"
        " FROM hypotheses h WHERE h.target_id=? AND h.verdict='vulnerable'", (t.id,)):
        rep.findings.append({"id": r["id"], "title": r["title"],
                             "scope": r["scope_note"], "evidence": r["ev"],
                             "verified": r["evv"], "ok": r["evv"] > 0})
    return rep


def check_files(con, root: str, target: str) -> list[str]:
    """程序可核的"报告材料"问题(与 domain.material_check 一致,单独暴露方便 CLI)。"""
    return Domain(con, root).material_check(target)["missing"]


# ---------------- KB 条数 / 引用完整性(只读文件事实,不解析研究状态) ----------------

def kb_counts(root: str) -> list[dict]:
    """knowledge/INDEX.md 声明的条数 vs 文件实际条数(只看文件事实)。"""
    import re

    idx = os.path.join(root, "knowledge", "INDEX.md")
    if not os.path.isfile(idx):
        return [{"file": "INDEX.md", "status": "missing"}]
    try:
        with open(idx, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        return [{"file": "INDEX.md", "status": "unreadable", "detail": str(e)}]
    out = []
    for name, declared in re.findall(r"^\|\s*`([A-Za-z0-9_.-]+\.md)`\s*\|.*?\|\s*(\d+)\s*\|", text, re.M):
        path = os.path.join(root, "knowledge", name)
        if not os.path.isfile(path):
            out.append({"file": name, "declared": int(declared), "status": "missing"})
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError as e:
            out.append({"file": name, "declared": int(declared), "status": "unreadable", "detail": str(e)})
            continue
        actual = (len(re.findall(r"^- 20\d\d", body, re.M)) if name == "intel-log.md"
                  else len(re.findall(r"(?m)^## ", body)))
        try:
            want = int(declared)
        except (TypeError, ValueError):
            out.append({"file": name, "declared": declared, "status": "bad_declaration"})
            continue
        out.append({"file": name, "declared": want, "actual": actual,
                    "status": "ok" if actual == want else "mismatch"})
    return out


def dangling_attachment_refs(con, root: str, target: str) -> list[str]:
    """报告里引用 attachments/... 但文件不存在(引用完整性;只看文件事实)。"""
    import re

    t = Domain(con, root).target(target)
    rdir = os.path.join(root, "state", t.name, "report")
    bad: list[str] = []
    if not os.path.isdir(rdir):
        return bad
    try:
        names = [n for n in os.listdir(rdir) if n.endswith(".md")]
    except OSError as e:
        return [f"report/ 不可读: {e}"]
    for n in names:
        try:
            with open(os.path.join(rdir, n), encoding="utf-8", errors="replace") as f:
                body = f.read()
        except OSError:
            continue
        for ref in re.findall(r"\(attachments/[^)\s]+\)|attachments/[^\s)\"'>]+", body):
            rel = ref.strip("()")
            if not os.path.exists(os.path.join(rdir, rel)):
                bad.append(f"{n}: {rel}")
    return bad
