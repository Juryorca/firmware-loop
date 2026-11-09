"""证据溯源:来源/哈希/工具状态/目标版本/关联实验 + **工具输出不得自动升级为漏洞证据**。"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys

import pytest

from fwcore import db as _db
from fwcore.domain import Domain, InvariantViolation, NotFound
from fwcore.policy import Authz, Policy
from fwcore.tools import ToolContext, default_registry


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="5.2.10")
    return con, dom, root


def _ctx(dom, root):
    return ToolContext(domain=dom, root=root, policy=Policy(authorization=Authz(roots=[root]),
                                                          allow_high_risk=True))


def _ok_tool_call(con, dom, root, path):
    """跑一次**成功**的工具调用(取它的 id 当证据来源),产物自己写(不依赖 shell 重定向)。"""
    ctx = _ctx(dom, root)
    out = default_registry().call(ctx, "shell_exec", {"command": "echo hello", "timeout": 10})
    assert out["status"] == "ok", out
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("hello\n")
    return out["tool_call_id"]


def test_tool_call_evidence_carries_source_hash_version_and_is_unverified(tmp_path):
    con, dom, root = _setup(tmp_path)
    art = os.path.join(root, "state", "artifacts", "a.txt")
    os.makedirs(os.path.dirname(art), exist_ok=True)
    tc = _ok_tool_call(con, dom, root, art)

    ev = dom.add_evidence("t1", "tool_output", path=art, tool_call_id=tc)
    row = con.execute("SELECT * FROM evidence WHERE id=?", (ev,)).fetchone()
    assert row["source"] and row["source"].startswith("tool:")
    assert row["tool_call_id"] == tc
    assert row["verified"] == 0, "工具输出默认未复核"
    assert row["version"] == "5.2.10"
    with open(art, "rb") as f:
        assert row["sha256"] == hashlib.sha256(f.read()).hexdigest()


def test_failed_tool_call_cannot_be_evidence_source(tmp_path):
    _con, dom, root = _setup(tmp_path)
    out = default_registry().call(_ctx(dom, root), "shell_exec", {"command": "exit 3", "timeout": 10})
    assert out["status"] != "ok"
    with pytest.raises(InvariantViolation):
        dom.add_evidence("t1", "tool_output", capture="x", tool_call_id=out["tool_call_id"])


def test_vulnerable_requires_verified_evidence(tmp_path):
    con, dom, root = _setup(tmp_path)
    art = os.path.join(root, "state", "artifacts", "b.txt")
    os.makedirs(os.path.dirname(art), exist_ok=True)
    tc = _ok_tool_call(con, dom, root, art)

    h = dom.add_hypothesis("t1", "崩溃可致 RCE", body="偏差:输入 a 未做长度检查",
                           source="[RE] 反编译看到 strcpy")
    aid = dom.start_attempt(h, "fuzz", {"payload": "A" * 300})
    ev = dom.add_evidence("t1", "tool_output", path=art, tool_call_id=tc,
                          attempt_id=aid, hypothesis_id=h)
    dom.finish_attempt(aid, "supported", "复现成功", evidence=[ev])

    with pytest.raises(InvariantViolation) as ei:
        dom.conclude(h, "vulnerable", "只影响 /cgi-bin/x 的参数 a")
    assert "复核" in str(ei.value)

    with pytest.raises(InvariantViolation):
        dom.promote_evidence(ev, "短")          # 复核说明太短 ⇒ 拒绝

    dom.promote_evidence(ev, "按 PoC 复现 3 次均崩溃,返回地址可控")
    assert con.execute("SELECT verified FROM evidence WHERE id=?", (ev,)).fetchone()["verified"] == 1
    cid = dom.conclude(h, "vulnerable", "只影响 /cgi-bin/x 的参数 a")
    assert cid
    kinds = [r["kind"] for r in con.execute("SELECT kind FROM events")]
    assert "evidence.verified" in kinds


def test_manual_evidence_is_unverified_until_checked(tmp_path):
    """手工写的证据也**不算**已验证:复核是独立动作,要留下可检查的记录。"""
    con, dom, root = _setup(tmp_path)
    ev = dom.add_evidence("t1", "decompiled", capture="switch(*(a+4)) 无边界检查",
                          source="idb:decompiled:/cgi-bin/x")
    row = con.execute("SELECT source, verified FROM evidence WHERE id=?", (ev,)).fetchone()
    assert row["source"].startswith("idb:")
    assert row["verified"] == 0
    from tests.helpers import verify
    verify(dom, ev, root)                       # 交叉验证(要可检查的产物)
    assert con.execute("SELECT verified FROM evidence WHERE id=?", (ev,)).fetchone()["verified"] == 1


def test_evidence_missing_artifact_is_rejected(tmp_path):
    _con, dom, _root = _setup(tmp_path)
    with pytest.raises(NotFound):
        dom.add_evidence("t1", "tool_output", path="state/artifacts/nope.bin")


def test_old_schema_is_rejected_with_actionable_error(tmp_path):
    """开发期不做逐版本升级:旧库必须报错并指向一次性 rebaseline 脚本。"""
    path = os.path.join(str(tmp_path), "old.sqlite3")
    con = sqlite3.connect(path, isolation_level=None)
    con.executescript(_db.SCHEMA)
    con.execute("INSERT INTO meta(key,value) VALUES('schema_version','3')")
    con.close()
    with pytest.raises(RuntimeError) as e:
        _db.connect(path)
    assert "db_rebaseline" in str(e.value)


def test_rebaseline_tool_preserves_rows(tmp_path):
    """一次性迁移:先备份、补基线列、行数不变、版本到位。"""
    path = os.path.join(str(tmp_path), "old.sqlite3")
    con = sqlite3.connect(path, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(_db.SCHEMA)
    con.execute("INSERT INTO meta(key,value) VALUES('schema_version','3')")
    con.execute("INSERT INTO targets(id,name,created_at) VALUES('T1','t','2026-01-01')")
    con.execute("INSERT INTO evidence(id,target_id,kind,capture,created_at)"
                " VALUES('E1','T1','log','old fact','2026-01-01')")
    con.close()

    r = subprocess.run([sys.executable, "tools/db_rebaseline.py", path],
                       capture_output=True, text=True, timeout=120, check=False)
    assert r.returncode == 0, (r.stdout, r.stderr)
    assert "备份" in r.stdout or "backup" in r.stdout

    con = _db.connect(path)          # 版本已到基线,能正常打开
    row = con.execute("SELECT capture, tool_call_id FROM evidence WHERE id='E1'").fetchone()
    assert row["capture"] == "old fact"
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"] \
        == str(_db.SCHEMA_VERSION)
    assert any(f.startswith("old.sqlite3.bak-") for f in os.listdir(str(tmp_path)))

def _text_registry():
    from fwcore.tools import Registry, ToolSpec

    reg = Registry()

    def handler(ctx, args):
        return {"status": "ok", "text": "decompile: strcpy(dst, src)",
                "observation": {"status": "ok", "kind": "decompiled", "lines": 1},
                "meta": {"provider": "idb", "capability": "ida.decompile"}}

    reg.register(ToolSpec(name="fake_decompile", description="假反编译",
                          input_schema={"type": "object", "properties": {}}, handler=handler,
                          risk="low", external=False))
    return reg


def test_text_tool_result_stays_in_result_not_artifact(tmp_path):
    con, dom, root = _setup(tmp_path)
    out = _text_registry().call(_ctx(dom, root), "fake_decompile", {})
    assert out["status"] == "ok" and "strcpy" in out["text"]
    row = con.execute("SELECT result_path, result_sha256, observation FROM tool_calls WHERE id=?",
                      (out["tool_call_id"],)).fetchone()
    assert row["result_path"] is None and row["result_sha256"] is None
    assert json.loads(row["observation"])["kind"] == "decompiled"


def test_text_tool_evidence_requires_explicit_capture(tmp_path):
    con, dom, root = _setup(tmp_path)
    out = _text_registry().call(_ctx(dom, root), "fake_decompile", {})
    with pytest.raises(InvariantViolation):
        dom.add_evidence("t1", "tool_output", tool_call_id=out["tool_call_id"])
    ev = dom.add_evidence("t1", "tool_output", tool_call_id=out["tool_call_id"],
                          capture=out["text"])
    row = con.execute("SELECT path, sha256, capture, verified, source FROM evidence WHERE id=?",
                      (ev,)).fetchone()
    assert row["path"] is None and row["sha256"] is None and "strcpy" in row["capture"]
    assert row["verified"] == 0
    assert row["source"] in ("tool:idb", "tool:ida.decompile"), row["source"]
