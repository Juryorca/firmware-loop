"""把仓库里已有的研究工具(main 的 tools/*)适配成可发现的工具描述。

要点
- **不重写研究工具**,只给它们"能力描述 + 参数 schema + 执行边界";模型据此自主选择。
- 高风险(起仿真、跑 fuzz)标记 risk=high ⇒ 走 policy 的确认/默认拒绝。
- 输出裁剪后返回(大文件留在 artifacts,模型拿到路径 + 摘要)。
"""

from __future__ import annotations

import os

from .tools import Registry, ToolContext, ToolSpec, shell_exec


OBSERVERS = {}


def observer(name: str):
    """注册"工具输出 → 结构化观测"的解析器(解析不出来就如实标 unparsed)。"""
    def deco(fn):
        OBSERVERS[name] = fn
        return fn
    return deco


def _run(ctx: ToolContext, cmd: str, timeout: float = 120.0, keep: int = 3000,
         tool: str | None = None, meta: dict | None = None) -> dict:
    out = shell_exec(ctx, {"command": cmd, "timeout": timeout})
    text = ""
    for key in ("stdout_path", "stderr_path"):
        p = out.get(key)
        if p and os.path.isfile(p):
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - keep))
                text += f.read().decode("utf-8", errors="replace")
    out["output_tail"] = text[-keep:]
    if tool:
        fn = OBSERVERS.get(tool)
        if fn:
            try:
                out["observation"] = fn(text)
            except Exception as e:                     # 解析失败不算工具失败,但要如实标注
                out["observation"] = {"status": "unparsed", "error": f"{type(e).__name__}: {e}"}
        else:
            out["observation"] = {"status": "unparsed", "reason": "该工具尚无结构化解析器"}
    if meta:
        out["meta"] = meta
    return out


@observer("research_derived")
def _obs_derived(text: str) -> dict:
    import re as _re

    m = _re.search(r"=== derived_check: (PASS|FAIL x(\d+)) ===", text)
    fails = [ln for ln in text.splitlines() if ln.startswith("FAIL")]
    oks = [ln for ln in text.splitlines() if ln.startswith("OK")]
    return {"status": "ok" if m and m.group(1) == "PASS" else "fail" if m else "unparsed",
            "verdict": m.group(1) if m else None, "fail_count": len(fails),
            "ok_count": len(oks),
            "fails": [ln[:160] for ln in fails[:5]]}


@observer("research_queue")
def _obs_queue(text: str) -> dict:
    import re as _re

    kv = dict(_re.findall(r"(OPEN|BAD|ROUND|STOP|POOL|NEXT)=([^ ]*)", text))
    try:
        pool, faces = (kv.get("POOL") or "0/0").split("/", 1)
        return {"status": "ok", "open": int(kv.get("OPEN", 0)), "bad": int(kv.get("BAD", 0)),
                "round": kv.get("ROUND"), "pool": int(pool), "faces": int(faces),
                "next": kv.get("NEXT", "")[:160]}
    except (TypeError, ValueError):
        return {"status": "unparsed", "raw": text[-160:]}


@observer("research_signal")
def _obs_signal(text: str) -> dict:
    hits = [ln for ln in text.splitlines() if ln.strip().startswith("HIT")]
    return {"status": "hit" if hits else "miss", "hits": [h[:160] for h in hits[:5]],
            "hit_count": len(hits)}


@observer("fuzz_cgi")
def _obs_fuzz(text: str) -> dict:
    import re as _re

    tuples = _re.findall(r"tuples[:=]\s*(\d+)", text)
    crash = bool(_re.search(r"SIGSEGV|SIGILL|SIGBUS|uncaught signal|crash", text, _re.I))
    if not tuples and not crash:
        return {"status": "unparsed", "raw": text[-160:]}
    return {"status": "ok", "tuples": int(tuples[-1]) if tuples else None, "crash": crash}


@observer("emu_run")
def _obs_emu(text: str) -> dict:
    import re as _re

    rc = _re.findall(r"(?:rc|exit)[=: ]\s*(\d+)", text)
    return {"status": "ok", "exit_code": int(rc[-1]) if rc else None,
            "raw_tail": text[-160:]}


@observer("ghidra_query")
def _obs_ghidra(text: str) -> dict:
    """Ghidra 输出结构化(JSON 优先;否则抽取函数/地址;解析不出标 unparsed)。"""
    return _obs_ida(text)


