"""no_finding 的强度边界:否证尝试+未复核证据,不能直接下"未发现缺陷"。"""

from __future__ import annotations

import os

import pytest

from fwcore import db as _db
from fwcore.domain import Domain, InvariantViolation


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    hid = dom.add_hypothesis("t1", "输入拼接进命令", body="b", source="[RE]")
    return con, dom, root, hid


def test_no_finding_rejects_unverified_evidence(tmp_path):
    _con, dom, _root, hid = _setup(tmp_path)
    aid = dom.start_attempt(hid, "审计 call sites")
    eid = dom.add_evidence("t1", "log", capture="23 call sites", attempt_id=aid, hypothesis_id=hid)
    dom.finish_attempt(aid, "refuted", "无外部可控参数", evidence=[eid],
                       obs_outcome="none", obs_reason="与预期一致:同一批调用点已逐条核对")
    # 未复核(verified=0)⇒ 不能下 no_finding(工具跑成功 ≠ 审计结论成立)
    with pytest.raises(InvariantViolation, match="经复核"):
        dom.conclude(hid, "no_finding", "范围:authLogin.cgi 的 23 个 qnap_exec 调用点")


def test_no_finding_accepts_verified_evidence(tmp_path):
    _con, dom, root, hid = _setup(tmp_path)
    aid = dom.start_attempt(hid, "审计 call sites")
    os.makedirs(os.path.join(root, "report", "artifacts"), exist_ok=True)
    art = os.path.join(root, "report", "artifacts", "call_sites.txt")
    with open(art, "w", encoding="utf-8") as f:
        f.write("23 call sites\n")
    eid = dom.add_evidence("t1", "log", capture="23 call sites", attempt_id=aid, hypothesis_id=hid,
                           path="report/artifacts/call_sites.txt")
    dom.verify_evidence(eid, method="cross-check", artifact="report/artifacts/call_sites.txt",
                        detail="与恢复 ELF 的反汇编逐条交叉核对(23 处调用点)")
    dom.finish_attempt(aid, "refuted", "无外部可控参数", evidence=[eid],
                       obs_outcome="none", obs_reason="与预期一致:同一批调用点已逐条核对")
    cid = dom.conclude(hid, "no_finding", "范围:authLogin.cgi 的 23 个 qnap_exec 调用点")
    assert cid.startswith("C-")
