"""把仓库里已有的研究工具(main 的 tools/*)适配成可发现的工具描述。

要点
- **不重写研究工具**,只给它们"能力描述 + 参数 schema + 执行边界";模型据此自主选择。
- 高风险(起仿真、跑 fuzz)标记 risk=high ⇒ 走 policy 的确认/默认拒绝。
- 输出裁剪后返回(大文件留在 artifacts,模型拿到路径 + 摘要)。
"""

from __future__ import annotations

import os

from .tools import Registry, ToolContext, ToolSpec, shell_exec


def _run(ctx: ToolContext, cmd: str, timeout: float = 120.0, keep: int = 3000) -> dict:
    out = shell_exec(ctx, {"command": cmd, "timeout": timeout})
    text = ""
    for key in ("stdout_path", "stderr_path"):
        p = out.get(key)
        if p and os.path.isfile(p):
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - keep))
                text += f.read().decode("utf-8", errors="replace")
    out["output_tail"] = text[-keep:]
    return out


def register_repo_tools(reg: Registry, root: str) -> None:
    """注册 main 里已有的研究工具(按仓库实际存在的脚本)。"""
    def exists(rel: str) -> bool:
        return os.path.isfile(os.path.join(root, rel))

    if exists("tools/derived_check.sh"):
        reg.register(ToolSpec(
            "research_derived",
            "重算派生量/覆盖度/材料完整性(程序可核):KB 条数、A2 覆盖度、finding 包、收尾前置",
            {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
            lambda ctx, a: _run(ctx, f"bash tools/derived_check.sh {a['target']}", 90)))

    if exists("tools/queue_state.sh"):
        reg.register(ToolSpec(
            "research_queue",
            "队列/轮/池状态探针(只读):OPEN/BAD/ROUND/STOP/POOL/NEXT",
            {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
            lambda ctx, a: _run(ctx, f"bash tools/queue_state.sh {a['target']}", 60)))

    if exists("tools/parse_signal.py"):
        reg.register(ToolSpec(
            "research_signal",
            "按仓库信号约定判读工具输出(命令注入/崩溃等);返回 HIT/MISS 与依据",
            {"type": "object", "properties": {"log": {"type": "string"}}, "required": ["log"]},
            lambda ctx, a: _run(ctx, f"python3 tools/parse_signal.py {a['log']}", 60)))

    if exists("tools/cgi_fuzz.sh"):
        reg.register(ToolSpec(
            "fuzz_cgi",
            "对厂商 CGI 做有界变异 fuzz(需提供字节/字符串种子与目标可执行);高风险,需确认",
            {"type": "object", "properties": {
                "args": {"type": "string", "description": "透传给 cgi_fuzz.sh 的参数串"},
                "timeout": {"type": "number"}}, "required": ["args"]},
            lambda ctx, a: _run(ctx, f"bash tools/cgi_fuzz.sh {a['args']}",
                                float(a.get("timeout", 300))), risk="high"))

    if exists("tools/emu_run.sh"):
        reg.register(ToolSpec(
            "emu_run",
            "在提取的 rootfs 上跑用户态仿真(需要 rootfs 路径与命令);高风险,需确认",
            {"type": "object", "properties": {
                "args": {"type": "string"}, "timeout": {"type": "number"}}, "required": ["args"]},
            lambda ctx, a: _run(ctx, f"bash tools/emu_run.sh {a['args']}",
                                float(a.get("timeout", 180))), risk="high"))
