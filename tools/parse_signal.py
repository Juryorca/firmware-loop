#!/usr/bin/env python
"""信号判定: 扫描 strace 日志输出二值判定 HIT/MISS。
用法: parse_signal.py <strace日志> [payload标记串]
HIT 判据: execve 命中 /bin/sh|busybox,含 payload 标记串的 execve,或 SIGSEGV/SIGILL/SIGBUS。"""
import re, sys
log = sys.argv[1] if len(sys.argv) > 1 else "/tmp/emu_strace.log"
mark = sys.argv[2] if len(sys.argv) > 2 else ""
try:
    text = open(log, errors="ignore").read()
except FileNotFoundError:
    print("MISS: no log"); sys.exit(1)
pats = {
    "shell_exec": r'execve\("[^"]*/bin/(sh|busybox)',
    "crash": r"--- SIG(SEGV|ILL|BUS)",
}
if mark:
    pats["payload_exec"] = r"execve\([^)]*" + re.escape(mark)
hits = [k for k, p in pats.items() if re.search(p, text)]
print(("HIT:" + ",".join(hits)) if hits else "MISS")
sys.exit(0 if hits else 1)
