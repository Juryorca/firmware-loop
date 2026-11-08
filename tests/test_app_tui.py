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
from fwapp.widgets.transcript import ToolWidget
from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.models import Reply, ScriptedAdapter


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
            assert app.query_one(StatusBar).size.height == 1
            assert app.query_one(PromptEditor).size.height == 3
            await pilot.resize_terminal(120, 36)
            await pilot.pause(0.2)
            assert app.size.width == 120 and app.size.height == 36
            assert app.query_one(StatusBar).size.height == 1

            await pilot.press("ctrl+q")
            await pilot.pause(0.2)

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
    import fwapp.widgets.screens as screens

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
            assert "Default Toolset Integrity" in str(screen.query_one("#toolset-body").render())
            assert calls == [str(tmp_path)]
            await pilot.press("escape")
            await pilot.pause(0.1)
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()


def test_tool_setup_command_runs_in_worker(monkeypatch, tmp_path):
    import fwcore.toolset as toolset

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
            assert any("默认工具集已完整" in t for t in texts)
            await pilot.press("ctrl+q")

    try:
        asyncio.run(scenario())
    finally:
        rt.dispose()
