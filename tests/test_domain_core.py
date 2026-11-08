"""领域不变量测试 —— 每条都对应旧设计里"可能被文字绕过"的地方。"""

from __future__ import annotations

import os

import pytest

from fwcore import db as _db
from fwcore.domain import Domain, InvariantViolation


@pytest.fixture()
def dom(tmp_path):
    con = _db.connect(str(tmp_path / "t.sqlite3"))
    d = Domain(con, str(tmp_path))
    d.create_target("t1", version="1.0")
    return d


def test_unknown_vs_empty(dom):
    assert dom.status("nope")["state"] == "unknown"
    assert dom.status("t1")["state"] == "empty"
    dom.add_hypothesis("t1", "h")
    assert dom.status("t1")["state"] == "active"


def test_non_decisive_attempts_cannot_conclude(dom):
    """工具/环境失败不是研究结论。"""
    hid = dom.add_hypothesis("t1", "解析器越界")
    for res in ("environment_failure", "no_signal", "cancelled"):
        aid = dom.start_attempt(hid, "fuzz")
        dom.finish_attempt(aid, res, "qemu 起不来")
    with pytest.raises(InvariantViolation) as e:
        dom.conclude(hid, "no_finding", "仅 /api/parse 这一个入口的解析器")
    assert "环境失败" in str(e.value) or "判定力" in str(e.value)


def test_refuted_requires_evidence(dom):
    hid = dom.add_hypothesis("t1", "命令注入")
    aid = dom.start_attempt(hid, "payload")
    with pytest.raises(InvariantViolation):
        dom.finish_attempt(aid, "refuted", "打不进去")   # 没有证据 ⇒ 拒
    import os as _os

    art = _os.path.join(dom.root, "report", "artifacts", "metachars.txt")
    _os.makedirs(_os.path.dirname(art), exist_ok=True)
    with open(art, "w", encoding="utf-8") as f:
        f.write("所有元字符被拒绝\n")
    eid = dom.add_evidence("t1", "log", path="report/artifacts/metachars.txt",
                           attempt_id=aid)
    dom.verify_evidence(eid, method="recheck-sha256")      # no_finding 要求 ≥1 条复核
    dom.finish_attempt(aid, "refuted", "打不进去")
    assert dom.conclude(hid, "no_finding", "仅该入口的参数 a") is not None


def test_conclude_requires_scope_and_evidence(dom):
    hid = dom.add_hypothesis("t1", "越权")
    aid = dom.start_attempt(hid, "diff")
    dom.finish_attempt(aid, "supported", "行为不同")
    with pytest.raises(InvariantViolation):
        dom.conclude(hid, "vulnerable", "  ")          # 没有 scope
    with pytest.raises(InvariantViolation):
        dom.conclude(hid, "vulnerable", "仅 /api/x 未认证")   # 没有证据
    ev = dom.add_evidence("t1", "http", capture="200 + 管理内容", attempt_id=aid, hypothesis_id=hid)
    # 新语义:手工证据也**不算**已验证;只有留下可检查的复核记录才作数
    with pytest.raises(InvariantViolation) as e2:
        dom.conclude(hid, "vulnerable", "仅 /api/x 未认证")
    assert "复核" in str(e2.value)
    with pytest.raises(InvariantViolation):
        dom.promote_evidence(ev, "我确认过了,确实未认证")     # 只写文字 ⇒ 不给
    from tests.helpers import verify
    verify(dom, ev, str(dom.root))
    assert dom.conclude(hid, "vulnerable", "仅 /api/x 未认证")


def test_running_attempt_blocks_conclude_and_closure(dom):
    hid = dom.add_hypothesis("t1", "race")
    dom.start_attempt(hid, "parallel")
    with pytest.raises(InvariantViolation):
        dom.conclude(hid, "inconclusive", "跑不动")
    blockers = dom.closure_blockers("t1")
    assert any("running" in b for b in blockers)
    assert any("未收口" in b for b in blockers)


def test_closure_blockers_include_unjudged_surface(dom):
    dom.add_surface("t1", "12080 /x", disposition="unjudged")
    assert any("未判定" in b for b in dom.closure_blockers("t1"))
    dom.set_surface_disposition("t1", "12080 /x", "no_finding", note="仅 /x 一个入口,已逐条读过鉴权门")
    assert not any("未判定" in b for b in dom.closure_blockers("t1"))


def test_evidence_version_binding(dom):
    eid = dom.add_evidence("t1", "note", capture="x")   # 未显式给 version ⇒ 取目标版本
    row = dom.con.execute("SELECT version FROM evidence WHERE id=?", (eid,)).fetchone()
    assert row["version"] == "1.0"


def test_material_check_flags_symlink_and_missing_readme(dom, tmp_path):
    pkg = tmp_path / "state" / "t1" / "report" / "attachments" / "p1"
    pkg.mkdir(parents=True)
    (tmp_path / "state" / "t1" / "report" / "a.md").write_text(
        "## Summary\n## Root Cause\n## Steps to Reproduce\n## Evidence\n", encoding="utf-8")
    os.symlink("../../../rootfs", pkg / "firmware-rootfs")
    res = dom.material_check("t1")
    assert any("符号链接" in m for m in res["missing"])
    assert any("README" in m for m in res["missing"])


