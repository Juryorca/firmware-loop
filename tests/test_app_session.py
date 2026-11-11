"""AgentSession 的应用层行为:提交 / 取消 / 恢复 / 快照 / 转录 / print mode。

全部用 scripted 适配器:不产生任何外部模型调用。
"""

from __future__ import annotations

import json
import threading
import time

from fwapp.args import AppOptions
from fwapp.config import resolve
from fwapp.modes.print import run_print
from fwapp.runtime import AppRuntime
from fwapp.session import _is_continue_command
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


def test_continue_command_accepts_prefixed_text():
    for text in ("继续", "继续 先做静态检查", "继续推进，顺便看 X", "continue 先看日志"):
        assert _is_continue_command(text), text
    for text in ("那就把ida关一下？", "不要继续", "", "  "):
        assert not _is_continue_command(text), text


def test_set_model_updates_model_and_context(tmp_path, monkeypatch):
    _root, cfg = make_ws(tmp_path)
    cfg.api_key = "key"
    cfg.base_url = "https://example.test/v1"
    cfg.model = "old"
    cfg.limits.context_tokens = 1_000
    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: ScriptedAdapter(script=[]),
                    mcp=False, watchdog=False)
    try:
        from fwcore.models import ModelInfo
        monkeypatch.setattr("fwapp.config.adapter_for", lambda cfg, name: object())
        info = ModelInfo(id="new", label="New", api_id="new", context_window=1_048_576)
        assert rt.session.set_model("new", info=info)
        assert rt.session.model_name == "new"
        assert cfg.model == "new"
        assert cfg.limits.context_tokens == 1_048_576
    finally:
        rt.dispose()


def test_submit_turn_uses_runner_and_records_tool(tmp_path):
    _root, cfg = make_ws(tmp_path)
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
    _root, cfg = make_ws(tmp_path)

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
        assert (row["handback_reason"] or "").startswith("user_stop"), dict(row)
    finally:
        rt.dispose()


def test_resume_paused_run_continues_same_run(tmp_path):
    _root, cfg = make_ws(tmp_path)
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
    _root, cfg = make_ws(tmp_path)
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
    _root, cfg = make_ws(tmp_path)

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
    _root, cfg = make_ws(tmp_path)
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


def test_event_queue_concurrent_drain_keeps_terminal_event(tmp_path):
    _root, cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    try:
        sess = rt.session
        seen = []
        start = threading.Barrier(2)
        done = threading.Event()

        def produce():
            start.wait()
            for i in range(1200):
                sess.emit("stream", f"chunk-{i}")
            sess.emit("turn_done", "done")
            done.set()

        def consume():
            start.wait()
            while not done.is_set():
                seen.extend(sess.drain_events())
            seen.extend(sess.drain_events())

        p = threading.Thread(target=produce)
        c = threading.Thread(target=consume)
        p.start()
        c.start()
        p.join(5)
        c.join(5)
        assert len(seen) >= 1201, len(seen)
        assert [e for e in seen if e.kind == "turn_done"], "终止事件不能丢"
    finally:
        rt.dispose()


def test_submit_binds_target_before_switch(tmp_path):
    _root, cfg = make_ws(tmp_path)
    from fwapp.services import build_services
    from fwapp.session import AgentSession
    services = build_services(cfg)
    try:
        Domain(services.con, cfg.root).create_target("t2")
        sess = AgentSession(services, target="t1", mcp=False, watchdog=False)
        sess.submit("绑定测试")
        item = sess._q.get_nowait()
        assert item[3] == "t1" and item[4] is None
        sess.set_target("t2")
        assert item[3] == "t1"  # 已在队列中的消息不能被后来的 target 改写
        with sess._lock:
            sess._busy = True
        sess.submit("排队测试")
        with sess._lock:
            sess._busy = False
        assert sess._pending[0][2] == "t2"
    finally:
        services.close()


