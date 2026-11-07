"""Interactive Mode:Textual 全屏应用(对应 Pi 的 InteractiveMode)。

布局与交互对齐 Pi:
- 顶部标题 / 中间 Transcript / 输入编辑器 / 状态栏 / 命令补全;
- 工具调用可折叠,运行状态实时刷新;
- Slash Commands / 选择器 / Overlay;
- 所有动作都调用 AgentSession → fwcore.Runner,不在这里跑 Agent Loop。
"""

from __future__ import annotations

import contextlib
import signal
import time
from typing import Any

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.widgets import Static

from ..commands import BY_NAME
from ..config import available_models
from ..runtime import AppRuntime
from ..session import AgentSession
from ..widgets.editor import PromptEditor
from ..widgets.palette import CommandPalette
from ..widgets.screens import (
    ConfirmScreen, HelpScreen, ResearchScreen, SelectItem, SelectorScreen,
)
from ..widgets.status import HINTS, StatusBar
from ..widgets.transcript import (
    MessageWidget, NoticeWidget, PendingBar, RunBanner, ToolWidget, TranscriptView,
)


class FwApp(App):
    CSS = """
    Screen { background: $surface; }
    #title { height: 1; background: $primary-darken-2; color: $text; padding: 0 1; }
    #transcript { height: 1fr; padding: 0 1; }
    .msg { margin: 1 0 0 0; }
    .msg-role { color: $accent; text-style: bold; }
    .msg.user .msg-body { color: $text; }
    .msg.assistant .msg-body { color: $text; }
    .thinking { color: $text-muted; }
    .thinking.hidden { display: none; }
    .notice { color: $text-muted; margin: 0 0 0 1; }
    .notice.warning { color: $warning; }
    .notice.error { color: $error; }
    .notice.success { color: $success; }
    .banner { margin: 1 0; color: $accent; }
    .tool { margin: 0 0 0 1; padding-left: 1; border-left: thick $primary; }
    .tool-header { color: $text; }
    .tool.ok { border-left: thick $success; }
    .tool.error { border-left: thick $error; }
    .tool.cancelled { border-left: thick $warning; }
    .tool.running { border-left: thick $accent; }
    .tool-body { color: $text-muted; padding-left: 1; }
    #pending { height: auto; max-height: 3; color: $warning; padding: 0 1; }
    StatusBar { height: 2; background: $panel; color: $text; padding: 0 1; }
    #palette { display: none; height: auto; max-height: 10; border: solid $accent; background: $panel; }
    #editor { height: 5; border: tall $primary; background: $surface; }
    #hints { height: 1; color: $text-muted; padding: 0 1; }
    .dialog { width: 90%; height: 80%; margin: 2 4; padding: 1 2;
              border: thick $accent; background: $panel; }
    .dialog.confirm { width: 70%; height: auto; }
    .dialog-title { text-style: bold; color: $accent; margin-bottom: 1; }
    .dialog-hint { color: $text-muted; margin-top: 1; }
    .dialog-buttons { height: auto; margin-top: 1; }
    .pane { height: 1fr; overflow-y: auto; }
    #search { margin-bottom: 1; }
    #options { height: 1fr; }
    """

    BINDINGS = [
        ("f1", "help", "Help"),
        ("f2", "research", "Research"),
        ("ctrl+g", "research", "Research"),
        ("ctrl+q", "quit_app", "Quit"),
        ("ctrl+l", "pick_model", "Model"),
        ("ctrl+r", "pick_resume", "Resume"),
        ("ctrl+t", "pick_target", "Target"),
        ("ctrl+o", "toggle_tools", "Expand tools"),
        ("ctrl+c", "interrupt", "Interrupt"),
    ]

    def __init__(self, runtime: AppRuntime, diagnostics=None):
        super().__init__()
        self.runtime = runtime
        self.sess: AgentSession = runtime.session
        self.diagnostics = list(diagnostics or [])
        self._tool_widgets: dict[str, ToolWidget] = {}
        self._expand_tools = False
        self._show_thinking = False
        self._last_sigint = 0.0

    # ---------------- 组装 ----------------
    def compose(self) -> ComposeResult:
        yield Static(id="title")
        yield TranscriptView(id="transcript")
        yield PendingBar(id="pending")
        yield StatusBar(id="status")
        yield CommandPalette()
        yield PromptEditor()
        yield Static(HINTS, id="hints")

    def on_mount(self) -> None:
        self.query_one(PromptEditor).completion_active = (
            lambda: self.query_one(CommandPalette).is_open)
        self.query_one(PromptEditor).focus()
        self._update_title()
        self._update_status()
        self._load_transcript()
        for d in self.diagnostics:
            if d.level != "info":
                self._notice(d.message, d.level)
        names = self.sess.services.con.execute("SELECT COUNT(*) n FROM targets").fetchone()["n"]
        if not names:
            self._notice("状态库还没有目标:先运行 `fw-core init <target>` 或迁移历史目标。", "warning")
        self.set_interval(0.25, self._tick)
        self.set_interval(1.0, self._update_status)

    # ---------------- 渲染 ----------------
    def _append(self, widget) -> None:
        self.query_one(TranscriptView).append(widget)

    def _notice(self, text: str, level: str = "info") -> None:
        self._append(NoticeWidget(text, level))

    def _update_title(self) -> None:
        s = self.sess.snapshot()
        t = Text()
        t.append("fw-core", style="bold")
        t.append("  ")
        t.append(s.target or "no target", style="bold cyan")
        t.append("  ")
        t.append(s.run_id or "no run", style="dim")
        t.append("  ")
        t.append(s.state, style="bold green" if s.state == "running" else "yellow")
        if s.busy:
            t.append("  ⏳ working", style="yellow")
        self.query_one("#title", Static).update(t)

    def _update_status(self) -> None:
        try:
            s = self.sess.snapshot()
        except Exception as e:
            self.query_one(StatusBar).update(Text(f"状态读取失败: {e}", style="red"))
            return
        self.query_one(StatusBar).update_snapshot(s)
        self.query_one(PendingBar).update_pending(s.pending)
        self._update_title()

    def _load_transcript(self) -> None:
        tv = self.query_one(TranscriptView)
        tv.remove_children()
        self._tool_widgets.clear()
        for item in self.sess.transcript():
            if item.kind == "assistant":
                self._append(MessageWidget("assistant", item.text, item.meta))
            elif item.kind == "user":
                self._append(MessageWidget("user", item.text))
            elif item.kind == "tool":
                self._upsert_tool(item.meta.get("call") or {}, force_append=True)
        # 先渲染转录,再把缓存标记为"已见";否则下一次轮询会重复追加
        for call in self.sess.tool_updates():
            if str(call.get("id")) not in self._tool_widgets:
                self._upsert_tool(call)
        tv.scroll_end(animate=False)

    def _upsert_tool(self, call: dict, force_append: bool = False) -> None:
        key = str(call.get("id") or "")
        if not key:
            return
        w = self._tool_widgets.get(key)
        if w is None:
            w = ToolWidget(call, expanded=self._expand_tools)
            self._tool_widgets[key] = w
            self._append(w)
        else:
            w.update_call(call)

    def _tick(self) -> None:
        for ev in self.sess.drain_events():
            self._handle_event(ev)
        for call in self.sess.tool_updates():
            self._upsert_tool(call)
        for upd in self.sess.model_updates():
            p = upd.get("payload") or {}
            usage = []
            if p.get("prompt_tokens") is not None:
                usage.append(f"prompt={p.get('prompt_tokens')}")
            if p.get("completion_tokens") is not None:
                usage.append(f"completion={p.get('completion_tokens')}")
            if p.get("tool_calls"):
                usage.append(f"tools={p['tool_calls']}")
            self._notice("model call" + (f" step={p.get('step')}" if p.get("step") else "")
                         + (" · " + " ".join(usage) if usage else ""))
        self.query_one(PendingBar).update_pending(self.sess.pending)

    def _handle_event(self, ev) -> None:
        if ev.kind == "turn_done":
            final = (ev.data.get("final") or "").strip()
            if final:
                self._append(MessageWidget("assistant", final))
            state = ev.data.get("state")
            hb = ev.data.get("handback")
            blockers = ev.data.get("blockers") or []
            if state and state != "finished":
                self._notice(f"run {state}" + (f" · {hb}" if hb else ""), "warning")
            elif hb in ("awaiting_user_close", "closed"):
                self._notice(f"程序判定收尾条件{'已满足' if hb == 'awaiting_user_close' else '已确认'}"
                             "(等用户确认,不自动收尾)", "success")
            if blockers:
                self._notice("未收口: " + "; ".join(blockers[:4]), "info")
            self._update_status()
        elif ev.kind == "error":
            self._notice(ev.text, "error")
        elif ev.kind == "cancelled":
            self._notice(f"已停止: {ev.text}", "warning")
            back = self.sess.take_returned()
            if back:
                self.query_one(PromptEditor).text = "\n".join(back)
        elif ev.kind == "session.new":
            self.query_one(TranscriptView).remove_children()
            self._tool_widgets.clear()
            self._append(RunBanner("新 run:下一条消息会创建新的研究会话"))
            self._update_status()
        elif ev.kind in ("session.resumed", "session.target"):
            self._load_transcript()
            data = ev.data or {}
            self._append(RunBanner(
                f"已切换: {data.get('target') or self.sess.target} · {data.get('run') or self.sess.run_id or 'no run'}"))
            self._update_status()
        elif ev.kind == "session.model":
            self._notice(f"模型 → {ev.data.get('model') or self.sess.model_name}")
            self._update_status()
        elif ev.kind == "busy":
            self._update_status()

    # ---------------- 输入 ----------------
    def on_prompt_editor_submitted(self, msg: PromptEditor.Submitted) -> None:
        pal = self.query_one(CommandPalette)
        if pal.is_open:
            text = (msg.text or "").strip()
            name = text[1:].split(" ", 1)[0].lower() if text.startswith("/") else ""
            if name in BY_NAME and " " not in text[1:]:
                pal.hide()                 # 精确命令名:Enter 直接执行,Tab 才是补全
                self.query_one(PromptEditor).text = ""
                self._command(text)
            else:
                self._complete()
            return
        self._submit(msg.text)

    def on_prompt_editor_queued(self, msg: PromptEditor.Queued) -> None:
        text = (msg.text or "").strip()
        if not text:
            return
        self._append(MessageWidget("user", text + "  (queued)"))
        self.query_one(PromptEditor).remember(text)
        self.query_one(PromptEditor).text = ""
        self.sess.submit(text)
        self._update_status()

    def on_prompt_editor_escape(self, msg: PromptEditor.Escape) -> None:
        pal = self.query_one(CommandPalette)
        if pal.is_open:
            pal.hide()
            return
        if self.sess.busy:
            self.sess.cancel()
            self._notice("已请求停止(在安全点暂停)", "warning")
        elif self.query_one(PromptEditor).text:
            self.query_one(PromptEditor).text = ""

    def on_prompt_editor_interrupt(self, msg: PromptEditor.Interrupt) -> None:
        self.action_interrupt()

    def on_prompt_editor_completion_key(self, msg: PromptEditor.CompletionKey) -> None:
        pal = self.query_one(CommandPalette)
        if msg.key == "up":
            pal.move(-1)
        elif msg.key == "down":
            pal.move(1)
        elif msg.key in ("enter", "tab"):
            editor = self.query_one(PromptEditor)
            text = (editor.text or "").strip()
            name = text[1:].split(" ", 1)[0].lower() if text.startswith("/") else ""
            if msg.key == "enter" and name in BY_NAME and " " not in text[1:]:
                pal.hide()                 # 精确命令名:Enter 执行,Tab 补全
                editor.text = ""
                self._command(text)
            else:
                self._complete()
        elif msg.key == "escape":
            pal.hide()

    def _complete(self) -> None:
        pal = self.query_one(CommandPalette)
        cmd = pal.current()
        pal.hide()
        if not cmd:
            return
        editor = self.query_one(PromptEditor)
        editor.text = "/" + cmd.name + (" " if cmd.arg_hint else "")
        editor.focus()

    def on_text_area_changed(self, event) -> None:      # 命令补全跟随输入
        with contextlib.suppress(Exception):
            editor = self.query_one(PromptEditor)
            if event.text_area is editor:
                self.query_one(CommandPalette).show_for(editor.text)

    def _submit(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        editor = self.query_one(PromptEditor)
        editor.text = ""
        editor.remember(text)
        self.query_one(CommandPalette).hide()
        if text.startswith("/"):
            self._command(text)
            return
        if not self.sess.target:
            self._notice("没有目标:先 `fw-core init <target>`。", "warning")
            return
        self._append(MessageWidget("user", text))
        self.sess.submit(text)
        self._update_status()

    # ---------------- Slash Commands ----------------
    def _command(self, text: str) -> None:
        body = text[1:].strip()
        name, _, arg = body.partition(" ")
        name = name.lower()
        arg = arg.strip()
        if name not in BY_NAME:
            self._notice(f"未知命令: /{name}(/help 查看)", "error")
            return
        handler = getattr(self, f"_cmd_{name}", None)
        if handler is None:
            self._notice(f"/{name} 尚未实现", "error")
            return
        handler(arg)

    def _cmd_help(self, arg: str) -> None:
        self.push_screen(HelpScreen())

    def _cmd_quit(self, arg: str) -> None:
        self.action_quit_app()

    def _cmd_clear(self, arg: str) -> None:
        self.query_one(TranscriptView).remove_children()
        self._tool_widgets.clear()
        self._notice("视图已清空(数据库与研究状态未变)")

    def _cmd_expand(self, arg: str) -> None:
        self.action_toggle_tools()

    def _cmd_status(self, arg: str) -> None:
        self.push_screen(ResearchScreen(self.sess, tab="progress"))

    def _cmd_hypotheses(self, arg: str) -> None:
        self.push_screen(ResearchScreen(self.sess, tab="hypotheses"))

    def _cmd_evidence(self, arg: str) -> None:
        self.push_screen(ResearchScreen(self.sess, tab="evidence"))

    def _cmd_budget(self, arg: str) -> None:
        s = self.sess.snapshot()
        lim = s.limits or {}
        tok = s.tokens or {}
        ctx = s.context or {}
        t = Text()
        t.append("预算与用量\n", style="bold")
        t.append(f"- 模型调用 {s.calls}/{lim.get('max_model_calls')} · "
                 f"工具 {s.tools} · 时长 {s.elapsed:.0f}s/{lim.get('max_seconds')}\n")
        t.append(f"- Token prompt={tok.get('prompt')} completion={tok.get('completion')} "
                 f"cache={tok.get('cached')} total={tok.get('total')}\n")
        if ctx.get("window"):
            t.append(f"- 上下文 {ctx.get('last_prompt') or ctx.get('estimated')}/{ctx['window']}")
            if ctx.get("percent") is not None:
                t.append(f" ({ctx['percent']}%)")
            t.append("\n")
        else:
            t.append("- 上下文窗口未配置(state/model.json 的 context_tokens)\n")
        if s.budget.get("last_limit"):
            t.append(f"- 最近触发: {s.budget['last_limit']}\n", style="yellow")
        self._notice(t.plain)

    def _cmd_model(self, arg: str) -> None:
        if arg:
            self.sess.set_model(arg)
            return
        items = [SelectItem(v, v, d) for v, d in available_models(self.runtime.cfg)]
        if not items:
            self._notice("没有可用模型", "warning")
            return
        self.push_screen(SelectorScreen("选择模型", items),
                         lambda v: self.sess.set_model(v) if v else None)

    def _cmd_target(self, arg: str) -> None:
        if arg:
            self.sess.set_target(arg)
            self._load_transcript()
            return
        names = [r["name"] for r in self.sess.services.con.execute(
            "SELECT name FROM targets ORDER BY name").fetchall()]
        if not names:
            self._notice("还没有目标:先 `fw-core init <target>`", "warning")
            return
        self.push_screen(SelectorScreen("选择目标", [SelectItem(n, n) for n in names]),
                         self._switch_target)

    def _switch_target(self, value: str | None) -> None:
        if value:
            self.sess.set_target(value)
            self._load_transcript()
            self._update_status()

    def _cmd_resume(self, arg: str) -> None:
        if arg:
            self.sess.resume_run(arg)
            self._load_transcript()
            return
        rows = [dict(r) for r in self.sess.services.con.execute(
            "SELECT r.id,r.state,r.started_at,r.goal,t.name AS target FROM runs r"
            " JOIN targets t ON t.id=r.target_id ORDER BY r.started_at DESC, r.rowid DESC LIMIT 200"
        ).fetchall()]
        if not rows:
            self._notice("没有历史 run", "warning")
            return
        items = [SelectItem(r["id"], f"{r['id']} · {r['target']}",
                            f"{r['state']} · {r['started_at'][:19]} · {(r['goal'] or '')[:80]}")
                 for r in rows]
        self.push_screen(SelectorScreen("恢复历史 run", items), self._resume_selected)

    def _resume_selected(self, value: str | None) -> None:
        if value:
            self.sess.resume_run(value)
            self._load_transcript()
            self._update_status()

    def _cmd_new(self, arg: str) -> None:
        self.sess.new_session()
        self._update_status()

    def _cmd_continue(self, arg: str) -> None:
        if not self.sess.run_id:
            self._notice("没有可继续的 run(先发一条消息)", "warning")
            return
        self.sess.continue_run()
        self._notice("继续推进…")

    # ---------------- Actions ----------------
    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_research(self) -> None:
        self.push_screen(ResearchScreen(self.sess, tab="progress"))

    def action_pick_model(self) -> None:
        self._cmd_model("")

    def action_pick_target(self) -> None:
        self._cmd_target("")

    def action_pick_resume(self) -> None:
        self._cmd_resume("")

    def action_toggle_tools(self) -> None:
        self._expand_tools = not self._expand_tools
        for w in self._tool_widgets.values():
            w.body.display = self._expand_tools
        self._notice("工具输出: " + ("展开" if self._expand_tools else "折叠"))

    def action_interrupt(self) -> None:
        if self.sess.busy:
            self.sess.cancel()
            self._notice("已请求停止(安全点暂停)", "warning")
            return
        editor = self.query_one(PromptEditor)
        if editor.text:
            editor.text = ""
            return
        now = time.time()
        if now - self._last_sigint < 1.5:
            self.exit()
        else:
            self._last_sigint = now
            self._notice("再按一次 Ctrl+C 退出")

    def action_quit_app(self) -> None:
        if self.sess.busy:
            def done(ok: bool | None) -> None:
                if ok:
                    self.sess.cancel("退出应用")
                    self.exit()
            self.push_screen(ConfirmScreen("研究仍在执行。退出会请求在安全点暂停(状态已落库,可 resume)。"),
                             done)
        else:
            self.exit()

    def on_key(self, event: events.Key) -> None:
        # 编辑器之外(例如面板打开)也允许 Esc 关闭由 ModalScreen 自己处理
        if event.key == "ctrl+c" and not isinstance(self.focused, PromptEditor):
            self.action_interrupt()
            event.stop()


def run_interactive(runtime: AppRuntime, diagnostics=None) -> int:
    app = FwApp(runtime, diagnostics=diagnostics)
    old: dict[int, Any] = {}

    def _terminate(signum, frame):     # SIGTERM/SIGHUP:走正常退出路径,恢复终端
        with contextlib.suppress(Exception):
            app.exit()

    for sig in (signal.SIGTERM, signal.SIGHUP):
        with contextlib.suppress(Exception):
            old[sig] = signal.getsignal(sig)
            signal.signal(sig, _terminate)
    try:
        app.run()
    finally:
        for sig, handler in old.items():
            with contextlib.suppress(Exception):
                signal.signal(sig, handler)
    return 0
