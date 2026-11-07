"""Slash Commands 注册表(Pi 的 core/slash-commands.ts 对应物)。

只声明"命令 → 说明/参数提示",执行在 Interactive Mode 里,
且每条都调用现有 Session/Services,不是静态帮助文本。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Command:
    name: str
    description: str
    arg_hint: str = ""


COMMANDS: tuple[Command, ...] = (
    Command("help", "显示命令与快捷键"),
    Command("model", "选择/切换模型", "[name]"),
    Command("target", "选择/切换固件目标", "[name]"),
    Command("resume", "恢复历史 run(选择器或 run-id)", "[run-id]"),
    Command("status", "研究状态 / 进度 / Gate 阻塞"),
    Command("budget", "预算、Token 与上下文占用"),
    Command("hypotheses", "假设池(排序与判定)"),
    Command("evidence", "证据与观察(复核状态)"),
    Command("new", "开始新的 run(同一目标)"),
    Command("continue", "继续当前 run(一轮)"),
    Command("clear", "清空当前对话视图(不删数据库)"),
    Command("expand", "展开/折叠工具输出"),
    Command("quit", "退出 fw-core"),
)

BY_NAME = {c.name: c for c in COMMANDS}
