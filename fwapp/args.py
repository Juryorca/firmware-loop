"""应用层参数解析(Pi 的 cli/args.ts 对应物)。

只负责**应用模式**参数;`init/hyp/...` 等研究子命令仍由 fwcore.cli 的
argparse 处理,两边不共享解析器(避免把应用参数塞进研究命令)。
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field


@dataclass
class AppOptions:
    root: str = field(default_factory=lambda: os.environ.get("FW_ROOT") or os.getcwd())
    db: str | None = None
    target: str | None = None
    run_id: str | None = None
    model: str | None = None
    print_mode: bool = False
    message: str | None = None
    script: str | None = None
    watchdog: bool = True
    mcp: bool = True
    tui: bool = False
    extra: list[str] = field(default_factory=list)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="fw-core",
        description="firmware-loop 交互式研究终端(顶层无子命令 = 自动模式)",
        add_help=True,
    )
    ap.add_argument("--root", default=None, help="工作区根目录(默认当前目录 / FW_ROOT)")
    ap.add_argument("--db", default=None, help="SQLite 路径(默认 <root>/state/fwcore.sqlite3)")
    ap.add_argument("--target", default=None, help="启动时选中的固件目标")
    ap.add_argument("--run", dest="run_id", default=None, help="启动时选中的 run")
    ap.add_argument("--model", default=None, help="模型:scripted 或 provider model id（/model 自动读取 /models 目录）")
    ap.add_argument("--script", default=None, help="scripted 模型的脚本 JSON(离线演示/测试)")
    ap.add_argument("-p", "--print", dest="print_mode", action="store_true",
                    help="非交互模式:执行一条消息后输出并退出")
    ap.add_argument("--no-watchdog", dest="watchdog", action="store_false",
                    help="关闭外层看护(仅调试)")
    ap.add_argument("--no-mcp", dest="mcp", action="store_false", help="本次不接入 MCP")
    ap.add_argument("prompt", nargs="*", help="要发送的消息(-p 模式)")
    return ap


def parse(argv: list[str]) -> tuple[AppOptions, argparse.ArgumentParser]:
    ap = build_parser()
    argv = list(argv)
    tui = bool(argv) and argv[0] == "tui"
    if tui:
        argv = argv[1:]
    ns = ap.parse_args(argv)
    msg = " ".join(ns.prompt).strip() or None
    opts = AppOptions(
        root=os.path.abspath(ns.root or os.environ.get("FW_ROOT") or os.getcwd()),
        db=ns.db, target=ns.target, run_id=ns.run_id, model=ns.model,
        print_mode=ns.print_mode, message=msg, script=ns.script,
        watchdog=bool(ns.watchdog), mcp=bool(ns.mcp), tui=tui,
    )
    return opts, ap


def resolve_mode(opts: AppOptions) -> str:
    """返回 interactive / print;非 TTY 永不进全屏。

    与 Pi 的 resolveAppMode 同义:显式 -p 或非 TTY ⇒ print;
    `tui` 显式要求但不在终端 ⇒ 由调用方报错(不静默降级)。
    """
    if opts.print_mode:
        return "print"
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return "print"
    return "interactive"


def read_stdin_message() -> str | None:
    if sys.stdin.isatty():
        return None
    try:
        data = sys.stdin.read()
    except OSError:
        return None
    return data.strip() or None
