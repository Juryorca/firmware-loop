"""项目默认工具集与完整性检测。

本模块**只做就绪检查**，不是工具注册/执行系统:
- 不注册 Tool、不执行 CLI、不决定 Agent 怎么调用；
- 只声明“项目正常研究需要的默认资产/外部依赖”，并**只读**检查；
- 普通工具由 Agent 通过 `shell_exec` + `state/tools.json` 调用；
- 只有复杂能力探针（Provider）和特殊生命周期（Adapter）才参与本检查。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from . import toolmeta
from .providers import (
    ARCH_MISMATCH,
    DEPS_MISSING,
    MISSING,
    NOT_STARTED,
    PARTIAL,
    READY,
    inventory,
)

ORDER = {READY: 0, PARTIAL: 1, NOT_STARTED: 2, DEPS_MISSING: 3, ARCH_MISMATCH: 4, MISSING: 5}
STATUSES = ("ready", "partial", "missing", "deps_missing", "not_started",
            "arch_mismatch", "optional")


@dataclass(frozen=True)
class ToolsetItem:
    id: str
    label: str
    category: str                 # P0 | P1 | P2
    required: bool = True
    capabilities: tuple[str, ...] = ()
    repo: tuple[str, ...] = ()
    external: tuple[str, ...] = ()
    optional: bool = False
    notes: str = ""


DEFAULT_TOOLSET: tuple[ToolsetItem, ...] = (
    ToolsetItem("ida", "IDA Pro(Windows/WSL)", "P0", True, ("debug.ida",),
                ("tools/ida_start.sh", "tools/ida_open.sh", "tools/ida_call.sh",
                 "tools/ida_stop.sh", "tools/ida_clean.sh", "tools/ida_env.sh",
                 "tools/ida_list.sh", "tools/ida_tools.sh", "tools/ida_mcp_lib.sh")),
    ToolsetItem("ghidra", "Ghidra 无头分析", "P0", True, ("debug.ghidra",),
                ("tools/ghidra_run.sh", "tools/ghidra_scripts/ProfileFuncs.java",
                 "tools/ghidra_scripts/DumpFuncsWithStr.java",
                 "tools/ghidra_scripts/DumpDecomp.java")),
    ToolsetItem("extraction", "固件提取(binwalk/unblob)", "P0", True,
                ("extract.binwalk", "extract.unblob"), ("tools/fw_extract.sh",)),
    ToolsetItem("qemu-user", "QEMU user 仿真", "P0", True,
                external=("qemu-x86_64", "qemu-aarch64", "qemu-arm",
                          "qemu-mipsel", "qemu-mips")),
    ToolsetItem("afl", "AFL++ 与架构 tracer", "P1", True, ("fuzz.aarch64",),
                external=("afl-fuzz",)),
    ToolsetItem("cgi-fuzz", "CGI 有界变异 fuzz", "P1", True,
                ("fuzz.cgi",), ("tools/cgi_fuzz.sh",)),
    ToolsetItem("simulation-helpers", "仿真辅助(Docker/bwrap)", "P1", True,
                external=("docker", "bwrap")),
    ToolsetItem("browser-mcp", "浏览器 MCP", "P1", True, ("mcp.chrome",)),
    ToolsetItem("sasquatch", "厂商 SquashFS 提取", "P1", True,
                ("extract.squashfs-vendor",)),
    ToolsetItem("research-helpers", "研究辅助脚本", "P1", True,
                repo=("tools/check.sh", "tools/derived_check.sh",
                      "tools/queue_state.sh", "tools/parse_signal.py",
                      "tools/win_stage.sh")),
    ToolsetItem("screenshot", "终端截图证据", "P1", True,
                repo=("tools/termshot.sh", "tools/ansi2png.py"),
                external=("tmux", "zsh", "chromium")),
    ToolsetItem("strace", "系统调用追踪", "P1", True, ("trace.strace",)),
    ToolsetItem("patched-qemu", "qemu-aarch64-patched(特殊构建)", "P2",
                False, external=("qemu-aarch64-patched",), optional=True),
    ToolsetItem("angr", "符号执行(angr)", "P2", True, ("symbolic.angr",)),
)


def _is_exec(path: str) -> bool:
    try:
        return os.access(path, os.X_OK)
    except OSError:
        return False


def _rank(status: str) -> int:
    return ORDER.get(status, 5)


def _worst(statuses: list[str]) -> str:
    return max(statuses, key=_rank) if statuses else READY


def _repo_check(root: str, paths: tuple[str, ...]) -> tuple[str, list[dict]]:
    rows = []
    statuses = []
    for rel in paths:
        p = os.path.join(root, rel)
        exists = os.path.isfile(p)
        executable = _is_exec(p) if exists else False
        needs_x = rel.endswith((".sh", ".py"))
        ok = exists and (executable or not needs_x)
        rows.append({"path": rel, "exists": exists, "executable": executable,
                     "needs_executable": needs_x, "ok": ok})
        statuses.append(READY if ok else (PARTIAL if exists else MISSING))
    return _worst(statuses), rows


def _external_check(root: str, names: tuple[str, ...]) -> tuple[str, list[dict]]:
    rows = []
    found = 0
    for name in names:
        entry = toolmeta.runtime(root, name)
        if entry.get("path"):
            p = entry["path"] if os.path.isfile(entry["path"]) else None
            source = "metadata" if p else "metadata-missing"
        else:
            p = shutil.which(name)
            source = "PATH"
        rows.append({"name": name, "path": p, "source": source})
        if p:
            found += 1
    if found == len(names):
        return READY, rows
    if found == 0:
        return MISSING, rows
    return PARTIAL, rows


def _config_error_report(root: str, error: str) -> dict:
    required = [item for item in DEFAULT_TOOLSET if item.required]
    optional = [item for item in DEFAULT_TOOLSET if item.optional]
    return {
        "required_total": len(required), "required_ready": 0,
        "required_incomplete": len(required), "optional_total": len(optional),
        "integrity": "config_error", "config_error": error, "items": [],
        "by_status": {}, "repo_ok": False, "external_ok": False,
        "capabilities_ok": False, "tool_metadata": toolmeta.describe(root)
        if toolmeta.status(root)["valid"] else [],
    }


def check_toolset(root: str, *, inventory_data: list[dict] | None = None) -> dict:
    """只读检查默认工具集；不启动服务、不启动浏览器、不修改环境。"""
    st = toolmeta.status(root)
    if not st["valid"]:
        return _config_error_report(root, st.get("error") or "工具元数据不可用")
    inv = {x["capability"]: x for x in
           (inventory_data if inventory_data is not None else inventory(root))}
    results = []
    for item in DEFAULT_TOOLSET:
        repo_status, repo_rows = _repo_check(root, item.repo)
        ext_status, ext_rows = _external_check(root, item.external)
        cap_rows = []
        for cap in item.capabilities:
            row = inv.get(cap)
            if not row:
                cap_rows.append({"capability": cap, "status": MISSING,
                                 "detail": "capability 未注册", "provider": None})
            else:
                cap_rows.append({
                    "capability": cap,
                    "status": row.get("best_status", MISSING),
                    "detail": next((p.get("detail", "") for p in row.get("providers", [])
                                    if p.get("provider") == row.get("best")), ""),
                    "provider": row.get("best"),
                })
        status = _worst([repo_status, ext_status] + [x["status"] for x in cap_rows])
        if item.optional and status != READY:
            status = "optional"
        results.append({
            "id": item.id, "label": item.label, "category": item.category,
            "required": item.required, "optional": item.optional,
            "status": status, "repo": repo_rows, "external": ext_rows,
            "capabilities": cap_rows, "notes": item.notes,
        })
    req = [r for r in results if r["required"]]
    ready = [r for r in req if r["status"] == READY]
    incomplete = [r for r in req if r["status"] != READY]
    return {
        "required_total": len(req), "required_ready": len(ready),
        "required_incomplete": len(incomplete),
        "optional_total": len([r for r in results if r["optional"]]),
        "integrity": "ready" if not incomplete else "incomplete",
        "items": results,
        "by_status": {s: len([r for r in req if r["status"] == s])
                      for s in STATUSES if any(r["status"] == s for r in req)},
        "repo_ok": all(row["ok"] for item in results for row in item["repo"]),
        "external_ok": all(row["path"] for item in results for row in item["external"]),
        "capabilities_ok": all(cap["status"] == READY
                               for item in results for cap in item["capabilities"]),
        "tool_metadata": toolmeta.describe(root),
    }


def render_report(report: dict) -> str:
    lines = ["默认工具集完整性 (Default Toolset Integrity)", ""]
    lines.append(f"required: {report['required_ready']}/{report['required_total']} ready; "
                 f"incomplete={report['required_incomplete']}; "
                 f"optional={report['optional_total']}; integrity={report['integrity']}")
    if report.get("config_error"):
        lines.append(f"[FAIL] 工具元数据配置错误: {report['config_error']}")
        lines.append("       修复 state/tools.json 后重试；不会回退 PATH。")
        return "\n".join(lines)
    for item in report["items"]:
        mark = "OK" if item["status"] == "ready" else (
            "OPT" if item["status"] == "optional" else "FAIL")
        lines.append(f"[{mark}] {item['category']} {item['label']}: {item['status']}")
    return "\n".join(lines)
