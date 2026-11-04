"""领域不变量测试 —— 每条都对应旧设计里"可能被文字绕过"的地方。"""

from __future__ import annotations

import os

import pytest

from fwcore import db as _db
from fwcore.domain import Domain, InvariantViolation, NotFound


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
    dom.add_evidence("t1", "log", capture="拒绝了所有元字符", attempt_id=aid)
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
    dom.add_evidence("t1", "http", capture="200 + 管理内容", attempt_id=aid, hypothesis_id=hid)
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
    dom.set_surface_disposition("t1", "12080 /x", "no_finding")
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
