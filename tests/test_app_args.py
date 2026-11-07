"""应用层入口/模式选择(不启动 TUI、不调用模型)。"""

from __future__ import annotations

import sys

from fwapp.args import AppOptions, parse, resolve_mode
from fwcore.cli import _app_invocation


def test_parse_tui_flag_and_target():
    opts, _ = parse(["tui", "--target", "RouterOS", "--model", "scripted"])
    assert opts.tui is True
    assert opts.target == "RouterOS"
    assert opts.model == "scripted"


def test_parse_print_message():
    opts, _ = parse(["-p", "hello", "world", "--model", "scripted"])
    assert opts.print_mode is True
    assert opts.message == "hello world"


def test_resolve_mode_non_tty_is_print(monkeypatch):
    class NoTTY:
        def isatty(self):
            return False

    monkeypatch.setattr(sys, "stdin", NoTTY())
    monkeypatch.setattr(sys, "stdout", NoTTY())
    assert resolve_mode(AppOptions()) == "print"
    assert resolve_mode(AppOptions(print_mode=True)) == "print"


def test_app_invocation_routing():
    assert _app_invocation([]) is True
    assert _app_invocation(["tui"]) is True
    assert _app_invocation(["-p", "hi"]) is True
    assert _app_invocation(["--root", "x", "status", "t"]) is False
    assert _app_invocation(["run", "t1"]) is False
    assert _app_invocation(["migrate", "--all"]) is False
    assert _app_invocation(["--root", "x"]) is True
    assert _app_invocation(["--root", "x", "--db", "y", "run", "t1"]) is False
    assert _app_invocation(["--root", "x", "--db", "y"]) is True
