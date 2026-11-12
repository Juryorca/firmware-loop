"""TUI 行为测试(Textual headless pilot:真实按键、真实布局、真实 Runtime)。

不调用外部模型:scripted 适配器 + 临时状态库。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, cast

from fwapp.args import AppOptions
from fwapp.config import resolve
from fwapp.modes.interactive import FwApp
from fwapp.runtime import AppRuntime
from fwapp.widgets.editor import PromptEditor
from fwapp.widgets.palette import CommandPalette
from fwapp.widgets.screens import (
    ConfirmScreen,
    HelpScreen,
    ResearchScreen,
    SelectorScreen,
    ToolsetScreen,
)
from fwapp.widgets.status import StatusBar
from fwapp.widgets.transcript import (
    MessageWidget,
    NoticeWidget,
    ThinkingBlock,
    ToolWidget,
    TranscriptView,
)
from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.models import Reply, ScriptedAdapter
from fwcore.streaming import StreamError


def make_ws(tmp_path, name="t1"):
    root = tmp_path / "ws"
    root.mkdir()
    con = _db.connect(_db.default_db_path(str(root)))
    Domain(con, str(root)).create_target(name)
    con.close()
    return resolve(AppOptions(root=str(root), model="scripted", mcp=False, watchdog=False))


def make_ws_without_target(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    con = _db.connect(_db.default_db_path(str(root)))
    con.close()
    return resolve(AppOptions(root=str(root), model="scripted", mcp=False, watchdog=False))


def _script():
    return [
        {"content": "先登记假设。", "tool_calls": [{
            "id": "c1", "name": "state_add_hypothesis",
            "args": {"target": "t1", "title": "解析器边界"}}]},
        {"content": "FINAL: 完成", "tool_calls": []},
    ]


def test_tui_boot_palette_screens_resize_and_quit(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1",
                    adapter_factory=lambda name: ScriptedAdapter(script=_script()),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            q = app.query
            await pilot.pause(0.3)
            assert app.query_one("#title") is not None
            from fwapp.widgets.transcript import NoticeWidget
            assert any("演示模式" in str(w.render()) for w in q(NoticeWidget))
            ed = app.query_one(PromptEditor)
            assert app.focused is ed

            # slash 补全
            ed.text = "/"
            await pilot.pause(0.2)
            palette = app.query_one(CommandPalette)
            assert palette.is_open and palette.option_count > 0
            await pilot.press("escape")
            await pilot.pause(0.1)
            assert not palette.is_open

            # overlay:帮助 / 恢复选择器 / 研究面板
            await pilot.press("f1")
            await pilot.pause(0.2)
            assert isinstance(app.screen, HelpScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)
            await pilot.press("f2")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ResearchScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)

            # 多行编辑:Shift+Enter 不提交
            ed.text = ""
            ed.focus()
            await pilot.press("a")
            await pilot.press("shift+enter")
            await pilot.press("b")
            assert "\n" in ed.text
            ed.text = "开始研究"
            await pilot.press("enter")
            for _ in range(60):
                await pilot.pause(0.1)
                if not app.sess.busy and not app.sess.pending:
                    break
            await pilot.pause(0.4)
            assert [w for w in q(ToolWidget)
                    if w.call_data.get("tool") == "state_add_hypothesis"]

            # 历史输入:↑ 取回上一条
            ed.focus()
            await pilot.press("up")
            assert ed.text.strip() == "开始研究"

            # /budget 走真实快照(命令执行会追加一条 notice)
            from fwapp.widgets.transcript import NoticeWidget
            before = len(q(NoticeWidget))
            ed.text = "/budget"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert len(q(NoticeWidget)) > before

            # 其余 slash commands 也必须连真实服务(不是静态帮助)
            ed.text = "/model scripted"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.sess.model_name == "scripted"
            ed.text = "/target t1"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert app.sess.target == "t1"
            ed.text = "/status"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ResearchScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)
            ed.text = "/hypotheses"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ResearchScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)
            ed.text = "/evidence"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ResearchScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)

            # 新增命令也必须走真实服务(空库时只产生提示,不崩溃)
            n_before = len(q(NoticeWidget))
            ed.text = "/skills"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert len(q(NoticeWidget)) > n_before
            n_before = len(q(NoticeWidget))
            ed.text = "/knowledge"
            await pilot.press("enter")
            await pilot.pause(0.2)
            assert len(q(NoticeWidget)) > n_before
            ed.text = "/tools"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert isinstance(app.screen, ToolsetScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)

            # 有 run 之后,/resume 打开选择器
            await pilot.press("ctrl+r")
            await pilot.pause(0.2)
            assert isinstance(app.screen, SelectorScreen)
            await pilot.press("escape")
            await pilot.pause(0.1)

            # resize 后布局仍可用;状态栏和编辑器保持紧凑
            await pilot.resize_terminal(80, 24)
            await pilot.pause(0.2)
            assert app.size.width == 80 and app.size.height == 24
            status = app.query_one(StatusBar)
            assert 1 <= status.size.height <= 3
            text = str(status.render())
            assert "工具调用" in text and "收尾阻塞" in text
            assert app.query_one(PromptEditor).size.height == 3
            await pilot.resize_terminal(120, 36)
            await pilot.pause(0.2)
            assert app.size.width == 120 and app.size.height == 36
            assert 1 <= app.query_one(StatusBar).size.height <= 3

            await pilot.press("ctrl+q")
            await pilot.pause(0.2)

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_click_transcript_keeps_editor_focus(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            ed = app.query_one(PromptEditor)
            assert app.focused is ed
            await pilot.click("#transcript")
            await pilot.pause(0.2)
            assert app.focused is ed
            await pilot.press("a")
            assert ed.text == "a"
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_tui_escape_cancels_running_turn(tmp_path):
    cfg = make_ws(tmp_path)

    class Slow:
        name = "scripted"

        def chat(self, messages, tools):
            time.sleep(0.8)
            return Reply(content="FINAL: 迟到", tool_calls=[])

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Slow(),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            ed = app.query_one(PromptEditor)
            ed.text = "开始"
            await pilot.press("enter")
            t0 = time.time()
            while not app.sess.busy and time.time() - t0 < 2:
                await pilot.pause(0.05)
            await pilot.press("escape")
            for _ in range(60):
                await pilot.pause(0.1)
                if not app.sess.busy:
                    break
            row = app.sess.services.con.execute(
                "SELECT state,handback_reason FROM runs WHERE id=?",
                (app.sess.run_id,)).fetchone()
            assert row["state"] == "paused"
            assert (row["handback_reason"] or "").startswith("user_stop")
            notices = [str(w.render()) for w in app.query("NoticeWidget")]
            assert sum("正在停止" in n for n in notices) == 1
            assert not any("回复未完整生成" in n or "已停止:" in n or "本轮已暂停" in n
                           for n in notices)
            assert app.focused is ed
            # /continue 恢复同一 run(再走一轮,不新建)
            ed.text = "/continue"
            await pilot.press("enter")
            for _ in range(80):
                await pilot.pause(0.1)
                if not app.sess.busy:
                    break
            row = app.sess.services.con.execute(
                "SELECT state FROM runs WHERE id=?", (app.sess.run_id,)).fetchone()
            assert row["state"] in ("finished", "paused")
            await pilot.press("ctrl+q")
            await pilot.pause(0.2)

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_tui_quit_confirm_while_running(tmp_path):
    cfg = make_ws(tmp_path)

    class Slow:
        name = "scripted"

        def chat(self, messages, tools):
            time.sleep(0.8)
            return Reply(content="FINAL: 迟到", tool_calls=[])

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Slow(),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            ed = app.query_one(PromptEditor)
            ed.text = "开始"
            await pilot.press("enter")
            t0 = time.time()
            while not app.sess.busy and time.time() - t0 < 2:
                await pilot.pause(0.05)
            await pilot.press("ctrl+q")
            await pilot.pause(0.2)
            assert isinstance(app.screen, ConfirmScreen)
            await pilot.press("n")
            await pilot.pause(0.2)
            assert not isinstance(app.screen, ConfirmScreen)
            await pilot.press("escape")
            for _ in range(60):
                await pilot.pause(0.1)
                if not app.sess.busy:
                    break
            row = app.sess.services.con.execute(
                "SELECT state FROM runs WHERE id=?", (app.sess.run_id,)).fetchone()
            assert row["state"] == "paused"
            await pilot.press("ctrl+q")
            await pilot.pause(0.2)

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_toggle_tools_includes_streaming_previews():
    from types import SimpleNamespace

    from fwapp.modes.interactive import FwApp

    class Body:
        display = False

    tool = SimpleNamespace(body=Body())
    stream = SimpleNamespace(body=Body())
    fake: Any = SimpleNamespace(
        _expand_tools=False,
        _tool_widgets={"a": tool},
        _stream_tools={"b": stream},
        _notice=lambda *a, **k: None,
    )
    FwApp.action_toggle_tools(fake)
    assert tool.body.display is True and stream.body.display is True
    FwApp.action_toggle_tools(fake)
    assert tool.body.display is False and stream.body.display is False


def test_toolset_screen_loads_in_worker(monkeypatch, tmp_path):
    from fwapp.widgets import screens

    calls = []
    report = {
        "required_total": 1, "required_ready": 0, "required_incomplete": 1,
        "optional_total": 0, "integrity": "incomplete", "items": [], "config_error": None,
    }

    def slow_check(root):
        calls.append(root)
        time.sleep(0.15)
        return report

    monkeypatch.setattr(screens, "check_toolset", slow_check)
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            screen = ToolsetScreen(str(tmp_path))
            app.push_screen(screen)
            await pilot.pause(0.05)
            assert "正在检查" in str(screen.query_one("#toolset-body").render())
            await pilot.pause(0.35)
            assert "工具就绪检查" in str(screen.query_one("#toolset-body").render())
            assert calls == [str(tmp_path)]
            await pilot.press("escape")
            await pilot.pause(0.1)
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_tool_setup_command_runs_in_worker(monkeypatch, tmp_path):
    from fwcore import toolset

    report = {
        "required_total": 0, "required_ready": 0, "required_incomplete": 0,
        "optional_total": 0, "integrity": "ready", "items": [], "config_error": None,
    }
    monkeypatch.setattr(toolset, "check_toolset", lambda root: report)
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            app.query_one(PromptEditor).text = "/tool-setup"
            await pilot.press("enter")
            await pilot.pause(0.2)
            texts = [str(w.render()) for w in app.query("NoticeWidget")]
            assert any("正在检查默认工具集" in t for t in texts)
            assert any("工具都已就绪" in t for t in texts)
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_model_picker_fetches_catalog(tmp_path, monkeypatch):
    """Ctrl+L / /model must fetch the provider catalog, not a hardcoded list."""
    from fwcore.models import ModelInfo
    cfg = make_ws(tmp_path)
    seen = {}
    def fake_available(cfg, *, refresh=False):
        seen["refresh"] = refresh
        return [ModelInfo(id="live-model", label="Live Model", api_id="live-model",
                          context_window=1_048_576)]
    monkeypatch.setattr("fwapp.modes.interactive.available_models", fake_available)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause(0.2)
            app.query_one(PromptEditor).text = "/model"
            await pilot.press("enter")
            for _ in range(50):
                await pilot.pause(0.05)
                if isinstance(app.screen, SelectorScreen):
                    break
            assert isinstance(app.screen, SelectorScreen)
            assert seen.get("refresh") is True
            assert any(i.value == "live-model" for i in app.screen.items)
            await pilot.press("escape")
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_invalid_model_does_not_change_current(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            app.query_one(PromptEditor).text = "/model definitely-not-a-model"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.sess.model_name == "scripted"
            texts = [str(w.render()) for w in app.query("NoticeWidget")]
            assert any("模型不可用" in t and "当前模型未改变" in t for t in texts)
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_continue_while_busy_reports_no_fake_queue(tmp_path):
    cfg = make_ws(tmp_path)

    class Slow:
        name = "scripted"

        def chat(self, messages, tools):
            time.sleep(0.5)
            return Reply(content="FINAL: 迟到", tool_calls=[])

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Slow(),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.2)
            app.query_one(PromptEditor).text = "开始"
            await pilot.press("enter")
            t0 = time.time()
            while not app.sess.busy and time.time() - t0 < 2:
                await pilot.pause(0.05)
            app.query_one(PromptEditor).text = "/continue"
            await pilot.press("enter")
            await pilot.pause(0.2)
            texts = [str(w.render()) for w in app.query("NoticeWidget")]
            assert any("暂时不能继续" in t for t in texts)
            assert not any("继续推进" in t for t in texts)
            await pilot.press("escape")
            for _ in range(60):
                await pilot.pause(0.1)
                if not app.sess.busy:
                    break
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_long_error_notice_is_bounded(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)
            app._notice("E" * 5000, "error")
            await pilot.pause(0.1)
            texts = [str(w.render()) for w in app.query("NoticeWidget")]
            assert any("E" * 100 in t for t in texts)
            assert all(len(t) < 1500 for t in texts)
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_status_bar_uses_user_labels_and_cache():
    from types import SimpleNamespace

    from fwapp.widgets.status import format_snapshot

    s: Any = SimpleNamespace(
        target="t1", model="scripted", state="running", busy=False, handback=None,
        target_status="archived",
        limits={"max_model_calls": 200}, calls=2, tools=3,
        tokens={"prompt": 100, "completion": 20, "cached": 75, "total": 120},
        context={"window": 1000, "last_prompt": 100, "percent": 10},
        hypotheses={"open": 1, "investigating": 0, "vulnerable": 0},
        evidence=2, blockers=["x"],
    )
    text = format_snapshot(s).plain
    assert "工具调用 3" in text
    assert "缓存 75%" in text
    assert "收尾阻塞 1" in text
    assert "运行中" in text
    assert "已归档" in text
    assert "H " not in text and " E " not in text
    s.state = "finished"
    assert "本轮完成" in format_snapshot(s).plain


def test_sigterm_while_model_blocked_pauses_run(tmp_path):
    import fcntl
    import os
    import pty
    import signal
    import struct
    import subprocess
    import sys
    import termios
    import textwrap
    import threading

    root = tmp_path / "ws"
    root.mkdir()
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    con = _db.connect(_db.default_db_path(str(root)))
    Domain(con, str(root)).create_target("t1")
    con.close()
    script = tmp_path / "run_tui.py"
    script.write_text(textwrap.dedent(f'''
        import sys, time
        sys.path.insert(0, {str(repo)!r})
        from fwapp.args import AppOptions
        from fwapp.config import resolve
        from fwapp.runtime import AppRuntime
        from fwapp.modes.interactive import run_interactive
        from fwcore.models import Reply

        class Slow:
            name = "scripted"
            def chat(self, messages, tools):
                time.sleep(1.5)
                return Reply(content="FINAL: late", tool_calls=[])

        cfg = resolve(AppOptions(root={str(root)!r}, model="scripted", mcp=False, watchdog=False))
        rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Slow(),
                        mcp=False, watchdog=False)
        try:
            run_interactive(rt)
        finally:
            rt.dispose()
    '''), encoding="utf-8")
    master, slave = pty.openpty()
    fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    env = dict(os.environ, PYTHONPATH=repo, TERM="xterm-256color")
    p = subprocess.Popen([sys.executable, str(script)], stdin=slave, stdout=slave,
                         stderr=slave, env=env, cwd=repo, close_fds=True)
    os.close(slave)

    def drain_pty():
        while True:
            try:
                if not os.read(master, 65536):
                    break
            except OSError:
                break

    reader = threading.Thread(target=drain_pty, daemon=True)
    reader.start()
    try:
        time.sleep(1.5)
        os.write(master, "开始".encode() + b"\r")
        row = None
        deadline = time.time() + 5
        while time.time() < deadline:
            c = _db.connect(str(root / "state" / "fwcore.sqlite3"))
            try:
                row = c.execute("SELECT id,state FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()
            finally:
                c.close()
            if row and row["state"] == "running":
                break
            time.sleep(0.1)
        assert row is not None
        os.kill(p.pid, signal.SIGTERM)
        p.wait(timeout=20)
        c = _db.connect(str(root / "state" / "fwcore.sqlite3"))
        try:
            final = c.execute("SELECT state,handback_reason FROM runs WHERE id=?",
                              (row["id"],)).fetchone()
        finally:
            c.close()
        assert final["state"] == "paused", dict(final)
        assert (final["handback_reason"] or "").startswith("user_stop"), dict(final)
    finally:
        if p.poll() is None:
            p.kill()
        os.close(master)
        reader.join(timeout=2)


def test_idle_tui_does_not_repeat_redraws(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.5)
            editor = app.query_one(PromptEditor)
            bar = app.query_one(StatusBar)
            from fwapp.widgets.transcript import PendingBar
            pending = app.query_one(PendingBar)
            assert editor.cursor_blink is False
            status_text = bar._last_plain
            pending_key = pending._last_pending
            await pilot.pause(1.2)
            assert bar._last_plain == status_text
            assert pending._last_pending == pending_key
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_tick_skips_status_refresh_when_nothing_changed(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.5)
            calls: list[int] = []
            app._update_status = lambda: calls.append(1)  # type: ignore[method-assign]
            app._tick()
            assert calls == []
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_tick_does_not_poll_tool_updates_when_idle(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)

            class IdleSession:
                busy = False
                pending = ()

                def drain_events(self):
                    return []

                def tool_updates(self):
                    raise AssertionError("idle tick must not scan tool_calls")

            app.sess = cast(Any, IdleSession())  # type: ignore[assignment]
            app._update_status = lambda: None  # type: ignore[method-assign]
            app._tick()
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_tick_does_not_refresh_status_for_text_only_deltas(tmp_path):
    from fwapp.session import UiEvent

    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)

            class DeltaSession:
                busy = False
                pending = ()

                def drain_events(self):
                    return [UiEvent("assistant_delta", "tick")]

                def tool_updates(self):
                    raise AssertionError("text-only tick must not scan tool_calls")

            app.sess = cast(Any, DeltaSession())  # type: ignore[assignment]
            calls: list[int] = []
            app._update_status = lambda: calls.append(1)  # type: ignore[method-assign]
            app._tick()
            assert calls == []
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_tool_body_is_built_only_when_expanded(tmp_path):
    output = tmp_path / "stdout.txt"
    output.write_text("tool output", encoding="utf-8")
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            tool = ToolWidget({
                "id": "t1", "tool": "shell_exec", "args": "{}",
                "status": "ok", "stdout_path": str(output),
            })
            assert tool._body_built is False
            tool.set_expanded(True)
            assert tool._body_built is True
            assert "tool output" in str(tool.body.render())
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_transcript_bounds_widget_tree(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)
            tv = app.query_one(TranscriptView)
            tv.MAX_WIDGETS = 20
            tv.remove_children()
            for i in range(tv.MAX_WIDGETS + 5):
                tv.append(NoticeWidget(f"line {i}"))
            await pilot.pause(0.2)
            assert len(tv.children) <= tv.MAX_WIDGETS
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_model_error_clears_busy_status(tmp_path):
    cfg = make_ws(tmp_path)

    class Failing:
        name = "scripted"

        def chat(self, messages, tools):
            raise StreamError("HTTP 0: 连接失败: cannot read from timed out object")

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Failing(),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            ed = app.query_one(PromptEditor)
            ed.text = "你好"
            await pilot.press("enter")
            for _ in range(60):
                await pilot.pause(0.05)
                if not app.sess.busy:
                    break
            await pilot.pause(0.5)
            assert app.sess.busy is False
            status = str(app.query_one(StatusBar).render())
            assert "正在处理" not in status
            assert "已暂停" in status or "paused" in status
            users = [w for w in app.query("MessageWidget") if getattr(w, "role", "") == "user"]
            assert users and all(w.size.height <= 3 for w in users)
            assistants = [w for w in app.query("MessageWidget") if getattr(w, "role", "") == "assistant"]
            assert all(getattr(w, "_text", "").strip() for w in assistants)
            notices = [str(w.render()) for w in app.query("NoticeWidget")]
            assert not any("已已" in n for n in notices)
            assert any("cannot read from timed out object" in n for n in notices)
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_model_error_does_not_block_next_submit(tmp_path):
    cfg = make_ws(tmp_path)
    calls = {"n": 0}

    class Flaky:
        name = "scripted"

        def chat(self, messages, tools):
            calls["n"] += 1
            if calls["n"] == 1:
                raise StreamError("HTTP 0: 连接失败: cannot read from timed out object")
            return Reply(content="FINAL: 已恢复", tool_calls=[])

    rt = AppRuntime(cfg, target="t1", adapter_factory=lambda name: Flaky(),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            ed = app.query_one(PromptEditor)
            ed.text = "你好"
            await pilot.press("enter")
            for _ in range(60):
                await pilot.pause(0.05)
                if not app.sess.busy:
                    break
            assert app.sess.busy is False
            ed.text = "继续"
            await pilot.press("enter")
            for _ in range(100):
                await pilot.pause(0.05)
                if calls["n"] >= 2 and not app.sess.busy:
                    break
            await pilot.pause(0.5)
            assert app.sess.busy is False
            assert calls["n"] >= 2
            assert any("已恢复" in getattr(w, "_text", "") for w in app.query("MessageWidget"))
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_thinking_block_is_visible_and_toggles(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            tv = app.query_one(TranscriptView)
            msg = MessageWidget("assistant", "回答正文", {"reasoning": "第一步\n第二步"})
            tv.mount(msg)
            await pilot.pause(0.2)
            tb = msg._thinking_widget
            assert isinstance(tb, ThinkingBlock)
            assert tb.body.display is True
            assert "第一步" in str(tb.body.render())
            assert "思考过程" in str(tb.header.render())
            await pilot.click(tb.header)
            await pilot.pause(0.1)
            assert tb.body.display is False
            await pilot.click(tb.header)
            await pilot.pause(0.1)
            assert tb.body.display is True
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_context_status_uses_last_main_request_like_pi():
    """The footer uses the last main request; summarize calls never replace it."""
    from types import SimpleNamespace

    from fwapp.widgets.status import format_snapshot

    s: Any = SimpleNamespace(
        target="t1", model="scripted", state="paused", busy=False, handback=None,
        target_status="active", limits={"max_model_calls": 200}, calls=2, tools=0,
        tokens={"prompt": 100, "cached": 75, "last_prompt": 43_103,
                "last_cached": 43_000, "completion": 0, "total": 100},
        context={"window": 1_000_000, "estimated": 945_218,
                 "last_prompt": 756_111, "percent": 75.6},
        hypotheses={}, evidence=0, blockers=[],
    )
    text = format_snapshot(s).plain
    assert "上下文 756.1k/1.0M" in text
    assert "43.1k" not in text


def test_status_bar_cache_uses_latest_request_like_pi():
    from types import SimpleNamespace

    from fwapp.widgets.status import format_snapshot

    s: Any = SimpleNamespace(
        target="t1", model="scripted", state="running", busy=False, handback=None,
        target_status="active", limits={"max_model_calls": 200}, calls=2, tools=0,
        tokens={"prompt": 100, "cached": 75, "last_prompt": 200,
                "last_cached": 190, "completion": 0, "total": 100},
        context={}, hypotheses={}, evidence=0, blockers=[],
    )
    text = format_snapshot(s).plain
    assert "缓存 95%" in text
    assert "缓存 75%" not in text


def test_transcript_auto_scrolls_after_appended_notice(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(90, 20)) as pilot:
            await pilot.pause(0.3)
            tv = app.query_one(TranscriptView)
            tv.remove_children()
            for i in range(50):
                tv.append(MessageWidget("notice", f"line {i}"))
            await pilot.pause(0.3)
            tv.scroll_end(animate=False)
            await pilot.pause(0.1)
            assert tv.at_bottom()
            tv.append(NoticeWidget("MCP: " + "x" * 400))
            await pilot.pause(0.2)
            assert tv.at_bottom()
            tv.scroll_home(animate=False)
            await pilot.pause(0.1)
            assert not tv.at_bottom()
            tv.append(NoticeWidget("while reading above"))
            await pilot.pause(0.2)
            assert not tv.at_bottom()
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_selected_text_is_copied_automatically(tmp_path, monkeypatch):
    from textual import events

    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)
            monkeypatch.setattr(app.screen, "get_selected_text", lambda: "selected text")
            app.on_text_selected(events.TextSelected())
            assert app.clipboard == "selected text"

            app.copy_to_clipboard("keep")
            monkeypatch.setattr(app.screen, "get_selected_text", lambda: "")
            app.on_text_selected(events.TextSelected())
            assert app.clipboard == "keep"
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_mouse_selection_auto_copies_to_clipboard(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(80, 20)) as pilot:
            await pilot.pause(0.2)
            tv = app.query_one(TranscriptView)
            tv.remove_children()
            notice = NoticeWidget("alpha beta gamma")
            tv.append(notice)
            await pilot.pause(0.3)
            await pilot.mouse_down(notice, offset=(0, 0))
            await pilot.mouse_up(notice, offset=(10, 0))
            await pilot.pause(0.2)
            assert "alpha" in app.clipboard
            assert app.focused is app.query_one(PromptEditor)
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_streamed_assistant_reply_is_visible(tmp_path):
    """A real streamed turn must leave a visible final assistant body."""
    cfg = make_ws(tmp_path)
    script = [{"content": "**hello from stream**", "tool_calls": []}]
    rt = AppRuntime(cfg, target="t1",
                    adapter_factory=lambda name: ScriptedAdapter(script=script),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(90, 20)) as pilot:
            await pilot.pause(0.2)
            ed = app.query_one(PromptEditor)
            ed.text = "say hello"
            await pilot.press("enter")
            for _ in range(80):
                await pilot.pause(0.05)
                if not app.sess.busy:
                    break
            await pilot.pause(0.5)
            msgs = app.screen.walk_children(MessageWidget)
            assert any("hello from stream" in m._text for m in msgs), [m._text for m in msgs]
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_streaming_message_is_plain_until_final(tmp_path):
    """Streaming must not parse Markdown per delta; final render is Markdown."""
    from textual.widgets import Markdown, Static
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(90, 20)) as pilot:
            tv = app.query_one(TranscriptView)
            tv.remove_children()
            msg = MessageWidget("assistant", "", streaming=True)
            tv.append(msg)
            await pilot.pause(0.1)
            assert isinstance(msg.body, Static)
            msg.append_text("**still plain**")
            await pilot.pause(0.1)
            assert isinstance(msg.body, Static)
            msg.finalize("**now markdown**")
            await pilot.pause(0.2)
            assert isinstance(msg.body, Markdown)
            assert "now markdown" in msg.body.source
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_tick_coalesces_stream_deltas(tmp_path, monkeypatch):
    """Token-rate deltas must become one Markdown append per UI tick."""
    from fwapp.session import UiEvent
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(90, 20)) as pilot:
            await pilot.pause(0.1)
            class FakeSession:
                def __init__(self):
                    self.events = [UiEvent("assistant_delta", "a"),
                                   UiEvent("assistant_delta", "b"),
                                   UiEvent("assistant_delta", "c")]
                def drain_events(self):
                    out, self.events = self.events, []
                    return out
                def tool_updates(self):
                    return []
                pending = ()
                busy = False
            app.sess = cast(Any, FakeSession())  # type: ignore[assignment]
            calls = []
            monkeypatch.setattr(MessageWidget, "append_text",
                                lambda self, text: calls.append(text))
            monkeypatch.setattr(app, "_update_status", lambda: None)
            app._tick()
            assert calls == ["abc"]
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_mcp_command_auto_scrolls_to_bottom(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(90, 20)) as pilot:
            await pilot.pause(0.3)
            tv = app.query_one(TranscriptView)
            tv.remove_children()
            for i in range(50):
                tv.append(MessageWidget("notice", f"line {i}"))
            await pilot.pause(0.3)
            tv.scroll_end(animate=False)
            await pilot.pause(0.1)
            assert tv.at_bottom()
            tv.scroll_home(animate=False)
            await pilot.pause(0.1)
            assert not tv.at_bottom()
            ed = app.query_one(PromptEditor)
            ed.text = "/mcp"
            await pilot.press("enter")
            await pilot.pause(0.3)
            assert app.query("NoticeWidget")
            assert tv.at_bottom()
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_mouse_selection_works_on_assistant_markdown(tmp_path):
    cfg = make_ws(tmp_path)
    rt = AppRuntime(cfg, target="t1", mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause(0.2)
            tv = app.query_one(TranscriptView)
            tv.remove_children()
            msg = MessageWidget("assistant", "alpha beta gamma")
            tv.append(msg)
            await pilot.pause(0.3)
            await pilot.mouse_down(msg.body, offset=(0, 0))
            await pilot.mouse_up(msg.body, offset=(12, 0))
            await pilot.pause(0.2)
            assert "alpha" in app.clipboard
            assert app.focused is app.query_one(PromptEditor)
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_fw_template_bootstraps_target_and_agent_names_it(tmp_path):
    cfg = make_ws_without_target(tmp_path)
    # Use the package default fw template; no .pi prompts directory needed.
    rt = AppRuntime(cfg, target=None,
                    adapter_factory=lambda name: ScriptedAdapter(script=[
                        {"content": "先确认固件名。", "tool_calls": [{
                            "id": "c1", "name": "target_create",
                            "args": {"name": "Zyxel-GS1900"}}]},
                        {"content": "FINAL: named", "tool_calls": []},
                    ]),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            ed = app.query_one(PromptEditor)
            ed.text = "/fw GS1900"
            await pilot.press("enter")
            for _ in range(120):
                await pilot.pause(0.1)
                if app.sess.target == "Zyxel-GS1900" and not app.sess.busy:
                    break
            assert app.sess.target == "Zyxel-GS1900"
            row = app.sess.services.con.execute(
                "SELECT status FROM targets WHERE name=?", ("Zyxel-GS1900",)).fetchone()
            assert row is not None and row["status"] == "active"
            default = app.sess.services.con.execute(
                "SELECT status FROM targets WHERE name='session'").fetchone()
            assert default is not None and default["status"] == "active"
            users = [w for w in app.screen.walk_children(MessageWidget)
                     if w.role == "user"]
            assert any("GS1900" in w._text for w in users)
    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()

def test_submit_prompt_template_command_expands_and_submits(tmp_path):
    """`/提交` is a Pi-style .pi/prompts template, not a hardcoded handler."""
    cfg = make_ws(tmp_path)
    prompts = tmp_path / "ws" / ".pi" / "prompts"
    prompts.mkdir(parents=True)
    (prompts / "提交.md").write_text(
        "---\n"
        "description: Submit a report\n"
        "argument-hint: \"<report>\"\n"
        "---\n"
        "请提交 ${1}${2:+(厂商: ${2})}，先定位报告。\n",
        encoding="utf-8")
    rt = AppRuntime(cfg, target="t1",
                    adapter_factory=lambda name: ScriptedAdapter(
                        script=[{"content": "FINAL: template submitted", "tool_calls": []}]),
                    mcp=False, watchdog=False)
    app = FwApp(rt)

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(0.3)
            assert any(c.name == "提交" for c in app._commands)
            ed = app.query_one(PromptEditor)
            ed.text = "/提交 VR-01"
            await pilot.press("enter")
            for _ in range(80):
                await pilot.pause(0.1)
                if not app.sess.busy:
                    break
            users = [w for w in app.screen.walk_children(MessageWidget)
                     if w.role == "user"]
            assert any("请提交 VR-01" in w._text for w in users)
    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()
