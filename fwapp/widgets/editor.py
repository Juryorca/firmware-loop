"""多行输入编辑器:历史、Enter 提交 / Shift+Enter 换行、Esc 停止、Ctrl+C 取消。

基于 Textual TextArea(与 Pi 的 Editor 组件职责一致),不自己实现 ANSI 输入。
"""

from __future__ import annotations

from collections.abc import Callable

from textual import events
from textual.message import Message
from textual.widgets import TextArea


class PromptEditor(TextArea):
    class Submitted(Message):
        def __init__(self, text: str):
            self.text = text
            super().__init__()

    class Queued(Message):
        def __init__(self, text: str):
            self.text = text
            super().__init__()

    class Escape(Message):
        pass

    class Interrupt(Message):
        pass

    class CompletionKey(Message):
        def __init__(self, key: str):
            self.key = key
            super().__init__()

    def __init__(self, **kw):
        super().__init__("", id="editor", soft_wrap=True, tab_behavior="indent",
                         show_line_numbers=False, placeholder="问点什么,或用 / 打开命令…", **kw)
        # Textual 用重绘模拟光标闪烁；在 WSL/终端切换后会造成无意义的重绘积压。
        # 光标保持常显，输入行为不变。
        self.cursor_blink = False
        self.prompt_history: list[str] = []
        self._hidx = 0
        self.completion_active: Callable[[], bool] = lambda: False

    def remember(self, text: str) -> None:
        text = (text or "").strip()
        if text and (not self.prompt_history or self.prompt_history[-1] != text):
            self.prompt_history.append(text)
        self._hidx = len(self.prompt_history)

    def _history(self, delta: int) -> None:
        if not self.prompt_history:
            return
        self._hidx = max(0, min(len(self.prompt_history), self._hidx + delta))
        self.text = self.prompt_history[self._hidx] if self._hidx < len(self.prompt_history) else ""

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if self.completion_active() and key in ("up", "down", "enter", "tab", "escape"):
            self.post_message(self.CompletionKey(key))
            event.prevent_default()
            event.stop()
            return
        if key in ("shift+enter", "ctrl+j"):
            self.insert("\n")
            event.prevent_default()
            event.stop()
            return
        if key == "enter":
            self.post_message(self.Submitted(self.text))
            event.prevent_default()
            event.stop()
            return
        if key == "alt+enter":
            self.post_message(self.Queued(self.text))
            event.prevent_default()
            event.stop()
            return
        if key == "escape":
            self.post_message(self.Escape())
            event.prevent_default()
            event.stop()
            return
        if key == "ctrl+c":
            self.post_message(self.Interrupt())
            event.prevent_default()
            event.stop()
            return
        if key == "up" and "\n" not in self.text:
            self._history(-1)
            event.prevent_default()
            event.stop()
            return
        if key == "down" and "\n" not in self.text:
            self._history(1)
            event.prevent_default()
            event.stop()
            return
        if key == "ctrl+p":
            self._history(-1)
            event.prevent_default()
            event.stop()
        elif key == "ctrl+n":
            self._history(1)
            event.prevent_default()
            event.stop()
