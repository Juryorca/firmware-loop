"""Transcript 组件:消息、工具调用(可折叠)、通知、运行横幅。

对应 Pi 的 chatContainer + UserMessage/AssistantMessage/ToolExecution 组件。
所有数据都来自 Session(权威 SQLite),这里只负责展示与交互。
"""

from __future__ import annotations

import json

from rich.markdown import Markdown as RichMarkdown
from rich.text import Text
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Static

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
    """可滚动的对话区;新内容只在用户已在底部时自动跟随。"""

    def at_bottom(self) -> bool:
        try:
            return self.scroll_offset.y >= self.max_scroll_y - 2
        except Exception:
            return True

    def append(self, widget) -> None:
        stick = self.at_bottom()
        self.mount(widget)
        if stick:
            self.scroll_end(animate=False)


class MessageWidget(Vertical):
    def __init__(self, role: str, text: str, meta: dict | None = None):
        meta = meta or {}
        label = {"user": "你", "assistant": "fw-core", "notice": "系统"}.get(role, role)
        head = Static(Text(label, style="bold"), classes="msg-role")
        body: Static
        if role == "assistant" and text:
            body = Static(RichMarkdown(text), classes="msg-body")
        elif role == "user":
            body = Static(Text(text, style="bold"), classes="msg-body")
        else:
            body = Static(Text(text or "(empty)"), classes="msg-body")
        children = [head, body]
        self.role = role
        self.body = body
        self._text = text or ""
        self._reasoning = ""
        self._thinking_widget = None
        reasoning = (meta.get("reasoning") or "").strip()
        if reasoning:
            # thinking 默认折叠;绝不作为普通 assistant 正文展示
            self._reasoning = reasoning
            self._thinking_widget = Static(Text(reasoning, style="dim italic"),
                                           classes="thinking hidden")
            children.append(self._thinking_widget)
        super().__init__(*children, classes=f"msg {role}")

    def append_text(self, delta: str) -> None:
        if not delta:
            return
        self._text += delta
        self.body.update(Text(self._text))

    def append_reasoning(self, delta: str) -> None:
        if not delta:
            return
        self._reasoning += delta
        if self._thinking_widget is None:
            self._thinking_widget = Static(Text("", style="dim italic"),
                                           classes="thinking hidden")
            self.mount(self._thinking_widget)
        self._thinking_widget.update(Text(self._reasoning, style="dim italic"))

    def finalize(self, text: str | None = None) -> None:
        if text is not None:
            self._text = text
        if self.role == "assistant" and self._text:
            self.body.update(RichMarkdown(self._text))
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
        self.body.update(self._body())

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
        out = _stdout_excerpt(c.get("stdout_path"))
        if out:
            t.append("标准输出\n", style="bold #8995A5")
            t.append(out + "\n")
        for label, key in (("退出码", "exit_code"), ("输出文件", "stdout_path"), ("结果文件", "result_path")):
            if c.get(key) not in (None, ""):
                t.append(f"{label}: {c[key]}\n", style="dim")
        return t

    def update_call(self, call: dict) -> None:
        self.call_data = dict(call)
        self._refresh()

    def toggle(self) -> None:
        self.body.display = not self.body.display


class PendingBar(Static):
    def __init__(self, **kw):
        super().__init__("", classes="pending-bar", **kw)
        self.display = False

    def update_pending(self, pending: list[str]) -> None:
        if not pending:
            self.display = False
            self.update("")
            return
        self.display = True
        t = Text("已排队（Alt+↑ 取回）：", style="dim")
        t.append(" | ".join(_short(x, 60) for x in pending), style="italic")
        self.update(t)
