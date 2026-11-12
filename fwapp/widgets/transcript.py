"""Transcript 组件:消息、工具调用(可折叠)、通知、运行横幅。

对应 Pi 的 chatContainer + UserMessage/AssistantMessage/ToolExecution 组件。
所有数据都来自 Session(权威 SQLite),这里只负责展示与交互。
"""

from __future__ import annotations

import contextlib
import json
import os
from collections.abc import Callable

from rich.text import Text
from textual import events
from textual.containers import Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import Markdown, Static

STATUS_ICON = {
    "pending": "·", "running": "⏳", "ok": "✓", "error": "✗",
    "timeout": "⏱", "cancelled": "⊘",
}
STATUS_CLASS = {
    "pending": "pending", "running": "running", "ok": "ok", "error": "error",
    "timeout": "timeout", "cancelled": "cancelled",
}
STATUS_STYLE = {
    "pending": "#8995A5", "running": "#83A9D5", "ok": "#89B99D",
    "error": "#CE8888", "timeout": "#D5B47A", "cancelled": "#D5B47A",
}


def _short(value, limit: int = 90) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


class TranscriptView(VerticalScroll):
    """可滚动的对话区;仅当用户已在底部时自动跟随新内容。

    点击对话区不应把键盘焦点从编辑器抢走；滚动条仍可用鼠标。
    """

    can_focus = False
    # Keep the widget tree bounded. The full transcript remains in SQLite;
    # this only limits what the renderer has to lay out at once.
    MAX_WIDGETS: int = 300
    on_drop: Callable[[Widget], None] | None = None

    def __init__(self, *children, **kw):
        self._trim_scheduled = False
        self._scroll_scheduled = False
        super().__init__(*children, **kw)

    def at_bottom(self) -> bool:
        try:
            return self.scroll_offset.y >= self.max_scroll_y - 2
        except Exception:  # noqa: BLE001
            return True

    def append(self, widget, *, force_follow: bool = False) -> None:
        # `mount()` lays out asynchronously. Scrolling immediately uses the old
        # max_scroll_y, so the newly added notice/message can land below the
        # visible area. Re-check after the next refresh.
        #
        # Ordinary messages follow only when the reader is already at the
        # bottom; explicit command output such as /mcp may force the follow.
        stick = force_follow or self.at_bottom()
        self.mount(widget)
        if not self._trim_scheduled:
            self._trim_scheduled = True
            self.call_after_refresh(self._trim_widgets)
        if stick:
            self.follow_end()

    def _trim_widgets(self) -> None:
        self._trim_scheduled = False
        excess = len(self.children) - self.MAX_WIDGETS
        for _ in range(max(0, excess)):
            if not self.children:
                break
            dropped = self.children[0]
            dropped.remove()
            if self.on_drop is not None:
                self.on_drop(dropped)
        if len(self.children) > self.MAX_WIDGETS and not self._trim_scheduled:
            self._trim_scheduled = True
            self.call_after_refresh(self._trim_widgets)

    def follow_end(self) -> None:
        """Schedule a scroll after the next layout pass.

        The same path is used for appended widgets, streaming Markdown updates,
        tool body updates, and initial transcript loading.  Calling
        ``scroll_end`` synchronously during mount sees the old max_scroll_y.
        """
        if self.is_attached and not self._scroll_scheduled:
            self._scroll_scheduled = True
            self.call_after_refresh(self._scroll_to_end)

    def _scroll_to_end(self) -> None:
        self._scroll_scheduled = False
        if self.is_attached:
            self.scroll_end(animate=False)




def _find_transcript(widget) -> TranscriptView | None:
    node = widget.parent
    while node is not None:
        if isinstance(node, TranscriptView):
            return node
        node = node.parent
    return None


class ThinkingBlock(Vertical):
    """可点击折叠的 thinking 链；默认展开，点击标题/正文切换。"""

    can_focus = False

    def __init__(self, text: str = "", **kw):
        self._text = text or ""
        self._rich = Text(self._text, style="dim italic")
        self._expanded = True
        self.header = Static(classes="thinking-head")
        self.body = Static(classes="thinking-body")
        super().__init__(self.header, self.body, classes="thinking-block", **kw)
        self._refresh()

    def _refresh(self) -> None:
        n = len(self._text)
        action = "折叠" if self._expanded else "展开"
        self.header.update(Text(f"思考过程 · {n} 字 · 点击{action}", style="dim italic"))
        self.body.display = self._expanded
        if self._expanded:
            self.body.update(self._rich)

    def append_text(self, delta: str) -> None:
        if not delta:
            return
        transcript = _find_transcript(self)
        stick = bool(transcript and transcript.at_bottom())
        self._text += delta
        self._rich.append(delta)
        self._refresh()
        if stick and transcript is not None:
            transcript.follow_end()

    def toggle(self) -> None:
        self._expanded = not self._expanded
        self._refresh()

    def on_click(self, event: events.Click) -> None:
        self.toggle()
        event.stop()

