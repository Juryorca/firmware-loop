"""QEMU user 的显式 argv 适配器:不经过 shell 字符串拼接。"""
from __future__ import annotations

import os
import shutil

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
    qemu = shutil.which(qemu_name)
    if not qemu:
        return {"status": "error", "error": f"未找到 {qemu_name}"}
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
    out = run_process(ctx, cmd, timeout=timeout, cwd=ctx.root)
    out.setdefault("meta", {})
    out["meta"].update({"capability": "emu.user", "provider": qemu, "arch": arch,
                        "binary": binary, "rootfs": rootfs})
    return out
