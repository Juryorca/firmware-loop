"""Interactive Mode:Textual 全屏应用(对应 Pi 的 InteractiveMode)。

布局与交互对齐 Pi:
- 顶部标题 / 中间 Transcript / 输入编辑器 / 状态栏 / 命令补全;
- 工具调用可折叠,运行状态实时刷新;
- Slash Commands / 选择器 / Overlay;
- 所有动作都调用 AgentSession → fwcore.Runner,不在这里跑 Agent Loop。
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
import time
from typing import Any

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.css.query import NoMatches
from textual.widgets import Static

from ..commands import BY_NAME
from ..config import ModelConfigError, adapter_for, available_models
from ..runtime import AppRuntime
from ..session import AgentSession
from ..widgets.editor import PromptEditor
from ..widgets.palette import CommandPalette
from ..widgets.screens import (
    ConfirmScreen,
    HelpScreen,
    ResearchScreen,
    SelectItem,
    SelectorScreen,
    ToolsetScreen,
)
from ..widgets.status import (
    HINTS,
    StatusBar,
    handback_label,
    knowledge_kind_label,
    knowledge_status_label,
    model_label,
    response_status_label,
    state_label,
    target_status_label,
)
from ..widgets.transcript import (
    MessageWidget,
    NoticeWidget,
    PendingBar,
    RunBanner,
    ToolWidget,
    TranscriptView,
)


class FwApp(App):
    CSS = """
    Screen { background: #10151C; color: #DCE3EA; }
    #title { height: 1; background: #10151C; color: #8995A5; padding: 0 1; }
    #transcript { height: 1fr; padding: 0 1; background: #10151C;
                 scrollbar-size: 1 1; scrollbar-background: #10151C;
                 scrollbar-color: #2C3643; scrollbar-color-hover: #8995A5;
                 scrollbar-color-active: #83A9D5; }
    .msg { height: auto; margin: 0 0 1 0; padding: 0; }
    .msg-role { color: #8995A5; text-style: bold; }
    .msg.user .msg-role { color: #83A9D5; }
    .msg.assistant .msg-role { color: #89B99D; }
    .msg .msg-body { padding-left: 2; color: #DCE3EA; }
    .thinking { color: #66717F; }
    .thinking.hidden { display: none; }
    .notice { color: #8995A5; margin: 0 0 0 1; }
    .notice.warning { color: #D5B47A; }
    .notice.error { color: #CE8888; }
    .notice.success { color: #89B99D; }
    .banner { margin: 0 0 1 0; color: #83A9D5; }
    .tool { height: auto; margin: 0 0 0 0; padding-left: 1; }
    .tool-header { height: 1; color: #DCE3EA; }
    .tool-body { height: auto; color: #8995A5; padding-left: 3; }
    .tool.ok .tool-header { color: #89B99D; }
    .tool.error .tool-header { color: #CE8888; }
    .tool.cancelled .tool-header { color: #D5B47A; }
    .tool.running .tool-header { color: #83A9D5; }
    #pending { height: auto; max-height: 2; color: #D5B47A; padding: 0 1; }
    StatusBar { height: auto; background: #10151C; color: #8995A5;
                padding: 0 1; text-wrap: wrap; }
    #palette { display: none; height: auto; max-height: 8; border: solid #2C3643;
               background: #19212B; color: #DCE3EA; }
    #editor { height: 3; border: none; background: #19212B; color: #DCE3EA; }
    #hints { height: 1; color: #66717F; padding: 0 1; }
    .dialog { width: 82%; height: 78%; margin: 2 4; padding: 1 2;
              border: solid #2C3643; background: #19212B; }
    .dialog.confirm { width: 58%; height: auto; }
    .dialog-title { text-style: bold; color: #83A9D5; margin-bottom: 1; }
    .dialog-hint { color: #8995A5; margin-top: 1; }
    .dialog-buttons { height: auto; margin-top: 1; }
    .pane { height: 1fr; overflow-y: auto; color: #DCE3EA; }
    #search { margin-bottom: 1; background: #10151C; color: #DCE3EA; }
    #options { height: 1fr; background: #19212B; }
    DataTable { background: #19212B; color: #DCE3EA; }
    DataTable > .datatable--header { background: #19212B; color: #83A9D5; }
    TabbedContent { background: #19212B; }
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
        self._stream_msg: MessageWidget | None = None
        self._stream_tools: dict[str, ToolWidget] = {}
        self._stream_tool_by_index: dict[int, str] = {}
        self._stream_tool_by_call: dict[str, ToolWidget] = {}
        self._stream_args: dict[str, str] = {}
        self._expand_tools = False
        self._show_thinking = False
        self._last_sigint = 0.0
        self._signal_exit = False
        self._last_title: str | None = None
        self._last_pending: tuple[str, ...] | None = None
        self._last_busy: bool | None = None
        self._assistant_rendered = False
        self._stream_error_notified = False
        self._stop_requested = False

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
        counts = self.sess.services.con.execute(
            "SELECT COUNT(*) total, SUM(CASE WHEN status='active' THEN 1 ELSE 0 END) active"
            " FROM targets").fetchone()
        if not counts["total"]:
            self._notice("状态库还没有目标：请先运行 `fw-core init <目标>` 或迁移历史目标。", "warning")
        elif not counts["active"]:
            self._notice("没有进行中的目标：已有目标已收尾或归档；可用 `/target` 查看，或执行 unarchive 恢复。",
                         "warning")
        if self.sess.model_name in ("scripted", "fake"):
            self._notice("演示模式：当前使用本地脚本，不会调用真实模型。", "info")
        # Pi 的 TUI 只在状态变化/输入后 requestRender；这里保留 250ms 事件泵，
        # 但不再用 1s 定时器无条件刷新状态栏（那是终端重绘积压的来源）。
        self.set_interval(0.25, self._tick)
        # 信号处理函数不能直接依赖 Textual 的消息泵；置标志，由事件循环安全退出。
        self.set_interval(0.1, self._check_signal_exit)

    # ---------------- 渲染 ----------------
    def _append(self, widget) -> None:
        self.query_one(TranscriptView).append(widget)

    def _notice(self, text: str, level: str = "info") -> None:
        self._append(NoticeWidget(text, level))

    def request_exit(self) -> None:
        """供 SIGTERM/SIGHUP 等信号处理函数调用（只置标志，不跨线程操作 UI）。"""
        self._signal_exit = True

    def _check_signal_exit(self) -> None:
        if self._signal_exit:
            self._signal_exit = False
            self.exit()

    def _update_title(self) -> None:
        s = self.sess.snapshot()
        t = Text()
        t.append("fw-core", style="bold #8995A5")
        t.append("  ")
        t.append(s.target or "未选择目标", style="bold #83A9D5")
        t.append("  ")
        t.append(state_label(s.state), style="bold #89B99D" if s.state == "running" else "#D5B47A")
        if s.target_status != "active":
            t.append(f"  · {target_status_label(s.target_status)}", style="#D5B47A")
        if self.sess.model_name in ("scripted", "fake"):
            t.append("  · 演示模式", style="#D5B47A")
        if s.busy:
            t.append("  · 正在处理", style="#D5B47A")
        if t.plain == self._last_title:
            return
        self._last_title = t.plain
        self.query_one("#title", Static).update(t)

    def _update_status(self) -> None:
        try:
            s = self.sess.snapshot()
        except Exception as e:
            self.query_one(StatusBar).update(Text(f"状态读取失败: {e}", style="#CE8888"))
            return
        try:
            self.query_one(StatusBar).update_snapshot(s)
            self.query_one(PendingBar).update_pending(s.pending)
            self._last_pending = tuple(s.pending)
            self._last_busy = s.busy
            self._update_title()
        except NoMatches:
            # interval 可能在 App 退出/卸载后仍触发一次；不能把退出变成异常。
            return

    def _load_transcript(self) -> None:
        tv = self.query_one(TranscriptView)
        tv.remove_children()
        self._tool_widgets.clear()
        self._reset_stream()
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
        self._take_pending_tool(call)
        w = self._tool_widgets.get(key)
        if w is None:
            w = ToolWidget(call, expanded=self._expand_tools)
            self._tool_widgets[key] = w
            self._append(w)
        else:
            w.update_call(call)

    def _tick(self) -> None:
        events = self.sess.drain_events()
        for ev in events:
            self._handle_event(ev)
        changed = bool(events)
        for call in self.sess.tool_updates():
            self._upsert_tool(call)
            changed = True
        pending = tuple(self.sess.pending)
        if pending != self._last_pending:
            self._last_pending = pending
            changed = True
        busy = self.sess.busy
        if busy != self._last_busy:
            self._last_busy = busy
            changed = True
        # 仿 Pi：只有事件/状态真的变化才刷新状态组件；空闲时不做 DB 快照和重绘。
        if changed:
            self._update_status()

    def _reset_stream(self) -> None:
        self._assistant_rendered = False
        self._stream_error_notified = False
        self._stream_msg = None
        self._stream_tools.clear()
        self._stream_tool_by_index.clear()
        self._stream_tool_by_call.clear()
        self._stream_args.clear()

    def _stream_tool_event(self, data: dict) -> None:
        try:
            idx = int(data.get("index") or 0)
        except (TypeError, ValueError):
            idx = 0
        key = self._stream_tool_by_index.get(idx)
        if key is None:
            call_id = str(data.get("call_id") or "")
            key = "stream:" + (call_id or str(idx))
            call = {"id": key, "tool": data.get("name") or "tool", "args": "{}",
                    "status": "pending", "note": ""}
            w = ToolWidget(call, expanded=self._expand_tools)
            self._stream_tools[key] = w
            self._stream_tool_by_index[idx] = key
            if call_id:
                self._stream_tool_by_call[call_id] = w
            self._append(w)
        w = self._stream_tools.get(key)
        if w is None:
            return
        frag = data.get("text") or ""
        if frag:
            self._stream_args[key] = self._stream_args.get(key, "") + frag
            w.update_call({**w.call_data, "args": self._stream_args[key]})

    def _take_pending_tool(self, call: dict) -> ToolWidget | None:
        """工具开始真正执行时,把流式预览组件换成权威 ToolWidget。"""
        key = str(call.get("call_key") or "")
        w = self._stream_tool_by_call.pop(key, None) if key else None
        if w is None:
            for _k, cand in list(self._stream_tools.items()):
                if cand.call_data.get("tool") == call.get("tool"):
                    w = cand
                    break
        if w is None:
            return None
        self._stream_tools = {k: v for k, v in self._stream_tools.items() if v is not w}
        self._stream_tool_by_index = {k: v for k, v in self._stream_tool_by_index.items()
                                      if self._stream_tools.get(v) is not None}
        for cid, cw in list(self._stream_tool_by_call.items()):
            if cw is w:
                self._stream_tool_by_call.pop(cid, None)
        w.remove()
        return w

    def _handle_event(self, ev) -> None:
        if ev.kind == "assistant_start":
            # 不在这里创建空 assistant 组件:模型失败时 start 已到、内容没到，
            # 会留下一个空的 “fw-core” 块。首个 delta/done 到达时再挂载。
            pass
        elif ev.kind == "assistant_delta":
            text = ev.data.get("text") or ev.text
            if not text:
                return
            if self._stream_msg is None:
                self._stream_msg = MessageWidget("assistant", "")
                self._append(self._stream_msg)
            self._stream_msg.append_text(text)
            self._assistant_rendered = True
        elif ev.kind == "reasoning_delta":
            if self._stream_msg is None:
                self._stream_msg = MessageWidget("assistant", "")
                self._append(self._stream_msg)
            self._stream_msg.append_reasoning(ev.data.get("text") or ev.text)
        elif ev.kind == "assistant_done":
            text = ev.data.get("text") or ev.text or ""
            if self._stream_msg is None and text:
                self._stream_msg = MessageWidget("assistant", text)
                self._append(self._stream_msg)
            if self._stream_msg is not None:
                self._stream_msg.finalize(text)
            if text:
                self._assistant_rendered = True
            status = ev.data.get("status") or "ok"
            if status != "ok" and not (self._stop_requested and status == "cancelled"):
                self._notice(f"回复未完整生成：{response_status_label(status)}", "warning")
            self._stream_msg = None
        elif ev.kind == "assistant_error":
            partial = ev.data.get("text") or ""
            if self._stream_msg is not None:
                if partial or getattr(self._stream_msg, "_text", "").strip():
                    self._stream_msg.finalize(partial)
                    self._assistant_rendered = True
                else:
                    self._stream_msg.remove()
            self._notice("模型输出中断：" + str(ev.data.get("error") or "")[:300], "error")
            self._stream_error_notified = True
            self._stream_msg = None
        elif ev.kind in ("toolcall_start", "toolcall_delta"):
            self._stream_tool_event(ev.data)
        elif ev.kind == "turn_done":
            # 流式 adapter 走 assistant_done；非流式 adapter 只有 RunResult.final。
            # 后者不补渲染的话，用户会看到“已完成”但没有任何回答。
            final = (ev.data.get("final") or "").strip()
            if final and not self._assistant_rendered:
                self._stream_msg = MessageWidget("assistant", final)
                self._append(self._stream_msg)
                self._stream_msg = None
                self._assistant_rendered = True
            state = ev.data.get("state")
            hb = ev.data.get("handback")
            blockers = ev.data.get("blockers") or []
            if state and state != "finished":
                reason = f"：{handback_label(hb)}" if hb else ""
                # handback 通常带 "model_error: <细节>"；只显示“模型调用失败”
                # 会让用户不知道发生了什么，保留可操作的错误尾巴。
                if hb and ":" in str(hb):
                    detail = str(hb).split(":", 1)[1].strip()
                    if detail and detail != handback_label(hb):
                        reason += f"（{detail[:300]}）"
                label = state_label(state)
                prefix = "本轮" if label.startswith("已") else "本轮已"
                # assistant_error 已经显示过同一条流错误时，不再重复一条泛化通知。
                # 用户主动停止时，请求提示已经显示过一次；这里不再重复“已暂停”。
                is_user_stop = str(hb or "").startswith("user_stop")
                if not (self._stream_error_notified and str(hb or "").startswith("model_error")):
                    if not (self._stop_requested and is_user_stop):
                        self._notice(f"{prefix}{label}{reason}", "warning")
            elif hb in ("awaiting_user_close", "closed"):
                self._notice("收尾条件已满足，等待你确认收尾" if hb == "awaiting_user_close"
                             else "已确认收尾", "success")
            if blockers:
                self._notice(f"收尾检查还有 {len(blockers)} 项未通过；在研究面板可查看详情。",
                             "info")
            # 保持到下一次 busy/新提交再清；cancelled 事件可能晚于 turn_done，
            # 立刻清会重新放出重复的“已停止”提示。
            self._update_status()
        elif ev.kind == "error":
            self._notice(ev.text, "error")
        elif ev.kind == "cancelled":
            if not self._stop_requested:
                self._notice(f"已停止: {ev.text}", "warning")
            back = self.sess.take_returned()
            if back:
                self.query_one(PromptEditor).text = "\n".join(back)
        elif ev.kind == "session.new":
            self.query_one(TranscriptView).remove_children()
            self._tool_widgets.clear()
            self._append(RunBanner("新研究会话：下一条消息会创建新的研究会话"))
            self._update_status()
        elif ev.kind in ("session.resumed", "session.target"):
            self._load_transcript()
            data = ev.data or {}
            run = data.get("run") or self.sess.run_id
            self._append(RunBanner(
                f"已切换目标：{data.get('target') or self.sess.target}"
                + (f" · 研究会话 {run}" if run else " · 尚无研究会话")))
            self._update_status()
        elif ev.kind == "session.model":
            self._notice(f"模型已切换：{model_label(ev.data.get('model') or self.sess.model_name)}")
            if (ev.data.get("model") or self.sess.model_name) in ("scripted", "fake"):
                self._notice("演示模式：本地脚本，不会调用真实模型。", "info")
            self._update_status()
        elif ev.kind == "busy":
            self._assistant_rendered = False
            self._stream_error_notified = False
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
        self._append(MessageWidget("user", text + "  （已排队）"))
        self.query_one(PromptEditor).remember(text)
        self.query_one(PromptEditor).text = ""
        self._stop_requested = False
        self.sess.submit(text)
        self._update_status()

    def on_prompt_editor_escape(self, msg: PromptEditor.Escape) -> None:
        pal = self.query_one(CommandPalette)
        if pal.is_open:
            pal.hide()
            return
        if self.sess.busy:
            self._request_stop()
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
            self._notice("没有目标：请先选择目标，或运行 `fw-core init <目标>`。", "warning")
            return
        self._append(MessageWidget("user", text))
        self._stop_requested = False
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
        handler = getattr(self, f"_cmd_{name.replace('-', '_')}", None)
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

    def _cmd_tool_setup(self, arg: str) -> None:
        """用户显式触发工具管理 Skill:异步检查,避免工具探针冻结 TUI。"""
        self._notice("正在检查默认工具集…")
        self.run_worker(self._tool_setup_worker(), exclusive=True)

    async def _tool_setup_worker(self) -> None:
        from fwcore.toolset import check_toolset
        try:
            rep = await asyncio.to_thread(check_toolset, self.sess.services.cfg.root)
        except Exception as e:  # noqa: BLE001 - UI 必须把异常显示出来，而不是退出
            self._notice(f"工具集检查失败: {e}", "error")
            return
        missing = [x for x in rep["items"]
                   if x["required"] and x["status"] != "ready"]
        if not missing:
            self._notice("工具都已就绪，无需处理。使用 /tools 查看详情。")
            return
        if not self.sess.target:
            self._notice("请先选择目标，再检查并补齐工具。", "warning")
            return
        self._notice("发现未就绪工具：" + "、".join(x["label"] for x in missing), "warning")
        task = "使用 tool-management Skill 检查并补齐默认工具，完成后继续当前研究。"
        self._append(MessageWidget("user", task))
        self.sess.submit(task)



    def _cmd_tools(self, arg: str) -> None:
        self.push_screen(ToolsetScreen(self.sess.services.cfg.root))

    def _cmd_skills(self, arg: str) -> None:
        from fwcore.skills import catalog
        rows = catalog(self.sess.services.cfg.root)
        if not rows:
            self._notice("还没有可用技能（.agents/skills/*/SKILL.md）。", "warning")
            return
        lines = ["可用技能（需要时再读取正文）", ""]
        for r in rows:
            lines.append(f"- {r['name']}: {r['description']}")
        self._notice("\n".join(lines))

    def _cmd_knowledge(self, arg: str) -> None:
        from fwcore.knowledge import search, stats
        con = self.sess.services.con
        if not arg.strip():
            self._notice("知识库统计: " + str(stats(con)))
            return
        try:
            rows = search(con, arg, limit=10)
        except ValueError as e:
            self._notice(f"检索失败: {e}", "error")
            return
        if not rows:
            self._notice(f"未找到知识: {arg}", "warning")
            return
        lines = [f"知识检索: {arg}", ""]
        for r in rows:
            lines.append(f"- {r['id']} [{knowledge_kind_label(r['kind'])}/"
                         f"{knowledge_status_label(r['status'])}] {r['title']}")
        self._notice("\n".join(lines))

    def _cmd_budget(self, arg: str) -> None:
        s = self.sess.snapshot()
        lim = s.limits or {}
        tok = s.tokens or {}
        ctx = s.context or {}
        t = Text()
        t.append("预算与用量\n", style="bold")
        t.append(f"- 模型调用 {s.calls}/{lim.get('max_model_calls')} · "
                 f"工具调用 {s.tools} · 用时 {s.elapsed:.0f}s/{lim.get('max_seconds')}\n")
        t.append(f"- Token 输入 {tok.get('prompt')} · 输出 {tok.get('completion')} · "
                 f"合计 {tok.get('total')}\n")
        prompt = int(tok.get("prompt") or 0)
        cached = int(tok.get("cached") or 0)
        if prompt:
            t.append(f"- 缓存命中率 {min(100, cached / prompt * 100):.0f}% ({cached}/{prompt})\n")
        if ctx.get("window"):
            t.append(f"- 上下文 {ctx.get('last_prompt') or ctx.get('estimated')}/{ctx['window']}")
            if ctx.get("percent") is not None:
                t.append(f" ({ctx['percent']}%)")
            t.append("\n")
        else:
            t.append("- 上下文窗口未配置（可选：state/model.json 的 context_tokens）\n")
        if s.budget.get("last_limit"):
            t.append(f"- 最近触发：{s.budget['last_limit']}\n", style="yellow")
        self._notice(t.plain)

    def _cmd_model(self, arg: str) -> None:
        if arg:
            # 先构造适配器验证配置；失败不能先改 UI 显示，下一轮才报错。
            try:
                adapter_for(self.runtime.cfg, arg)
            except ModelConfigError as e:
                self._notice(f"模型不可用：{e}；当前模型未改变。", "error")
                return
            if not self.sess.set_model(arg):
                self._notice("模型切换失败；当前模型未改变。", "error")
            return
        items = [SelectItem(v, v, d) for v, d in available_models(self.runtime.cfg)]
        if not items:
            self._notice("没有可用模型", "warning")
            return
        def choose(value: str | None) -> None:
            if value and not self.sess.set_model(value):
                self._notice("模型切换失败；当前模型未改变。", "error")
        self.push_screen(SelectorScreen("选择模型", items), choose)

    def _cmd_target(self, arg: str) -> None:
        if arg:
            self.sess.set_target(arg)
            self._load_transcript()
            return
        rows = [dict(r) for r in self.sess.services.con.execute(
            "SELECT name,status FROM targets ORDER BY name").fetchall()]
        if not rows:
            self._notice("还没有目标：请先运行 `fw-core init <目标>`。", "warning")
            return
        items = [SelectItem(r["name"], r["name"], target_status_label(r["status"]))
                 for r in rows]
        self.push_screen(SelectorScreen("选择目标", items), self._switch_target)

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
            self._notice("没有历史研究会话。", "warning")
            return
        items = [SelectItem(r["id"], f"{r['id']} · {r['target']}",
                            f"{state_label(r['state'])} · {r['started_at'][:19]} · "
                            f"{(r['goal'] or '')[:80]}")
                 for r in rows]
        self.push_screen(SelectorScreen("恢复历史研究会话", items), self._resume_selected)

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
            self._notice("当前没有可继续的研究会话（先发一条消息）。", "warning")
            return
        if self.sess.busy:
            self._notice("当前一轮仍在执行，暂时不能继续；请等它结束或先按 Esc 停止。", "warning")
            return
        self._stop_requested = False
        if self.sess.continue_run():
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
        # 流式工具预览尚未进入 _tool_widgets;与已执行工具一起切换。
        for w in (*self._tool_widgets.values(), *self._stream_tools.values()):
            w.body.display = self._expand_tools
        self._notice("工具输出: " + ("展开" if self._expand_tools else "折叠"))

    def _request_stop(self) -> None:
        if self._stop_requested:
            return
        self._stop_requested = True
        self.sess.cancel()
        self._notice("正在停止…", "warning")

    def action_interrupt(self) -> None:
        if self.sess.busy:
            self._request_stop()
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
            app.request_exit()

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
