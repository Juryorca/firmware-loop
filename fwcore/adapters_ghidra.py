"""Ghidra 显式参数适配器:项目/样本/脚本分开传,不拼一条自由 shell。"""
from __future__ import annotations

import os

from .toolreg import num, run_process
from .tools import ToolContext


def run(ctx: ToolContext, args: dict) -> dict:
    project = str(args.get("project") or "")
    name = str(args.get("name") or "")
    binary = str(args.get("binary") or "")
    script = str(args.get("script") or "")
    if not project or not name or not binary or not script:
        return {"status": "error", "error": "project/name/binary/script 均不能为空"}
    if not os.path.isfile(binary):
        return {"status": "error", "error": f"binary 不存在: {binary}"}
    wrapper = os.path.join(ctx.root, "tools", "ghidra_run.sh")
    if not os.path.isfile(wrapper):
        return {"status": "error", "error": f"Ghidra wrapper 不存在: {wrapper}"}
    script_args = args.get("script_args") or []
    if not isinstance(script_args, list) or not all(isinstance(x, str) for x in script_args):
        return {"status": "error", "error": "script_args 必须是字符串数组"}
    try:
        os.makedirs(project, exist_ok=True)
        if script_args and script_args[0]:
            os.makedirs(script_args[0], exist_ok=True)
    except OSError as e:
        return {"status": "error", "error": f"无法创建 Ghidra 项目/输出目录: {e}"}
    cmd = ["bash", wrapper, project, name, "-import", binary, "-postScript", script]
    cmd += script_args
    if args.get("cleanup"):
        cmd.append("-deleteProject")
    out = run_process(ctx, cmd, timeout=num(args.get("timeout"), 300.0), cwd=ctx.root)
    # Ghidra/Java 的 stdout 很噪；把脚本产物做成给模型看的结构化摘要，
    # 原始 stdout/stderr 仍保留在 out 的路径里。
    if out.get("status") == "ok" and script_args:
        import glob
        import json
        outdir = script_args[0]
        tsv = sorted(glob.glob(os.path.join(outdir, "*.tsv")))
        if tsv:
            result = tsv[0]
            try:
                with open(result, encoding="utf-8", errors="replace") as f:
                    rows = sum(1 for _ in f)
            except OSError:
                rows = 0
            out["result_path"] = result
            out["text"] = json.dumps({"script": script, "result": result,
                                      "rows": max(0, rows - 1),
                                      "functions": max(0, rows - 1)},
                                     ensure_ascii=False)
    out.setdefault("meta", {})
    out["meta"].update({"capability": "debug.ghidra", "provider": "repo:ghidra_run.sh",
                        "project": project, "name": name, "binary": binary,
                        "script": script})
    return out
