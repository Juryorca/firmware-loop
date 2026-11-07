#!/usr/bin/env bash
# [legacy] 用法: emu_run.sh <arch: arm|aarch64|mips|mipsel> <二进制> [strace输出] [秒数]
# 新工具优先用 qemu_user(显式 binary/rootfs/argv);这里只保留历史 strace 入口。
set -u
ROOT="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
ARCH="$1"; BIN="$2"; LOG="${3:-/tmp/emu_strace.log}"; SEC="${4:-30}"
Q="${FW_QEMU:-}"
if [ -z "$Q" ]; then
  Q="$(cd "$ROOT" && python3 -m fwcore.toolmeta --path "qemu-$ARCH" 2>/dev/null || true)"
fi
[ -n "$Q" ] || Q="$(command -v "qemu-$ARCH" || true)"
[ -n "$Q" ] && [ -x "$Q" ] || { echo "no qemu-$ARCH(configure state/tools.json or PATH)"; exit 2; }
[ -x "$BIN" ] || { echo "no such file: $BIN"; exit 2; }
timeout "$SEC" strace -f -s 256 -o "$LOG" "$Q" "$BIN" >/dev/null 2>&1
echo "[emu] strace 日志: $LOG"
