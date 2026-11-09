#!/usr/bin/env bash
# ida_clean.sh — 清空 IDA 暂存区(路径来自配置,不写死)
# 调用时机: fw.sh **会话退出**(ida_stop 之后)。中途 stop/start 不调用 ⇒ 暂存件跨 stop 保留。
# 语义(既定语): 会话退出时把**本暂存区里白名单外的一切**清掉,包括 IDB/数据库副本
#   (它们是本会话拷进暂存区的副本,不是原始研究数据);白名单管线工具保留。
# 与 ida_stop.sh 的分工: stop 只停进程、默认不删 IDB(可能还有别的会话在用)。
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,6p' "$0" | sed 's/^# //'
  exit 0
fi
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
