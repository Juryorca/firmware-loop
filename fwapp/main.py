"""Application Main(对应 Pi 的 main.ts)。

CLI Entry 之后、模式之前:解析应用参数 → 解析配置 → 建 Runtime →
按模式分发(interactive / print)。研究子命令不经过这里。
"""

from __future__ import annotations

import sys

from .args import parse, read_stdin_message, resolve_mode
from .config import resolve
from .runtime import AppRuntime


def main(argv: list[str] | None = None) -> int:
    opts, parser = parse(list(sys.argv[1:] if argv is None else argv))
    mode = resolve_mode(opts)
    if opts.tui and mode != "interactive":
        print("错误: `tui` 需要真实终端(stdin/stdout 都是 TTY);非 TTY 请用 `-p`。",
              file=sys.stderr)
        return 2

    cfg = resolve(opts)
    for d in cfg.diagnostics:
        if mode == "print" or d.level == "error":
            print(f"[{d.level}] {d.message}", file=sys.stderr)

    message = opts.message
    if mode == "print" and not message:
        message = read_stdin_message()
    if mode == "print" and not message:
        parser.print_help()
        print("\n错误: 非交互模式需要一条消息(-p \"...\" 或管道输入)。", file=sys.stderr)
        return 2

    runtime = AppRuntime(
        cfg, target=opts.target, run_id=opts.run_id, model=opts.model,
        mcp=opts.mcp, watchdog=opts.watchdog,
    )
    try:
        if mode == "interactive":
            return _run_interactive(runtime, cfg.diagnostics)
        from .modes.print import run_print
        return run_print(runtime, message or "")
    finally:
        runtime.dispose()


def _run_interactive(runtime: AppRuntime, diagnostics) -> int:
    """延迟导入:没有 textual 时 print/研究子命令仍可用。"""
    try:
        from .modes.interactive import run_interactive
    except ImportError as e:                    # 缺 TUI 依赖时给可操作提示,不糊弄
        print(f"错误: 交互模式缺少依赖({e})。安装:`pip install textual`", file=sys.stderr)
        return 2
    return run_interactive(runtime, diagnostics=diagnostics)


if __name__ == "__main__":
    raise SystemExit(main())