@observer("ida_query")
def _obs_ida(text: str) -> dict:
    """IDA 输出结构化:优先 JSON(ida_call 的 --json),否则尝试抽取关键字段,再不行标 unparsed。"""
    import json as _json
    import re as _re

    for chunk in (text.strip(), text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not chunk:
            continue
        try:
            data = _json.loads(chunk)
            if isinstance(data, dict):
                return {"status": "ok", "format": "json",
                        "fields": sorted(data.keys())[:20],
                        "preview": {k: str(v)[:120] for k, v in list(data.items())[:8]}}
        except _json.JSONDecodeError:
            continue
    funcs = _re.findall(r"(?:sub|loc)_[0-9A-Fa-f]{6,}", text)
    if funcs:
        return {"status": "ok", "format": "text", "function_count": len(set(funcs)),
                "functions": sorted(set(funcs))[:10]}
    return {"status": "unparsed", "raw_tail": text[-160:]}


def register_repo_tools(reg: Registry, root: str) -> None:
    """注册 main 里已有的研究工具(按仓库实际存在的脚本)。"""
    def exists(rel: str) -> bool:
        return os.path.isfile(os.path.join(root, rel))

    if exists("tools/derived_check.sh"):
        reg.register(ToolSpec(
            "research_derived",
            "重算派生量/覆盖度/材料完整性(程序可核):KB 条数、A2 覆盖度、finding 包、收尾前置",
            {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
            lambda ctx, a: _run(ctx, f"bash tools/derived_check.sh {a['target']}", 90,
                                tool="research_derived",
                                meta={"capability": "gate.derived", "provider": "repo:derived_check.sh"})))

    if exists("tools/queue_state.sh"):
        reg.register(ToolSpec(
            "research_queue",
            "队列/轮/池状态探针(只读):OPEN/BAD/ROUND/STOP/POOL/NEXT",
            {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
            lambda ctx, a: _run(ctx, f"bash tools/queue_state.sh {a['target']}", 60,
                                tool="research_queue",
                                meta={"capability": "state.queue", "provider": "repo:queue_state.sh"})))

    if exists("tools/parse_signal.py"):
        reg.register(ToolSpec(
            "research_signal",
            "按仓库信号约定判读工具输出(命令注入/崩溃等);返回 HIT/MISS 与依据",
            {"type": "object", "properties": {"log": {"type": "string"}}, "required": ["log"]},
            lambda ctx, a: _run(ctx, f"python3 tools/parse_signal.py {a['log']}", 60,
                                tool="research_signal",
                                meta={"capability": "signal.parse", "provider": "repo:parse_signal.py"})))

    if exists("tools/cgi_fuzz.sh"):
        reg.register(ToolSpec(
            "fuzz_cgi",
            "对厂商 CGI 做有界变异 fuzz(需提供字节/字符串种子与目标可执行);高风险,需确认",
            {"type": "object", "properties": {
                "args": {"type": "string", "description": "透传给 cgi_fuzz.sh 的参数串"},
                "timeout": {"type": "number"}}, "required": ["args"]},
            lambda ctx, a: _run(ctx, f"bash tools/cgi_fuzz.sh {a['args']}",
                                float(a.get("timeout", 300)), tool="fuzz_cgi",
                                meta={"capability": "fuzz.cgi", "provider": "repo:cgi_fuzz.sh"}),
            risk="high"))

    if os.path.isdir(os.path.join(root, "tools", "ghidra_scripts")):   # 目录要用 isdir,
        def _ghidra(ctx: ToolContext, a: dict) -> dict:
            """Ghidra 无头批处理(经既有脚本)。能力不可用时**如实报错**,不伪装成功。"""
            from .providers import default_capabilities

            cap = default_capabilities(ctx.root)["debug.ghidra"] if "debug.ghidra" in default_capabilities(ctx.root) \
                else None
            if cap is not None:
                best, probe = cap.best()
                if probe is None or probe.status == "missing":
                    return {"status": "error",
                            "error": "能力不可用:debug.ghidra 没有可用 provider(不会静默成功)",
                            "meta": {"capability": "debug.ghidra", "provider": None}}
            return _run(ctx, f"bash tools/ghidra_run.sh {a.get('args', '')}", float(a.get("timeout", 300)),
                        tool="ghidra_query",
                        meta={"capability": "debug.ghidra", "provider": "repo:ghidra_run.sh"})

        reg.register(ToolSpec(
            "ghidra_query",
            "调用 Ghidra 无头脚本做函数画像/反编译;输出结构化;能力缺失时如实报错",
            {"type": "object", "properties": {
                "args": {"type": "string"}, "timeout": {"type": "number"}}, "required": ["args"]},
            _ghidra, risk="high"))

    if exists("tools/ida_call.sh"):
        def _ida(ctx: ToolContext, a: dict) -> dict:
            # 复用既有生命周期封装:先确保 supervisor 起来(幂等),再调用
            start = os.path.join(root, "tools", "ida_start.sh")
            if os.path.isfile(start):
                shell_exec(ctx, {"command": f"bash {start}", "timeout": 120})
            argv = a.get("args", "")
            return _run(ctx, f"bash tools/ida_call.sh {argv}", float(a.get("timeout", 180)),
                        tool="ida_query",
                        meta={"capability": "debug.ida", "provider": "repo:ida_call.sh",
                              "arch": a.get("arch")})

        reg.register(ToolSpec(
            "ida_query",
            "调用 Windows IDA(经 WSL,复用 tools/ida_*.sh 生命周期)做反编译/函数画像;输出被结构化成字段",
            {"type": "object", "properties": {
                "args": {"type": "string", "description": "透传给 ida_call.sh 的参数串"},
                "arch": {"type": "string"}, "timeout": {"type": "number"}},
             "required": ["args"]},
            _ida, risk="high"))

    if exists("tools/emu_run.sh"):
        reg.register(ToolSpec(
            "emu_run",
            "在提取的 rootfs 上跑用户态仿真(需要 rootfs 路径与命令);高风险,需确认",
            {"type": "object", "properties": {
                "args": {"type": "string"}, "timeout": {"type": "number"}}, "required": ["args"]},
            lambda ctx, a: _run(ctx, f"bash tools/emu_run.sh {a['args']}",
                                float(a.get("timeout", 180)), tool="emu_run",
                                meta={"capability": "emu.run", "provider": "repo:emu_run.sh"}),
            risk="high"))
