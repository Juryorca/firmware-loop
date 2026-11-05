#!/usr/bin/env bash
# ida_clean.sh — 清理 IDA 暂存区里的**临时件**(路径来自配置,不写死)
# 调用时机: fw.sh 会话退出(ida_stop 之后)。中途 stop/start 不调用,暂存件跨 stop 保留。
# 白名单 = 我们自己的管线工具,禁止清理;IDB/数据库默认**不动**(共享研究数据)。
set -u
source "$(dirname "${BASH_SOURCE[0]}")/ida_env.sh"
ALLOW='start_mcp.py|keepalive.py|idalog.txt'
STAGE_DIR="$FW_IDA_STAGE"
removed=0
rm -rf "$STAGE_DIR/submit" 2>/dev/null       # 提交中转件(win_stage.sh 放的)一并清掉
rm -f "$STAGE_DIR"/.port-* "$STAGE_DIR"/boot_*.py "$STAGE_DIR"/STOP* "$STAGE_DIR"/READY* "$STAGE_DIR"/BEAT* 2>/dev/null
for f in "$STAGE_DIR"/*; do
  [ -e "$f" ] || continue
  base=$(basename "$f")
  echo "$base" | grep -qE "^($ALLOW)$" && continue
  [ -d "$f" ] && continue      # 目录不动(本暂存区约定只放文件)
  rm -f "$f" && { echo "[ida_clean] removed: $base"; removed=$((removed+1)); }
done
echo "[ida_clean] done ($removed staged file(s) removed; pipeline tools preserved)"
