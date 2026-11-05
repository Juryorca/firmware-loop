#!/usr/bin/env bash
# 用法: fw_extract.sh <firmware> <outdir> [--no-unblob]
# 目的: 用**已装好的** binwalk + unblob 做提取(两者产出都保留),打印结构化摘要给 Agent。
# 边界: 由 manifest 的 timeout 控制总时长;不在脚本里放松;不联网。
set -u
FW="${1:?用法: fw_extract.sh <firmware> <outdir> [--no-unblob]}"
OUT="${2:?缺输出目录}"
shift 2
mkdir -p "$OUT"

say() { printf '%s\n' "$*"; }
say "FIRMWARE=$FW"
say "OUTDIR=$OUT"
[ -f "$FW" ] || { say "ERROR=固件不存在"; exit 2; }

if command -v binwalk >/dev/null 2>&1; then
  say "== binwalk -Me =="
  ( cd "$OUT" && timeout 600 binwalk -Me "$FW" 2>&1 | tail -40 ) || say "BINWALK_EXIT=$?"
else
  say "BINWALK=missing"
fi

if [ "${1:-}" != "--no-unblob" ]; then
  UNBLOB="$HOME/venvs/unblob/bin/unblob"
  if [ -x "$UNBLOB" ]; then
    say "== unblob =="
    timeout 600 "$UNBLOB" -e "$OUT/unblob" "$FW" 2>&1 | tail -20 || say "UNBLOB_EXIT=$?"
  else
    say "UNBLOB=missing"
  fi
fi

say "== summary =="
find "$OUT" -maxdepth 2 -type d 2>/dev/null | head -20
say "files=$(find "$OUT" -type f 2>/dev/null | wc -l)"
