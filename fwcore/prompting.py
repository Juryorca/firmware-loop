"""System Prompt 的唯一组装入口。

顺序固定：core system -> root AGENTS.md -> Skills 目录 -> Slash Commands。
- 知识正文不进入 system；由 knowledge_search/get 按需返回。
- LOOP/知识库正文不进入 system；由 Skill/知识工具按需加载。
- 这里只做文本组装，不执行研究逻辑。
"""
from __future__ import annotations

import os

from .commands import COMMANDS
from .skills import catalog as skill_catalog

CORE_PATH = os.path.join(os.path.dirname(__file__), "prompts", "system.md")


def core_prompt() -> str:
    try:
        with open(CORE_PATH, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError as e:
        raise RuntimeError(f"核心 System Prompt 缺失/不可读: {CORE_PATH}: {e}") from e


def project_instructions(root: str) -> str:
    """根目录 AGENTS.md 是项目级指令文件；完整读取，不做字节裁剪。"""
    path = os.path.join(root, "AGENTS.md")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return "（未找到根目录 AGENTS.md；请检查工作区，不要把本提示词当作项目规则。）"


def _skill_section(root: str) -> str:
    items = [x for x in skill_catalog(root) if x.get("audience") != "development"]
    if not items:
        return ""
    lines = ["## Available Skills（仅目录；正文用 skill_read 按需加载）", ""]
    for s in items:
        desc = " ".join((s["description"] or "").split())
        lines.append(f"- `{s['name']}`: {desc[:240]}")
    lines += ["", "用法：先按任务选择 Skill，再调用 `skill_read(name=..., reference=...)`；"
                  "不要一次加载全部 Skills。"]
    return "\n".join(lines)


def _command_section() -> str:
    lines = ["## User Slash Commands（仅用户输入触发，不是模型工具）", ""]
    for c in COMMANDS:
        hint = f" {c.arg_hint}" if c.arg_hint else ""
        lines.append(f"- `/{c.name}{hint}`: {c.description}")
    lines += ["", "命令由 fwapp 解析并由用户输入触发；模型不得自行调用、伪造或把它们当作 Skill。"]
    return "\n".join(lines)


def build_system_prompt(root: str) -> str:
    parts = [core_prompt(), "## 项目级指令（AGENTS.md）", project_instructions(root)]
    skills = _skill_section(root)
    if skills:
        parts += [skills]
    parts += [_command_section()]
    return "\n\n".join(parts).strip() + "\n"