def test_dropped_hypothesis_needs_reason(dom):
    hid = dom.add_hypothesis("t1", "低价值")
    with pytest.raises(InvariantViolation):
        dom.drop_hypothesis(hid, "   ")
    dom.drop_hypothesis(hid, "同类已报,证据不足")
    assert dom.hypothesis(hid)["state"] == "dropped"


def test_writes_are_locked_under_concurrency(tmp_path):
    """两个写者:一个持有写事务时,另一个必须明确失败(不静默覆盖)。"""
    path = str(tmp_path / "c.sqlite3")
    con1 = _db.connect(path)
    con2 = _db.connect(path, timeout=0.2)
    d1 = Domain(con1, str(tmp_path))
    d1.create_target("t1")
    with _db.Tx(con1):
        d2 = Domain(con2, str(tmp_path))
        with pytest.raises(_db.LockedError):
            d2.add_hypothesis("t1", "并发写")


def test_inconclusive_requires_next_probe_note(dom):
    hid = dom.add_hypothesis("t1", "证书校验")
    aid = dom.start_attempt(hid, "diff")
    with pytest.raises(InvariantViolation):
        dom.finish_attempt(aid, "inconclusive", "")
    dom.finish_attempt(aid, "inconclusive", "已排除路径 A;需在真机抓到 B 的调用栈")


# ---------- 审计反例:判定一致性 / 证据归属 / 面处置 ----------

def test_no_finding_cannot_be_supported_by_inconclusive(dom):
    """inconclusive/supported 不能支撑 no_finding。"""
    hid = dom.add_hypothesis("t1", "越权")
    aid = dom.start_attempt(hid, "diff")
    dom.finish_attempt(aid, "inconclusive", "只跑了外层路由")
    with pytest.raises(InvariantViolation) as e:
        dom.conclude(hid, "no_finding", "仅 /api/x")
    assert "refuted" in str(e.value)


def test_vulnerable_requires_supported_attempt(dom):
    hid = dom.add_hypothesis("t1", "越权")
    aid = dom.start_attempt(hid, "diff")
    dom.add_evidence("t1", "http", capture="200", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "inconclusive", "行为不同但未确认可利用")
    with pytest.raises(InvariantViolation):
        dom.conclude(hid, "vulnerable", "仅 /api/x")


def test_evidence_cannot_cross_targets(dom):
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "m")
    dom.create_target("t2", version="9")
    with pytest.raises(InvariantViolation):
        dom.add_evidence("t2", "log", capture="x", attempt_id=aid)
    with pytest.raises(InvariantViolation):
        dom.add_evidence("t2", "log", capture="x", hypothesis_id=hid)


def test_surface_vulnerable_requires_evidence(dom):
    dom.add_surface("t1", "12080 /a")
    with pytest.raises(InvariantViolation):
        dom.set_surface_disposition("t1", "12080 /a", "vulnerable")
    ev = dom.add_evidence("t1", "http", capture="200 + admin body")
    dom.set_surface_disposition("t1", "12080 /a", "vulnerable", evidence_id=ev)
    row = dom.con.execute("SELECT disposition,judged_by FROM surfaces WHERE key='12080 /a'").fetchone()
    assert row["disposition"] == "vulnerable" and row["judged_by"] == ev


def test_surface_no_finding_requires_reason(dom):
    dom.add_surface("t1", "12080 /b")
    with pytest.raises(InvariantViolation):
        dom.set_surface_disposition("t1", "12080 /b", "no_finding", note="x")
    dom.set_surface_disposition("t1", "12080 /b", "no_finding", note="该入口只有静态资源,无参数")


def test_empty_research_state_blocks_closure(dom):
    """0 假设 / 0 面 不能算干净(空状态 ≠ 完成)。"""
    b = dom.closure_blockers("t1")
    assert any("没有任何假设" in x for x in b)
    assert any("攻击面" in x for x in b)


def test_observation_cannot_use_cross_target_attempt(dom):
    dom.create_target("t2", version="9")
    hid = dom.add_hypothesis("t1", "h")
    aid = dom.start_attempt(hid, "m")
    with pytest.raises(InvariantViolation):
        dom.add_observation("t2", "observation", attempt_id=aid)


def test_evidence_tool_call_must_belong_to_same_target(dom):
    dom.create_target("t2", version="9")
    con = dom.con
    t2 = dom.target("t2")
    con.execute("INSERT INTO runs(id,target_id,state,started_at,updated_at) VALUES(?,?,?,?,?)",
                ("R2", t2.id, "running", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"))
    con.execute("INSERT INTO tool_calls(id,run_id,tool,status,created_at) VALUES(?,?,?,?,?)",
                ("TC-2", "R2", "shell_exec", "ok", "2026-01-01T00:00:00Z"))
    with pytest.raises(InvariantViolation):
        dom.add_evidence("t1", "log", capture="x", tool_call_id="TC-2")


def test_evidence_attempt_and_hypothesis_must_match(dom):
    h1 = dom.add_hypothesis("t1", "h1")
    a1 = dom.start_attempt(h1, "m")
    h2 = dom.add_hypothesis("t1", "h2")
    with pytest.raises(InvariantViolation):
        dom.add_evidence("t1", "log", capture="x", attempt_id=a1, hypothesis_id=h2)