class MessageWidget(Vertical):
    def _follow_transcript(self, stick: bool) -> None:
        if not stick:
            return
        transcript = _find_transcript(self)
        if transcript is not None:
            transcript.follow_end()

    def __init__(self, role: str, text: str, meta: dict | None = None,
                 *, streaming: bool = False):
        meta = meta or {}
        label = {"user": "你", "assistant": "fw-core", "notice": "系统"}.get(role, role)
        head = Static(Text(label, style="bold"), classes="msg-role")
        body: Static | Markdown
        # Textual Markdown is selectable, but its append path parses/mounts
        # blocks asynchronously.  Do not use it for token-rate streaming: a
        # plain Static is cheap, then finalize swaps in one Markdown widget.
        self._streaming = bool(streaming and role == "assistant")
        if self._streaming:
            body = Static(Text(text or ""), classes="msg-body")
        elif role == "assistant":
            body = Markdown(text or "", classes="msg-body")
        elif role == "user":
            body = Static(Text(text, style="bold"), classes="msg-body")
        else:
            body = Static(Text(text or "(empty)"), classes="msg-body")
        self.role = role
        self.body = body
        self._text = text or ""
        self._stream_rich = Text(self._text) if self._streaming else None
        self._reasoning = ""
        self._thinking_widget: ThinkingBlock | None = None
        children: list[Widget] = [head]
        reasoning = (meta.get("reasoning") or "").strip()
        if reasoning:
            self._reasoning = reasoning
            self._thinking_widget = ThinkingBlock(reasoning)
            children.append(self._thinking_widget)
        children.append(body)
        super().__init__(*children, classes=f"msg {role}")

    def append_text(self, delta: str) -> None:
        if not delta:
            return
        transcript = _find_transcript(self)
        stick = bool(transcript and transcript.at_bottom())
        self._text += delta
        if self._stream_rich is not None:
            self._stream_rich.append(delta)
            if isinstance(self.body, Static):
                self.body.update(self._stream_rich)
            else:
                self.body.update(self._text)
        elif isinstance(self.body, Markdown):
            self.body.append(delta)
        else:
            self.body.update(Text(self._text))
        self._follow_transcript(stick)

    def append_reasoning(self, delta: str) -> None:
        if not delta:
            return
        transcript = _find_transcript(self)
        stick = bool(transcript and transcript.at_bottom())
        self._reasoning += delta
        if self._thinking_widget is None:
            self._thinking_widget = ThinkingBlock(self._reasoning)
            self.mount(self._thinking_widget, before=self.body)
        else:
            self._thinking_widget.append_text(delta)
        self._follow_transcript(stick)

    def finalize(self, text: str | None = None) -> None:
        if text is not None:
            self._text = text
        if self._streaming:
            old = self.body
            final = Markdown(self._text, classes="msg-body")
            self.body = final
            self._streaming = False
            self._stream_rich = None
            self.mount(final)
            old.display = False
            # Keep the old Static mounted-hidden until Textual has mounted the
            # Markdown; removing it synchronously can race the mount.
            with contextlib.suppress(Exception):
                old.remove()
            return
        if isinstance(self.body, Markdown):
            self.body.update(self._text)
        else:
            self.body.update(Text(self._text))


class NoticeWidget(Static):
    MAX_DETAIL_CHARS = 1200

    def __init__(self, text: str, level: str = "info"):
        icons = {"info": "·", "warning": "!", "error": "✗", "success": "✓"}
        # 长错误/警告不把主聊天区刷满；详细信息仍可从工具记录/状态页追溯。
        if level in ("warning", "error") and len(text) > self.MAX_DETAIL_CHARS:
            text = text[:self.MAX_DETAIL_CHARS] + "…（已截断）"
        super().__init__(Text(f"{icons.get(level, '·')} {text}", style="dim" if level == "info" else ""),
                         classes=f"notice {level}")


class RunBanner(Static):
    def __init__(self, text: str, level: str = "info"):
        super().__init__(Text(text, style="bold"), classes=f"banner {level}")


def _stdout_excerpt(path, limit: int = 4000) -> str:
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = f.read(limit)
    except OSError:
        return ""
    return data


