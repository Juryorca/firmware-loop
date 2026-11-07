"""项目默认工具集与完整性检测。

这不是第二套工具注册系统:
- 工具仍由 Registry/manifest/providers 负责注册与执行；
- 本模块只声明“项目正常研究需要的默认资产/外部依赖”，并**只读**检查它们；
- 状态区分仓库资产、外部依赖、能力探针和服务生命周期，避免一律报 missing。
"""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass

from .providers import (ARCH_MISMATCH, DEPS_MISSING, MISSING, NOT_STARTED,
                        PARTIAL, READY, inventory)

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
                      "tools/termshot.sh", "tools/win_stage.sh")),
    ToolsetItem("strace", "系统调用追踪", "P1", True, ("trace.strace",)),
    ToolsetItem("patched-qemu", "qemu-aarch64-patched(特殊构建)", "P2",
                False, external=("qemu-aarch64-patched",), optional=True),
    ToolsetItem("angr", "符号执行(angr)", "P2", False,
                ("symbolic.angr",), optional=True),
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


def _external_check(names: tuple[str, ...]) -> tuple[str, list[dict]]:
    rows = []
    found = 0
    for name in names:
        p = shutil.which(name)
        rows.append({"name": name, "path": p})
        if p:
            found += 1
    if found == len(names):
        return READY, rows
    if found == 0:
        return MISSING, rows
    return PARTIAL, rows


def check_toolset(root: str, *, inventory_data: list[dict] | None = None) -> dict:
    """只读检查默认工具集；不启动服务、不启动浏览器、不修改环境。"""
    inv = {x["capability"]: x for x in
           (inventory_data if inventory_data is not None else inventory(root))}
    results = []
    for item in DEFAULT_TOOLSET:
        repo_status, repo_rows = _repo_check(root, item.repo)
        ext_status, ext_rows = _external_check(item.external)
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
        reasons = []
        for r in repo_rows:
            if not r["ok"]:
                if not r["exists"]:
                    reasons.append(f"仓库资产缺失: {r['path']}")
                elif r["needs_executable"] and not r["executable"]:
                    reasons.append(f"仓库脚本不可执行: {r['path']}")
        for r in ext_rows:
            if not r["path"]:
                reasons.append(f"外部命令缺失: {r['name']}")
        for r in cap_rows:
            if r["status"] != READY:
                reasons.append(f"能力 {r['capability']}={r['status']}: "
                               f"{r.get('detail') or ''}".rstrip())
        results.append({
            "id": item.id, "label": item.label, "category": item.category,
            "required": item.required, "optional": item.optional,
            "status": status, "repo": repo_rows, "external": ext_rows,
            "capabilities": cap_rows, "reasons": reasons, "notes": item.notes,
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
    }


def render_report(report: dict) -> str:
    lines = ["默认工具集完整性 (Default Toolset Integrity)", ""]
    lines.append(f"required: {report['required_ready']}/{report['required_total']} ready; "
                 f"incomplete={report['required_incomplete']}; "
                 f"optional={report['optional_total']}; integrity={report['integrity']}")
    for item in report["items"]:
        mark = "OK" if item["status"] == "ready" else (
            "OPT" if item["status"] == "optional" else "FAIL")
        lines.append(f"[{mark}] {item['category']} {item['label']}: {item['status']}")
        for reason in item["reasons"][:4]:
            lines.append(f"      - {reason}")
    if report["required_incomplete"]:
        lines += ["", "修复建议:"]
        for item in report["items"]:
            if item["required"] and item["status"] != "ready":
                lines.append(f"- {item['label']}: " +
                             ("; ".join(item["reasons"][:2]) or "检查配置/启动服务"))
    return "\n".join(lines)
