#!/usr/bin/env bash
# win_stage.sh — 把文件送到「Windows 侧可引用的中转目录」,并打印 Windows 路径
# 用途: 浏览器在 Windows 上跑,upload_file / 表单附件只认 Windows 路径 ✗ Linux 路径无效
# 用法: tools/win_stage.sh <文件...>      (只接受工作区内文件)
# 落点: /mnt/d/ida_test/submit/  (= Windows D:\ida_test\submit\);收尾由 tools/ida_clean.sh 清掉
set -u
WS="$(cd "$(dirname "$0")/.." && pwd)"
STAGE=/mnt/d/ida_test/submit
[ $# -ge 1 ] || { echo "用法: tools/win_stage.sh <文件...>" >&2; exit 2; }
mkdir -p "$STAGE" || { echo "无法创建中转目录 $STAGE" >&2; exit 2; }
rc=0
for f in "$@"; do
  [ -f "$f" ] || { echo "跳过(不是文件): $f" >&2; rc=2; continue; }
  abs=$(cd "$(dirname "$f")" && pwd)/$(basename "$f")
  case "$abs" in
    "$WS"/*) ;;
    *) echo "拒绝(工作区外): $abs" >&2; rc=2; continue;;
  esac
  base=$(basename "$abs")
  cp -f "$abs" "$STAGE/$base" || { echo "复制失败: $base" >&2; rc=2; continue; }
  printf '%s\n' "D:\\ida_test\\submit\\$base"
done
[ $rc -eq 0 ] && echo "(放到 Windows 侧: D:\\ida_test\\submit\\;用完 tools/ida_clean.sh 可清)" >&2
exit $rc
