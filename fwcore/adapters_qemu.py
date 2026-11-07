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


def run(ctx: ToolContext, args: dict) -> dict:
    arch = str(args.get("arch") or "")
    qemu_name = ARCHES.get(arch)
    if not qemu_name:
        return {"status": "error", "error": f"不支持的 arch: {arch!r};可选 {sorted(ARCHES)}"}
    requested = args.get("qemu")
    entry = toolmeta.runtime(ctx.root, qemu_name)
    qemu = str(requested) if requested else entry.get("path") or shutil.which(qemu_name)
    if qemu and os.path.sep not in qemu and not os.path.isfile(qemu):
        qemu = shutil.which(qemu) or qemu
    if not qemu or not os.path.isfile(qemu):
        return {"status": "error", "error": f"未找到 {qemu_name}" + (
            f"(指定 qemu={requested})" if requested else
            f";在 state/tools.json 配置 {qemu_name}.path 或 PATH 中提供")}
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
    out = run_process(ctx, cmd, timeout=timeout, cwd=ctx.root,
                      env=entry.get("env"))
    out.setdefault("meta", {})
    out["meta"].update({"capability": "emu.user", "provider": qemu, "arch": arch,
                        "binary": binary, "rootfs": rootfs,
                        "special": bool(requested) or qemu_name == "qemu-aarch64-patched"})
    return out
