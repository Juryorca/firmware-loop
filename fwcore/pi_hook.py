"""Pi hook 的**惰性**入口:默认什么都不做,只有显式开关文件存在时才写入权威状态。

为什么这样设计(用户约束):启用 Pi hook 会改变**研究会话**的 live 行为 ⇒ 需要用户明确授权。
所以代码可以先就位,但:
- 开关不是配置项,而是一个**显式文件** `state/pi_bridge.enabled`(用户手动创建/删除);
- 关闭时 `handle()` 直接返回 `{"status": "disabled"}`,**不写任何一行状态**;
- 关闭时也不导入/初始化任何重资源(不连 MCP、不起线程)。

Enabling is the user's decision; the code must be harmless by default.
"""

from __future__ import annotations

import contextlib
import os

FLAG_REL = os.path.join("state", "pi_bridge.enabled")


def flag_path(root: str) -> str:
    return os.path.join(root, FLAG_REL)


def enabled(root: str) -> bool:
    return os.path.isfile(flag_path(root))


def enable(root: str, note: str = "") -> str:
    p = flag_path(root)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(note or "enabled by user\n")
    return p


def disable(root: str) -> None:
    with contextlib.suppress(FileNotFoundError):
        os.remove(flag_path(root))


def handle(con, root: str, event: dict) -> dict:
    """关闭时是**纯 no-op**;打开时委派给 pi_bridge(幂等语义见那里)。"""
    if not enabled(root):
        return {"status": "disabled", "note": "Pi hook 未启用(state/pi_bridge.enabled 不存在)"}
    from .pi_bridge import handle_event

    out = handle_event(con, root, event)
    out["via"] = "pi_hook"
    return out