class ToolWidget(Vertical):
    """一条工具调用:标题行常显,详细参数/结果可折叠。"""

    def __init__(self, call: dict, expanded: bool = False):
        self.call_data = dict(call)
        self.header = Static(classes="tool-header")
        self.body = Static(classes="tool-body")
        self._body_built = False
        self._body_sig: tuple[object, ...] | None = None
        self._stdout_key: tuple[str, int, int] | None = None
        self._stdout_text = ""
        self.body.display = expanded
        super().__init__(self.header, self.body, classes="tool")
        self._refresh()

    @property
    def key_id(self) -> str:
        return str(self.call_data.get("id") or "")

    def _refresh(self) -> None:
        c = self.call_data
        status = c.get("status") or "pending"
        icon = STATUS_ICON.get(status, "·")
        name = c.get("tool") or "?"
        args = c.get("args")
        try:
            a = json.loads(args) if isinstance(args, str) and args else (args or {})
        except (TypeError, ValueError):
            a = {"raw": args}
        hint = ""
        for k in ("command", "path", "url", "target", "name", "cmd", "query", "pattern"):
            if isinstance(a, dict) and a.get(k):
                hint = _short(a[k], 78)
                break
        if not hint and isinstance(a, dict):
            hint = _short(a)
        dur = c.get("duration_ms")
        dur_s = f" · {dur}ms" if dur is not None else ""
        head = Text()
        head.append(f"{icon} ", style=STATUS_STYLE.get(status, "dim"))
        head.append(name, style="bold")
        if hint:
            head.append("  " + hint, style="#8995A5")
        head.append(dur_s, style="#66717F")
        self.header.update(head)
        for name_cls in set(STATUS_CLASS.values()):
            self.remove_class(name_cls)
        self.add_class(STATUS_CLASS.get(status, "pending"))
        if self.body.display:
            self._render_body()
        else:
            self._body_built = False

    def _render_body(self) -> None:
        c = self.call_data
        sig = (c.get("args"), c.get("note"), c.get("exit_code"),
               c.get("stdout_path"), c.get("result_path"))
        if (self._body_built and sig == self._body_sig
                and c.get("status") in ("ok", "error", "timeout", "cancelled")):
            return
        self._body_sig = sig
        self.body.update(self._body())
        self._body_built = True

    def _cached_stdout(self, path) -> str:
        if not path:
            return ""
        try:
            st = os.stat(path)
        except OSError:
            return ""
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key != self._stdout_key:
            self._stdout_key = key
            self._stdout_text = _stdout_excerpt(path)
        return self._stdout_text

    def _body(self) -> Text:
        c = self.call_data
        t = Text()
        args = c.get("args")
        try:
            pretty = json.dumps(json.loads(args), ensure_ascii=False, indent=1) if isinstance(args, str) and args else (args or "")
        except (TypeError, ValueError):
            pretty = args or ""
        t.append("参数\n", style="bold")
        t.append(str(pretty)[:3000] + "\n")
        if c.get("id"):
            t.append(f"工具 ID: {c['id']}\n", style="dim")
        if c.get("note"):
            t.append("结果\n", style="bold #8995A5")
            t.append(str(c["note"])[:4000] + "\n")
        out = self._cached_stdout(c.get("stdout_path"))
        if out:
            t.append("标准输出\n", style="bold #8995A5")
            t.append(out + "\n")
        for label, key in (("退出码", "exit_code"), ("输出文件", "stdout_path"), ("结果文件", "result_path")):
            if c.get(key) not in (None, ""):
                t.append(f"{label}: {c[key]}\n", style="dim")
        return t

    def update_call(self, call: dict) -> None:
        transcript = _find_transcript(self)
        stick = bool(transcript and transcript.at_bottom())
        self.call_data = dict(call)
        self._refresh()
        if stick and transcript is not None:
            transcript.follow_end()

    def set_expanded(self, expanded: bool) -> None:
        expanded = bool(expanded)
        self.body.display = expanded
        if expanded:
            if not self._body_built:
                self._render_body()
        else:
            self._body_built = False

    def toggle(self) -> None:
        self.set_expanded(not self.body.display)


class PendingBar(Static):
    def __init__(self, **kw):
        super().__init__("", classes="pending-bar", **kw)
        self.display = False
        self._last_pending: tuple[str, ...] | None = None

    def update_pending(self, pending: list[str]) -> None:
        key = tuple(pending or ())
        if key == self._last_pending:
            return
        self._last_pending = key
        if not key:
            self.display = False
            self.update("")
            return
        self.display = True
        t = Text("已排队（Alt+↑ 取回）：", style="dim")
        t.append(" | ".join(_short(x, 60) for x in key), style="italic")
        self.update(t)
