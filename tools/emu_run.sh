#!/usr/bin/env bash
# 用法: emu_run.sh <arch: arm|aarch64|mips|mipsel> <二进制> [strace输出] [秒数]
ARCH="$1"; BIN="$2"; LOG="${3:-/tmp/emu_strace.log}"; SEC="${4:-30}"
Q=/usr/bin/qemu-$ARCH
[ -x "$Q" ] || { echo "no qemu-$ARCH"; exit 2; }
[ -x "$BIN" ] || { echo "no such file: $BIN"; exit 2; }
timeout "$SEC" strace -f -s 256 -o "$LOG" "$Q" -L "$BIN" >/dev/null 2>&1
echo "[emu] strace 日志: $LOG"
