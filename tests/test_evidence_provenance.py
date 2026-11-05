"""证据溯源:来源/哈希/工具状态/目标版本/关联实验 + **工具输出不得自动升级为漏洞证据**。"""

from __future__ import annotations

import hashlib
import os
import sqlite3

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
    con, dom, root = _setup(tmp_path)
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


def test_manual_evidence_is_verified_but_records_source(tmp_path):
    con, dom, _root = _setup(tmp_path)
    ev = dom.add_evidence("t1", "decompiled", capture="switch(*(a+4)) 无边界检查",
                          source="idb:decompiled:/cgi-bin/x")
    row = con.execute("SELECT source, verified FROM evidence WHERE id=?", (ev,)).fetchone()
    assert row["source"].startswith("idb:")
    assert row["verified"] == 1


def test_evidence_missing_artifact_is_rejected(tmp_path):
    con, dom, _root = _setup(tmp_path)
    with pytest.raises(NotFound):
        dom.add_evidence("t1", "tool_output", path="state/artifacts/nope.bin")


def test_migration_v3_to_v4_preserves_existing_rows(tmp_path):
    """v3 库(无新列)升级后:旧证据仍可用且默认视为已复核(不能凭空降级历史事实)。"""
    path = os.path.join(str(tmp_path), "old.sqlite3")
    con = sqlite3.connect(path, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.executescript(_db.SCHEMA)
    con.execute("INSERT INTO meta(key,value) VALUES('schema_version','3')")
    con.execute("INSERT INTO targets(id,name,created_at) VALUES('T1','t','2026-01-01')")
    con.execute("INSERT INTO evidence(id,target_id,kind,capture,created_at) VALUES"
                "('E1','T1','decompiled','old fact','2026-01-01')")
    con.close()

    con = _db.connect(path)
    cols = {r[1] for r in con.execute("PRAGMA table_info(evidence)")}
    assert {"source", "tool_call_id", "verified", "notes"} <= cols
    row = con.execute("SELECT * FROM evidence WHERE id='E1'").fetchone()
    assert row["capture"] == "old fact" and row["verified"] == 1
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()["value"] == "4"
