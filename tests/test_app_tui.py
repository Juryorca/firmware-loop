"""TUI 行为测试(Textual headless pilot:真实按键、真实布局、真实 Runtime)。

不调用外部模型:scripted 适配器 + 临时状态库。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

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
            tv.scroll_home(animate=False)
            await pilot.pause(0.1)
            assert not tv.at_bottom()
            tv.append(NoticeWidget("MCP: " + "x" * 400))
            await pilot.pause(0.2)
            assert tv.at_bottom()
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
