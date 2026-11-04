"""工具执行状态 + 运行时恢复 + 迁移保真的测试。"""

from __future__ import annotations

import hashlib
import json
import os

import pytest

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.migrate import extract_pool_entries, import_notes, verify
from fwcore.runtime import Runner, ScriptedModel
from fwcore.tools import Registry, ToolContext, default_registry


@pytest.fixture()
def setup(tmp_path):
    con = _db.connect(str(tmp_path / "s.sqlite3"))
    dom = Domain(con, str(tmp_path))
    dom.create_target("t1", version="2.0")
    return con, dom, str(tmp_path)


# ---------------- 工具真实状态 ----------------

def test_shell_exec_records_statuses(setup):
    con, dom, root = setup
    reg = default_registry()
    ctx = ToolContext(domain=dom, root=root, artifacts_dir=os.path.join(root, "art"))

    ok = reg.call(ctx, "shell.exec", {"command": "true"})
    assert ok["status"] == "ok" and ok["exit_code"] == 0
    err = reg.call(ctx, "shell.exec", {"command": "false"})
    assert err["status"] == "error" and err["exit_code"] == 1
    tmo = reg.call(ctx, "shell.exec", {"command": "sleep 5", "timeout": 0.3})
    assert tmo["status"] == "timeout"
    # 三种状态都落库,且带 sha256/落盘路径
    rows = con.execute("SELECT status FROM tool_calls ORDER BY created_at").fetchall()
    assert {r["status"] for r in rows} == {"ok", "error", "timeout"}
    assert os.path.isfile(ok["stdout_path"])


def test_high_risk_tool_requires_confirmation(setup):
    con, dom, root = setup
    ctx = ToolContext(domain=dom, root=root, artifacts_dir=os.path.join(root, "art"),
                      confirm=lambda _s: False)
    out = default_registry().call(ctx, "shell.exec", {"command": "echo hi"})
    assert out["status"] == "cancelled"


def test_unknown_tool(setup):
    con, dom, root = setup
    out = default_registry().call(ToolContext(domain=dom, root=root), "nope", {})
    assert out["status"] == "error"


# ---------------- 运行时:检查点与恢复 ----------------

def _runner(setup, script, steps=10):
    con, dom, root = setup
    return con, dom, root, Runner(con, root, ScriptedModel(script=script), max_steps=steps)


def test_run_executes_tool_calls_and_finishes(setup):
    script = [
        {"content": "登记假设", "tool_calls": [{"name": "state.add_hypothesis",
                                          "args": {"target": "t1", "title": "解析器越界"}}]},
        {"content": "FINAL: 完成", "tool_calls": []},
    ]
    con, dom, root, r = _runner(setup, script)
    rid = r.start("t1", goal="smoke")
    res = r.run(rid, "t1", goal="smoke")
    assert res["state"] == "finished" and res["final"].startswith("FINAL:")
    assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1
    assert con.execute("SELECT COUNT(*) n FROM checkpoints WHERE run_id=?", (rid,)).fetchone()["n"] >= 2


class Boom(Exception):
    pass


class ExplodingModel:
    def __init__(self):
        self.n = 0

    def chat(self, messages, tools):
        self.n += 1
        if self.n == 1:
            return {"content": "先看看状态", "tool_calls": []}
        raise Boom("模拟进程中断")


def test_interruption_pauses_and_keeps_hypothesis_state(setup):
    """进程中断 = 运行事件,不是研究结论;假设状态不得被改动。"""
    con, dom, root = setup
    hid = dom.add_hypothesis("t1", "授权缺陷")
    r = Runner(con, root, ExplodingModel(), max_steps=10)
    rid = r.start("t1")
    with pytest.raises(Boom):
        r.run(rid, "t1")
    run = con.execute("SELECT state,step FROM runs WHERE id=?", (rid,)).fetchone()
    assert run["state"] == "paused"
    assert con.execute("SELECT state FROM hypotheses WHERE id=?", (hid,)).fetchone()["state"] == "open"
    assert con.execute("SELECT COUNT(*) n FROM checkpoints WHERE run_id=?", (rid,)).fetchone()["n"] >= 1


def test_resume_continues_from_checkpoint(setup):
    con, dom, root, r = _runner(setup, [
        {"content": "一步", "tool_calls": []},
        {"content": "FINAL: done", "tool_calls": []},
    ], steps=10)
    rid = r.start("t1")
    r.run(rid, "t1")
    step_before = con.execute("SELECT step FROM runs WHERE id=?", (rid,)).fetchone()["step"]
    res = r.resume(rid)
    assert res["state"] == "finished"
    assert con.execute("SELECT step FROM runs WHERE id=?", (rid,)).fetchone()["step"] >= step_before


def test_step_limit_pauses_not_concludes(setup):
    con, dom, root, r = _runner(setup, [{"content": "循环", "tool_calls": []}] * 5, steps=2)
    rid = r.start("t1")
    res = r.run(rid, "t1")
    assert res["state"] == "paused" and res["steps"] == 2


# ---------------- 迁移:不丢信息、幂等 ----------------

NOTES = """# t1

## 假设池(唯一队列)

- **H-01** 未认证可读配置 | 状态: **未处理**
- **H-02** 命令注入 | 状态: **定论:No finding**(判据: 逐条读过)

## §1 轮

本轮目标: H-01
"""


def test_migrate_is_lossless_and_idempotent(setup, tmp_path):
    con, dom, root = setup
    p = tmp_path / "state" / "t1" / "notes.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(NOTES, encoding="utf-8")

    r1 = import_notes(con, root, "t1", str(p))
    v1 = verify(con, root, "t1", str(p))
    assert r1["pool_entries"] == 2 and r1["hypotheses_created"] == 2
    assert v1["byte_identical"] and v1["ok"]

    r2 = import_notes(con, root, "t1", str(p))       # 幂等
    assert r2["documents_added"] == 0 and r2["hypotheses_created"] == 0
    assert con.execute("SELECT COUNT(*) n FROM documents").fetchone()["n"] == 1


def test_migration_does_not_infer_completion(setup, tmp_path):
    """历史条目里的"定论"字样不得被自动当成已完成(那是旧设计的问题)。"""
    con, dom, root = setup
    p = tmp_path / "state" / "t1" / "notes.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(NOTES, encoding="utf-8")
    import_notes(con, root, "t1", str(p))
    rows = con.execute("SELECT title,state,source FROM hypotheses").fetchall()
    assert all(r["state"] == "open" for r in rows)
    assert all("needs_review" in r["source"] for r in rows)


def test_pool_entry_extraction_prefers_unique_queue():
    text = "## 假设池(唯一队列)\n- **H-01** a\n\n## 假设池(旧)\n- **H-09** b\n"
    ent = extract_pool_entries(text)
    assert [e[0] for e in ent] == ["H-01"]
