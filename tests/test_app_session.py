"""AgentSession 的应用层行为:提交 / 取消 / 恢复 / 快照 / 转录 / print mode。

全部用 scripted 适配器:不产生任何外部模型调用。
"""

from __future__ import annotations

import time

from fwapp.args import AppOptions
from fwapp.config import resolve
from fwapp.modes.print import run_print
from fwapp.runtime import AppRuntime
from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.models import Reply, ScriptedAdapter


def make_ws(tmp_path, name="t1"):
    root = tmp_path / "ws"
    root.mkdir()
    con = _db.connect(_db.default_db_path(str(root)))
    Domain(con, str(root)).create_target(name)
    con.close()
    cfg = resolve(AppOptions(root=str(root), model="scripted", mcp=False, watchdog=False))
    return root, cfg


def wait_turn(sess, timeout=10.0):
    events = []
    t0 = time.time()
    while time.time() - t0 < timeout:
        events.extend(sess.drain_events())
        if any(e.kind in ("turn_done", "error") for e in events):
            return events
        time.sleep(0.05)
    raise AssertionError(f"turn 未结束;busy={sess.busy} pending={sess.pending} events={events}")


def test_submit_turn_uses_runner_and_records_tool(tmp_path):
    root, cfg = make_ws(tmp_path)
    script = [
        {"content": "先登记假设。", "tool_calls": [{
            "id": "c1", "name": "state_add_hypothesis",
            "args": {"target": "t1", "title": "解析器边界"}}]},
        {"content": "FINAL: 完成", "tool_calls": []},
    ]
    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: ScriptedAdapter(script=script),
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.submit("开始研究")
        events = wait_turn(sess)
        assert any(e.kind == "turn_done" for e in events), events
        done = [e for e in events if e.kind == "turn_done"][-1]
        assert done.data["state"] == "finished"
        con = sess.services.con
        assert con.execute("SELECT COUNT(*) n FROM hypotheses").fetchone()["n"] == 1
        tools = [t for t in sess.tool_updates() if t["tool"] == "state_add_hypothesis"]
        assert tools and tools[-1]["status"] == "ok"
        items = sess.transcript()
        assert any(i.kind == "assistant" and "FINAL: 完成" in i.text for i in items)
        snap = sess.snapshot()
        assert snap.state == "finished" and snap.calls >= 2 and snap.tools >= 1
    finally:
        rt.dispose()


def test_cancel_pauses_run_at_safe_point(tmp_path):
    root, cfg = make_ws(tmp_path)

    class Slow:
        name = "scripted"

        def chat(self, messages, tools):
            time.sleep(0.6)
            return Reply(content="FINAL: 迟到的回答", tool_calls=[])

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Slow(),
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.submit("开始")
        t0 = time.time()
        while not sess.busy and time.time() - t0 < 3:
            time.sleep(0.02)
        assert sess.cancel("测试取消")
        events = wait_turn(sess)
        assert any(e.kind == "cancelled" for e in events), events
        row = sess.services.con.execute(
            "SELECT state,handback_reason FROM runs WHERE id=?", (sess.run_id,)).fetchone()
        assert row["state"] == "paused"
        assert (row["handback_reason"] or "").startswith("user_stop")
    finally:
        rt.dispose()


def test_resume_paused_run_continues_same_run(tmp_path):
    root, cfg = make_ws(tmp_path)
    first = [{"content": "FINAL: 第一轮", "tool_calls": []}]
    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: ScriptedAdapter(script=first),
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.submit("开始")
        wait_turn(sess)
        rid = sess.run_id
        assert sess.services.con.execute(
            "SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "finished"
    finally:
        rt.dispose()

    # 新进程(新 Runtime)从检查点恢复,仍是同一个 run
    rt2 = AppRuntime(cfg, target="t1",
                     adapter_factory=lambda name: ScriptedAdapter(
                         script=[{"content": "FINAL: 第二轮", "tool_calls": []}]),
                     mcp=False, watchdog=False)
    try:
        sess2 = rt2.session
        assert sess2.run_id == rid
        sess2.submit("继续")
        events = wait_turn(sess2)
        assert sess2.run_id == rid
        assert any(e.kind == "turn_done" for e in events)
        assert sess2.services.con.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"] == 1
    finally:
        rt2.dispose()


def test_new_session_creates_another_run(tmp_path):
    root, cfg = make_ws(tmp_path)
    script = [{"content": "FINAL: 完成", "tool_calls": []}] * 4
    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: ScriptedAdapter(script=script),
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.submit("第一轮")
        wait_turn(sess)
        first = sess.run_id
        sess.new_session()
        assert sess.run_id is None
        sess.submit("新一轮")
        wait_turn(sess)
        assert sess.run_id and sess.run_id != first
        assert sess.services.con.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"] == 2
    finally:
        rt.dispose()


def test_submit_while_busy_is_queued_and_steers_next_turn(tmp_path):
    root, cfg = make_ws(tmp_path)

    class Slow:
        name = "scripted"

        def __init__(self):
            self.n = 0

        def chat(self, messages, tools):
            self.n += 1
            time.sleep(0.3)
            return Reply(content=f"FINAL: 第{self.n}轮", tool_calls=[])

    adapter = Slow()
    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: adapter,
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.submit("第一问")
        t0 = time.time()
        while not sess.busy and time.time() - t0 < 3:
            time.sleep(0.02)
        sess.submit("第二问")            # 运行中 ⇒ 排队
        assert sess.pending == ["第二问"]
        t0 = time.time()
        while time.time() - t0 < 10:
            if adapter.n >= 2 and not sess.busy and not sess.pending:
                break
            time.sleep(0.05)
        assert adapter.n == 2
        assert sess.pending == []
        assert sess.services.con.execute("SELECT COUNT(*) n FROM runs").fetchone()["n"] == 1
    finally:
        rt.dispose()


def test_print_mode_prints_final_and_uses_session(tmp_path, capsys):
    root, cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1",
                    adapter_factory=lambda name: ScriptedAdapter(
                        script=[{"content": "FINAL: 打印结果", "tool_calls": []}]),
                    mcp=False, watchdog=False)
    try:
        rc = run_print(rt, "执行")
    finally:
        rt.dispose()
    out = capsys.readouterr()
    assert rc == 0
    assert "FINAL: 打印结果" in out.out
