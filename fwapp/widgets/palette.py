"""斜杠命令补全(Pi 的 autocomplete/slash command 菜单)。

焦点始终留在编辑器,补全列表只做展示与选中;按键由 Interactive Mode 转发。
"""

from __future__ import annotations

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from ..commands import COMMANDS, Command


def _option(cmd: Command) -> Option:
    t = Text()
    t.append("/" + cmd.name, style="bold")
    if cmd.arg_hint:
        t.append(" " + cmd.arg_hint, style="dim")
    t.append("  " + cmd.description, style="dim")
    return Option(t, id=cmd.name)


class CommandPalette(OptionList):
    can_focus = False

    def __init__(self, **kw):
        super().__init__(id="palette", **kw)
        self.display = False
        self._items: list[Command] = []

    def show_for(self, text: str) -> bool:
        """按当前输入过滤;返回是否可见。"""
        if not text.startswith("/") or " " in text:
            self.hide()
            return False
        prefix = text[1:].lower()
        items = [c for c in COMMANDS if c.name.startswith(prefix)]
        self._items = items
        self.clear_options()
        for c in items:
            self.add_option(_option(c))
        self.display = bool(items)
        if items:
            self.highlighted = 0
        return bool(items)

    def hide(self) -> None:
        self.display = False
        self._items = []

    @property
    def is_open(self) -> bool:
        return bool(self.display)

    def move(self, delta: int) -> None:
        if not self._items:
            return
        cur = self.highlighted or 0
        self.highlighted = max(0, min(len(self._items) - 1, cur + delta))

    def current(self) -> Command | None:
        if not self._items:
            return None
        idx = self.highlighted or 0
        return self._items[max(0, min(len(self._items) - 1, idx))]
