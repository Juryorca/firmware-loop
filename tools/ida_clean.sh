#!/usr/bin/env bash
# ida_clean.sh — 清空 IDA 暂存区 /mnt/d/ida_test(白名单外的本目标拷贝件全清)
# 调用时机: fw.sh 会话退出(ida_stop 之后)。中途 stop/start 不调用,暂存件跨 stop 保留。
# 白名单 = 我们自己的管线工具,禁止清理。
ALLOW='start_mcp.py|keepalive.py|idalog.txt'
removed=0
rm -rf /mnt/d/ida_test/submit 2>/dev/null    # 提交中转件(win_stage.sh 放的)一并清掉
rm -f /mnt/d/ida_test/.port-* /mnt/d/ida_test/boot_*.py /mnt/d/ida_test/STOP* /mnt/d/ida_test/READY* /mnt/d/ida_test/BEAT* 2>/dev/null
for f in /mnt/d/ida_test/*; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  echo "$base" | grep -qE "^($ALLOW)$" && continue
  [ -d "$f" ] && continue      # 目录不动(本暂存区约定只放文件)
  rm -f "$f" && { echo "[ida_clean] removed: $base"; removed=$((removed+1)); }
done
echo "[ida_clean] done ($removed staged file(s) removed; pipeline tools preserved)"
