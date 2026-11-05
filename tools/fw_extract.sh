#!/usr/bin/env bash
# 用法: fw_extract.sh <firmware> <outdir> [--no-unblob]
# 目的: 用已装好的 binwalk + unblob 做提取(两者产出都保留),打印结构化摘要给 Agent。
#
# 资源保护(硬;timeout 只限时间,不限产物):
#   FW_EXTRACT_MAX_MB     输出目录占用上限(默认 512MB),超了立刻杀整组并 ABORT
#   FW_EXTRACT_MAX_FILES  文件数上限(默认 200000),超了同样 ABORT
#   FW_EXTRACT_MAX_DEPTH  递归深度(默认 6),直接传给 binwalk -d / unblob -d
#   另加 ulimit -f:单个文件不允许超过上限(解压炸弹只会写一个巨文件时也能兜住)
# 说明: 所有大小/数量判断都是"运行时轮询真实产物",不是估计;abort 一定整组回收子进程。
set -u

FW_ARG="${1:?用法: fw_extract.sh <firmware> <outdir> [--no-unblob]}"
OUT_ARG="${2:?缺输出目录}"
shift 2
NO_UNBLOB=0
[ "${1:-}" = "--no-unblob" ] && NO_UNBLOB=1

MAX_MB="${FW_EXTRACT_MAX_MB:-512}"
MAX_FILES="${FW_EXTRACT_MAX_FILES:-200000}"
MAX_DEPTH="${FW_EXTRACT_MAX_DEPTH:-6}"
CHILD_PGID=""

say() { printf '%s\n' "$*"; }

abort() {   # $1=原因;返回 9,由调用方决定退出(不能只在子 shell 里 exit)
  say "ABORT=$1"
  if [ -n "$CHILD_PGID" ]; then
    kill -TERM -- "-$CHILD_PGID" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$CHILD_PGID" 2>/dev/null || true
  fi
  return 9
}

cleanup() {
  if [ -n "$CHILD_PGID" ] && kill -0 -- "-$CHILD_PGID" 2>/dev/null; then
    kill -TERM -- "-$CHILD_PGID" 2>/dev/null || true
    sleep 1
    kill -KILL -- "-$CHILD_PGID" 2>/dev/null || true
  fi
  CHILD_PGID=""
}
on_signal() {   # 被 INT/TERM 打断:整组回收后立刻退出(不能继续 wait)
  say "ABORT=interrupted"
  cleanup
  exit 143
}
trap cleanup EXIT
trap on_signal INT TERM

# ---- 路径:先解析成绝对路径(修掉 cd 之后相对固件路径失效) ----
case "$FW_ARG" in
  /*) FW="$FW_ARG" ;;
  *)  FW="$(cd "$(dirname "$FW_ARG")" 2>/dev/null && pwd)/$(basename "$FW_ARG")" ;;
esac
mk_dirs() { mkdir -p "$1" || { say "ERROR=无法创建输出目录 $1"; exit 3; }; }
mk_dirs "$OUT_ARG"
OUT="$(cd "$OUT_ARG" && pwd)"
[ -f "$FW" ] || { say "FIRMWARE=$FW"; say "ERROR=固件不存在"; exit 2; }

say "FIRMWARE=$FW"
say "OUTDIR=$OUT"
say "LIMITS=max_mb=$MAX_MB max_files=$MAX_FILES max_depth=$MAX_DEPTH"

# 预检:剩余空间至少要有上限的 1.2 倍(避免一开始就注定写爆)
avail_mb=$(df -Pm "$OUT" 2>/dev/null | awk 'NR==2{print $4+0}')
if [ -n "${avail_mb:-}" ] && [ "$avail_mb" -lt $((MAX_MB * 12 / 10)) ]; then
  say "ABORT=free space ${avail_mb}MB < required $((MAX_MB * 12 / 10))MB"
  exit 9
fi

size_mb() { du -sm "$OUT" 2>/dev/null | awk '{print $1+0}'; }
count_files() { find "$OUT" -type f 2>/dev/null | wc -l; }

# 在独立进程组里跑,轮询真实产物;超限就整组杀掉(不能只杀父进程)
run_guarded() {   # $1=tag $2...=命令
  local tag="$1"; shift
  local log="$OUT/.${tag}.log"
  (
    ulimit -f $((MAX_MB * 2048)) 2>/dev/null || true   # 单文件上限(512B 块)
    exec "$@"
  ) >"$log" 2>&1 &
  local pid=$!
  CHILD_PGID=$pid                     # setsid 让 pid == pgid
  local last_count_check=$SECONDS
  while kill -0 "$pid" 2>/dev/null; do
    local mb
    mb=$(size_mb)
    if [ "$mb" -gt "$MAX_MB" ]; then
      say "== ${tag} =="; tail -10 "$log" 2>/dev/null
      abort "disk ${mb}MB > ${MAX_MB}MB (${tag})"; return 9
    fi
    if [ $((SECONDS - last_count_check)) -ge 2 ]; then
      last_count_check=$SECONDS
      local n
      n=$(count_files)
      if [ "$n" -gt "$MAX_FILES" ]; then
        say "== ${tag} =="; tail -10 "$log" 2>/dev/null
        abort "files ${n} > ${MAX_FILES} (${tag})"; return 9
      fi
    fi
    sleep 0.5
  done
  # 不用阻塞式 wait:先轮询到进程结束,shell 才能及时处理 INT/TERM(否则 trap 要等 wait 返回)
  wait "$pid" 2>/dev/null; local rc=$?
  CHILD_PGID=""
  say "== ${tag} == ("$rc")"
  tail -30 "$log" 2>/dev/null
  return "$rc"
}

rc_binwalk=0
if command -v binwalk >/dev/null 2>&1; then
  # binwalk 在 cwd 下产出;FW 已绝对化,cd 到 OUT 不会丢路径。
  # 注意:**不要**再套一层 `( ... )` 子 shell —— 父 shell 会把它当前台命令阻塞等待,
  # 于是 INT/TERM 的 trap 要等它结束才执行(实测会挂住)。
  cd "$OUT" || { say "ERROR=无法进入输出目录 $OUT"; exit 3; }
  run_guarded binwalk setsid binwalk -Me -d "$MAX_DEPTH" "$FW" || rc_binwalk=$?
  [ "$rc_binwalk" = "9" ] && exit 9
else
  say "BINWALK=missing"
fi

rc_unblob=0
if [ "$NO_UNBLOB" = "0" ]; then
  UNBLOB="${FW_UNBLOB:-$HOME/venvs/unblob/bin/unblob}"
  if [ -x "$UNBLOB" ]; then
    run_guarded unblob setsid "$UNBLOB" -e "$OUT/unblob" -d "$MAX_DEPTH" "$FW" || rc_unblob=$?
    [ "$rc_unblob" = "9" ] && exit 9
  else
    say "UNBLOB=missing"
  fi
fi

say "== summary =="
find "$OUT" -maxdepth 2 -type d 2>/dev/null | head -20
say "files=$(count_files)"
say "size_mb=$(size_mb)"
depth=$(find "$OUT" -type d 2>/dev/null | awk -F/ '{print NF}' | sort -n | tail -1)
say "max_dir_depth=$(( ${depth:-0} ))"
say "rc_binwalk=$rc_binwalk rc_unblob=$rc_unblob"
[ "$rc_binwalk" = "0" ] && [ "$rc_unblob" = "0" ]
