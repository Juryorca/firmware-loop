"""QEMU user 的显式 argv 适配器:不经过 shell 字符串拼接。"""
from __future__ import annotations

import os
import shutil

from . import toolmeta
from .toolreg import num, run_process
from .tools import ToolContext

ARCHES = {
    "x86_64": "qemu-x86_64",
    "aarch64": "qemu-aarch64",
    "arm": "qemu-arm",
    "mipsel": "qemu-mipsel",
    "mips": "qemu-mips",
}
# 只有 aarch64 有明确的特殊构建;其它架构仍用系统 QEMU。
SPECIAL = {"aarch64": "qemu-aarch64-patched"}


def _select_qemu(root: str, arch: str, requested: str | None) -> tuple[str | None, dict, str, str | None]:
    """返回 (path, entry, 选中的名称, error)。配置失效不静默回退。"""
    qemu_name = ARCHES[arch]
    if requested:
        path = requested
        if os.path.sep not in path and not os.path.isfile(path):
            path = shutil.which(path) or path
        if not os.path.isfile(path):
            return None, {}, path, f"指定的 qemu 不存在: {requested}"
        return path, {}, path, None
    special = SPECIAL.get(arch)
    if special:
        p, state = toolmeta.path_status(root, special)
        if state == "ok":
            return p, toolmeta.runtime(root, special), special, None
        if state == "missing":
            return None, toolmeta.runtime(root, special), special, f"配置的 {special} 路径不存在: {p}"
        if state.startswith("invalid-config:"):
            return None, {}, special, state.split(":", 1)[1]
    p, state = toolmeta.path_status(root, qemu_name)
    if state == "ok":
        return p, toolmeta.runtime(root, qemu_name), qemu_name, None
    if state == "missing":
        return None, toolmeta.runtime(root, qemu_name), qemu_name, f"配置的 {qemu_name} 路径不存在: {p}"
    if state.startswith("invalid-config:"):
        return None, {}, qemu_name, state.split(":", 1)[1]
    path = shutil.which(qemu_name)
    if not path:
        return None, {}, qemu_name, f"未找到 {qemu_name}(在 state/tools.json 配置 {qemu_name}.path 或 PATH 中提供)"
    return path, {}, qemu_name, None


def run(ctx: ToolContext, args: dict) -> dict:
    arch = str(args.get("arch") or "")
    if arch not in ARCHES:
        return {"status": "error", "error": f"不支持的 arch: {arch!r};可选 {sorted(ARCHES)}"}
    requested = str(args["qemu"]) if args.get("qemu") else None
    qemu, entry, selected, err = _select_qemu(ctx.root, arch, requested)
    if err or not qemu:
        return {"status": "error", "error": err or "未找到 QEMU"}
    binary = str(args.get("binary") or "")
    if not binary:
        return {"status": "error", "error": "缺 binary"}
    if not os.path.isfile(binary):
        return {"status": "error", "error": f"binary 不存在: {binary}"}
    rootfs = args.get("rootfs")
    if rootfs and not os.path.isdir(str(rootfs)):
        return {"status": "error", "error": f"rootfs 不存在: {rootfs}"}
    extra = args.get("argv") or []
    if not isinstance(extra, list) or not all(isinstance(x, str) for x in extra):
        return {"status": "error", "error": "argv 必须是字符串数组"}
    cmd = [qemu]
    if rootfs:
        cmd += ["-L", str(rootfs)]
    cmd += [binary] + extra
    timeout = num(args.get("timeout"), 30.0)
    out = run_process(ctx, cmd, timeout=timeout,
                      cwd=entry.get("cwd") or ctx.root, env=entry.get("env"))
    out.setdefault("meta", {})
    out["meta"].update({"capability": "emu.user", "provider": qemu, "arch": arch,
                        "binary": binary, "rootfs": rootfs,
                        "special": selected == "qemu-aarch64-patched",
                        "qemu_name": selected})
    return out
