"""CLI Entry(对应 Pi 的 cli.ts):只做入口转发。

真正的参数解析在 args.py,启动流程在 main.py —— 保持入口极薄,
避免把应用逻辑塞回 fwcore.cli。
"""

from __future__ import annotations


def main(argv: list[str] | None = None) -> int:
    from .main import main as app_main
    return app_main(argv)