def test_continue_while_busy_is_not_acknowledged_as_queued(tmp_path):
    _root, cfg = make_ws(tmp_path)

    class Slow:
        name = "scripted"

        def __init__(self):
            self.calls = 0

        def chat(self, messages, tools):
            self.calls += 1
            time.sleep(0.35)
            return Reply(content="FINAL: done", tool_calls=[])

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Slow(),
                    mcp=False, watchdog=False)
    try:
        sess = rt.session
        sess.submit("开始")
        t0 = time.time()
        while not sess.busy and time.time() - t0 < 3:
            time.sleep(0.02)
        assert sess.continue_run() is False
        assert not any(e.kind == "queued" for e in sess.drain_events())
        while sess.busy and time.time() - t0 < 5:
            time.sleep(0.02)
    finally:
        rt.dispose()


def test_close_while_busy_cancels_to_paused(tmp_path):
    _root, cfg = make_ws(tmp_path)

    class Slow:
        name = "scripted"

        def chat(self, messages, tools):
            time.sleep(0.5)
            return Reply(content="FINAL: late", tool_calls=[])

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Slow(),
                    mcp=False, watchdog=False)
    sess = rt.session
    sess.submit("退出测试")
    t0 = time.time()
    while (not sess.busy or not sess.run_id) and time.time() - t0 < 3:
        time.sleep(0.02)
    rid = sess.run_id
    assert rid
    rt.dispose()
    con = _db.connect(cfg.db_path)
    try:
        row = con.execute("SELECT state,handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
        assert row["state"] == "paused", dict(row)
        assert (row["handback_reason"] or "").startswith("user_stop"), dict(row)
    finally:
        con.close()


def test_submit_context_survives_target_new_and_resume(tmp_path):
    _root, cfg = make_ws(tmp_path)
    from fwapp.services import build_services
    from fwapp.session import AgentSession
    from fwcore.runtime import Runner
    services = build_services(cfg)
    try:
        Domain(services.con, cfg.root).create_target("t2")
        runner = Runner(services.con, cfg.root, ScriptedAdapter(script=[]),
                        mcp=False, watchdog=False)
        rid = runner.start("t1", "seed")
        runner.close()
        sess = AgentSession(services, target="t1", run_id=rid, mcp=False, watchdog=False)
        sess.submit("before switch")
        item = sess._q.get_nowait()
        assert item[3:] == ("t1", rid)
        sess.set_target("t2")
        sess.new_session()
        sess.resume_run(rid)
        assert item[3:] == ("t1", rid)
        with sess._lock:
            sess._busy = True
        sess.submit("queued after switch")
        with sess._lock:
            sess._busy = False
        assert sess._pending[0][2:] == ("t1", rid)
    finally:
        services.close()


def test_default_target_skips_archived_and_archived_submit_is_blocked(tmp_path):
    _root, cfg = make_ws(tmp_path)
    from fwapp.services import build_services
    from fwapp.session import AgentSession
    from fwcore.domain import Domain
    services = build_services(cfg)
    try:
        Domain(services.con, cfg.root).create_target("old")
        Domain(services.con, cfg.root).archive_target("old", "归档")
        sess = AgentSession(services, target=None, mcp=False, watchdog=False)
        assert sess.target == "t1"  # 旧的 archived 目标不能成为默认目标
        sess.set_target("old")
        sess.submit("不应开始")
        assert sess._q.empty()
        assert any("归档" in e.text for e in sess.drain_events())
    finally:
        services.close()


def test_transcript_keeps_tool_without_call_key_before_later_assistant(tmp_path):
    from fwcore.runtime import Runner

    root, cfg = make_ws(tmp_path)
    con = _db.connect(cfg.db_path)
    runner = Runner(con, str(root), ScriptedAdapter(script=[]), mcp=False, watchdog=False)
    rid = runner.start("t1", "hello")
    dom = Domain(con, str(root))
    dom.add_conversation(rid, "assistant", "before tools",
                         meta={"tool_calls": ["call_x"]})
    dom.add_conversation(
        rid, "tool", '{"status": "cancelled"}', status="cancelled",
        tool_call_id="call_x", meta={"tool": "shell_exec", "status": "cancelled"})
    created = con.execute(
        "SELECT created_at FROM conversation_entries WHERE run_id=? AND role='tool'",
        (rid,)).fetchone()["created_at"]
    # 未授权工具的真实 tool_calls 行没有 call_key；它仍必须在原位置显示。
    con.execute(
        "INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        ("TC-test", None, rid, "shell_exec", "{}", "cancelled", created))
    con.commit()
    dom.add_conversation(rid, "assistant", "after tools")

    rt = AppRuntime(cfg, target="t1", run_id=rid, mcp=False, watchdog=False)
    try:
        items = rt.session.transcript()
        assert [i.kind for i in items] == ["user", "assistant", "tool", "assistant"]
        assert items[2].meta["call"]["id"] == "TC-test"
    finally:
        rt.dispose()


def test_tokens_expose_latest_cache_hit_for_footer(tmp_path):
    from fwcore.runtime import Runner

    root, cfg = make_ws(tmp_path)
    con = _db.connect(cfg.db_path)
    runner = Runner(con, str(root), ScriptedAdapter(script=[]), mcp=False, watchdog=False)
    rid = runner.start("t1", "cache metric")
    for prompt, cached in ((100, 90), (200, 190)):
        con.execute(
            "INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
            ("2026-01-01T00:00:00+00:00", "model.call", rid,
             json.dumps({"usage": {"prompt_tokens": prompt},
                         "cached_tokens": cached})))
    con.commit()
    rt = AppRuntime(cfg, target="t1", run_id=rid, mcp=False, watchdog=False)
    try:
        tok = rt.session.snapshot().tokens
        assert tok["prompt"] == 300 and tok["cached"] == 280
        assert tok["last_prompt"] == 200 and tok["last_cached"] == 190
    finally:
        rt.dispose()


def test_tui_direct_policy_and_mcp_status(tmp_path):
    """TUI 不再有 authz/allow_all 开关；直接 DirectPolicy，并识别 MCP 配置。"""
    from fwcore.policy import DirectPolicy

    root, cfg = make_ws(tmp_path)
    (root / "state").mkdir(exist_ok=True)
    (root / "state" / "mcp.json").write_text(json.dumps({
        "mcpServers": {"demo": {"command": "true"}}
    }), encoding="utf-8")
    rt = AppRuntime(cfg, target="t1", mcp=False)
    try:
        runner = rt.session._make_runner(rt.session.services.con)
        try:
            assert isinstance(runner.policy, DirectPolicy)
            assert runner.policy.decide("shell_exec", "high", {}, requires="mcp")[0]
        finally:
            runner.close()
        status = rt.session.mcp_status()
        assert status["enabled"] is False
        assert "demo" in status["configured"]
        assert status["config_error"] is None
    finally:
        rt.dispose()


def test_bootstrap_target_is_reused_until_new_target(tmp_path):
    """A targetless TUI gets one placeholder target, not one per prompt."""
    root = tmp_path / "empty"
    root.mkdir()
    cfg = resolve(AppOptions(root=str(root), model="scripted", mcp=False, watchdog=False))
    from fwapp.services import build_services
    from fwapp.session import AgentSession
    services = build_services(cfg)
    sess = AgentSession(services, target=None, mcp=False, watchdog=False)
    try:
        first = sess.ensure_bootstrap_target()
        assert first and first.startswith("session-")
        assert sess.ensure_bootstrap_target() == first
        sess.new_session()
        assert sess.ensure_bootstrap_target() == first
        n = services.con.execute("SELECT COUNT(*) n FROM targets").fetchone()["n"]
        assert n == 1
    finally:
        sess.close()


def test_bootstrap_target_is_reused_by_a_new_session_instance(tmp_path):
    """A restarted TUI reuses the active placeholder instead of inserting another."""
    root = tmp_path / "empty2"
    root.mkdir()
    cfg = resolve(AppOptions(root=str(root), model="scripted", mcp=False, watchdog=False))
    from fwapp.services import build_services
    from fwapp.session import AgentSession
    first_services = build_services(cfg)
    first = AgentSession(first_services, target=None, mcp=False, watchdog=False)
    try:
        name = first.ensure_bootstrap_target()
        assert name
    finally:
        first.close()
    second_services = build_services(cfg)
    second = AgentSession(second_services, target=None, mcp=False, watchdog=False)
    try:
        assert second.target == name
        assert second.ensure_bootstrap_target() == name
        assert second_services.con.execute(
            "SELECT COUNT(*) n FROM targets").fetchone()["n"] == 1
    finally:
        second.close()
