"""工具反馈与证据来源:有界输出摘要、给已有证据补来源(不改 E-ID/哈希/复核历史)。"""

from __future__ import annotations

import os

import pytest

from fwcore import db as _db
from fwcore.domain import Domain, InvariantViolation
from fwcore.policy import Authz, Policy
from fwcore.toolreg import run_process
from fwcore.tools import TAIL_CHARS, ToolContext, default_registry


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _ctx(dom, root, rid="R1"):
    return ToolContext(domain=dom, root=root, run_id=rid,
                       artifacts_dir=os.path.join(root, "state", "artifacts", rid),
                       policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))


def test_shell_exec_returns_bounded_tail_without_artifacts(tmp_path):
    con, dom, root = _setup(tmp_path)
    out = default_registry().call(_ctx(dom, root), "shell_exec",
                                  {"command": "seq 1 2000"})
    assert out["status"] == "ok"
    assert out["stdout_tail"] and len(out["stdout_tail"]) <= TAIL_CHARS
    assert out["stdout_truncated"] is True
    assert "2000" in out["stdout_tail"]          # 尾部内容确实可用
    assert out["stdout_sha256"] and "stdout_path" not in out  # Pi 风格:不自动落 artifact


def test_binary_output_is_flagged_not_dumped(tmp_path):
    con, dom, root = _setup(tmp_path)
    out = default_registry().call(_ctx(dom, root), "shell_exec",
                                  {"command": "printf 'a\\000b\\000c'"})
    assert out["status"] == "ok" and out["stdout_binary"] is True
    assert "\x00" not in out["stdout_tail"]


def test_run_process_returns_tail_for_script_tools(tmp_path):
    con, dom, root = _setup(tmp_path)
    out = run_process(_ctx(dom, root), ["bash", "-c", "echo hello-tail"], timeout=10)
    assert out["status"] == "ok" and "hello-tail" in out["stdout_tail"]


def _evidence_with_call(con, dom, root):
    hid = dom.add_hypothesis("t1", "h", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    call = default_registry().call(_ctx(dom, root), "shell_exec",
                                   {"command": "echo produced > produced.txt"})
    path = os.path.join(root, "produced.txt")
    ev = dom.add_evidence("t1", "tool_output", path=path,
                          attempt_id=aid, hypothesis_id=hid)
    return hid, aid, call, ev


def test_link_existing_evidence_keeps_id_hash_and_verification(tmp_path):
    con, dom, root = _setup(tmp_path)
    _hid, _aid, call, ev = _evidence_with_call(con, dom, root)
    before = con.execute("SELECT sha256,path,verified FROM evidence WHERE id=?", (ev,)).fetchone()
    dom.verify_evidence(ev, "recheck-sha256")
    res = dom.link_evidence_source(ev, call["tool_call_id"])
    assert res["relation"] == "command-write" and res["direct"] is False
    row = con.execute("SELECT id,sha256,path,verified,tool_call_id,link_relation"
                      " FROM evidence WHERE id=?", (ev,)).fetchone()
    assert row["id"] == ev and row["sha256"] == before["sha256"]     # 同一 E-ID/哈希
    assert row["verified"] == 1 and row["tool_call_id"] == call["tool_call_id"]
    assert row["link_relation"] == "command-write"
    kinds = [r["kind"] for r in con.execute("SELECT kind FROM events")]
    assert "evidence.linked" in kinds
    n = con.execute("SELECT COUNT(*) n FROM evidence").fetchone()["n"]
    assert n == 1, "补来源不能生成重复证据"


def test_link_rejects_cross_target_and_failed_call(tmp_path):
    con, dom, root = _setup(tmp_path)
    _hid, _aid, call, ev = _evidence_with_call(con, dom, root)
    # 另一个 target 的 run/调用
    dom.create_target("t2", version="1")
    con.execute("INSERT INTO runs(id,target_id,state,step,rounds,pid,host,heartbeat_at,"
                "started_at,updated_at) VALUES('R2',(SELECT id FROM targets WHERE name='t2'),"
                "'running',0,0,1,'h','2026-01-01','2026-01-01','2026-01-01')")
    con.execute("INSERT INTO tool_calls(id,run_id,tool,args,status,created_at)"
                " VALUES('TCX','R2','shell_exec','{}','ok','2026-01-01T00:00:00+00:00')")
    with pytest.raises(InvariantViolation):
        dom.link_evidence_source(ev, "TCX")
    # 失败的调用不能当来源
    con.execute("UPDATE tool_calls SET status='error' WHERE id=?", (call["tool_call_id"],))
    with pytest.raises(InvariantViolation):
        dom.link_evidence_source(ev, call["tool_call_id"])


def test_link_command_write_is_weak(tmp_path):
    con, dom, root = _setup(tmp_path)
    hid = dom.add_hypothesis("t1", "h", body="b", source="[RE]")
    aid = dom.start_attempt(hid, "poc")
    call = default_registry().call(_ctx(dom, root), "shell_exec",
                                   {"command": "echo x > report.txt"})
    path = os.path.join(root, "report.txt")
    ev = dom.add_evidence("t1", "note", path=path, attempt_id=aid, hypothesis_id=hid)
    res = dom.link_evidence_source(ev, call["tool_call_id"])
    assert res["relation"] == "command-write" and res["direct"] is False
