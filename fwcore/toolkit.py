"""把仓库里已有的研究工具(main 的 tools/*)适配成可发现的工具描述。

要点
- **不重写研究工具**,只给它们"能力描述 + 参数 schema + 执行边界";模型据此自主选择。
- 高风险(起仿真、跑 fuzz)标记 risk=high ⇒ 走 policy 的确认/默认拒绝。
- 输出裁剪后返回(大文件留在 artifacts,模型拿到路径 + 摘要)。
"""

from __future__ import annotations

import json
import os
import re

from .tools import Registry, ToolContext, shell_exec


def _int_or_none(value):
    """把观察值安全转 int(解析失败返回 None,不让解析器把工具结果变成异常)。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


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
        if not (p and os.path.isfile(p)):
            continue
        try:
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - keep))
                text += f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
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

    m = re.search(r"=== derived_check: (PASS|FAIL x(\d+)) ===", text)
    fails = [ln for ln in text.splitlines() if ln.startswith("FAIL")]
    oks = [ln for ln in text.splitlines() if ln.startswith("OK")]
    return {"status": "ok" if m and m.group(1) == "PASS" else "fail" if m else "unparsed",
            "verdict": m.group(1) if m else None, "fail_count": len(fails),
            "ok_count": len(oks),
            "fails": [ln[:160] for ln in fails[:5]]}


@observer("research_queue")
def _obs_queue(text: str) -> dict:

    kv = dict(re.findall(r"(OPEN|BAD|ROUND|STOP|POOL|NEXT)=([^ ]*)", text))
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

    tuples = re.findall(r"tuples[:=]\s*(\d+)", text)
    crash = bool(re.search(r"SIGSEGV|SIGILL|SIGBUS|uncaught signal|crash", text, re.I))
    if not tuples and not crash:
        return {"status": "unparsed", "raw": text[-160:]}
    return {"status": "ok", "tuples": _int_or_none(tuples[-1]) if tuples else None,
            "crash": crash}


@observer("emu_run")
def _obs_emu(text: str) -> dict:

    rc = re.findall(r"(?:rc|exit)[=: ]\s*(\d+)", text)
    return {"status": "ok", "exit_code": _int_or_none(rc[-1]) if rc else None,
            "raw_tail": text[-160:]}


@observer("ghidra_query")
def _obs_ghidra(text: str) -> dict:
    """Ghidra 输出结构化(JSON 优先;否则抽取函数/地址;解析不出标 unparsed)。"""
    return _obs_ida(text)


@observer("ida_query")
def _obs_ida(text: str) -> dict:
    """IDA 输出结构化:优先 JSON(ida_call 的 --json),否则尝试抽取关键字段,再不行标 unparsed。"""

    # IDA MCP 返回的是 JSON-RPC 信封,真正的结果在 result.content[0].text(通常是内层 JSON 字符串)
    def _unwrap(d):
        try:
            inner = d["result"]["content"][0]["text"]
            return json.loads(inner) if isinstance(inner, str) else None
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            return None

    for chunk in (text.strip(), text[text.find("{"): text.rfind("}") + 1] if "{" in text else ""):
        if not chunk:
            continue
        try:
            data = json.loads(chunk)
            if isinstance(data, dict):
                inner = _unwrap(data)
                if isinstance(inner, dict):
                    data = inner
                elif isinstance(inner, list):
                    data = {"items": inner, "count": len(inner)}
                return {"status": "ok", "format": "json",
                        "fields": sorted(data.keys())[:20],
                        "preview": {k: str(v)[:120] for k, v in list(data.items())[:8]}}
        except json.JSONDecodeError:
            continue
    funcs = re.findall(r"(?:sub|loc)_[0-9A-Fa-f]{6,}", text)
    if funcs:
        return {"status": "ok", "format": "text", "function_count": len(set(funcs)),
                "functions": sorted(set(funcs))[:10]}
    return {"status": "unparsed", "raw_tail": text[-160:]}


def register_repo_tools(reg: Registry, root: str) -> dict:
    """把仓库研究工具注册进 Registry。

    **不再逐个硬编码**:工具来自 `<root>/tools/registry/*.json` 的声明(见 `fwcore/toolreg.py`)。
    新增已有执行类型的工具 = 加一个 manifest(+必要时适配器/解析器),不改这个函数、不改 Agent Loop。

    返回注册报告(registered / unavailable / skipped),便于在 CLI/进度里如实展示"哪些工具没装上、为什么"。
    """
    from .adapters_ida import factory as ida_factory
    from .toolreg import register_feature, register_manifests

    # 特色适配器只在这里登记"名字 → 工厂";Registry 只按名字取,不知道 Windows/端口/暂存目录
    register_feature("ida", ida_factory)
    report = register_manifests(reg, root)
    reg.registration = report
    return report
