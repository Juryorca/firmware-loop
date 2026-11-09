#!/usr/bin/env bash
# 用法: ida_stop.sh [--all] [--purge]
#   默认: 只停**本环境**的 supervisor(由 start 写的 pidfile 记录)与本暂存目录下的 worker 注册;
#         不动其它实例的进程,不删 IDB/研究数据。
#   --all    : 额外按镜像名 taskkill(会波及同机其它实例;显式才做)。
#   --purge  : 删除暂存目录里的 IDB/数据库(默认**不删**,那是共享研究数据)。
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,6p' "$0" | sed 's/^# //'
  exit 0
fi
set -u
source "$(dirname "${BASH_SOURCE[0]}")/ida_env.sh"

ALL=0; PURGE=0
for a in "$@"; do
  case "$a" in
    --all) ALL=1 ;;
    --purge) PURGE=1 ;;
    *) echo "未知参数: $a" >&2; exit 2 ;;
  esac
done

stopped=0
PIDFILE="$FW_IDA_STAGE/supervisor.pid"
if [ -f "$PIDFILE" ]; then
  SUP=$(head -1 "$PIDFILE" 2>/dev/null || true)
  if [ -n "${SUP:-}" ]; then
    if kill -0 "$SUP" 2>/dev/null; then
      kill -TERM "$SUP" 2>/dev/null || true
      for _ in $(seq 1 15); do kill -0 "$SUP" 2>/dev/null || break; sleep 0.2; done
      kill -0 "$SUP" 2>/dev/null && kill -KILL "$SUP" 2>/dev/null || true
      stopped=$((stopped+1)); echo "stopped supervisor pid=$SUP"
    fi
  fi
  rm -f "$PIDFILE"
fi

# worker 注册表:只处理**指向本暂存目录**的条目(其它实例一律不动)
INST="${FW_IDA_INSTANCES:-}"
if [ -n "$INST" ] && [ -d "$INST" ]; then
  WINPREFIX="$(printf '%s' "$FW_IDA_WIN_STAGE" | tr '[:upper:]' '[:lower:]')"
  for j in "$INST"/*.json; do
    [ -f "$j" ] || continue
    info=$(python3 - "$j" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
except (OSError, json.JSONDecodeError):
    print("")
    raise SystemExit
if d.get("backend") != "worker":
    raise SystemExit
print(f"{d.get('pid','')}\t{d.get('idb_path') or d.get('input_path') or ''}")
PY
)
    [ -n "$info" ] || continue
    pid="${info%%	*}"; path="${info#*	}"
    lowpath=$(printf '%s' "$path" | tr '[:upper:]' '[:lower:]')
    case "$lowpath" in
      "$WINPREFIX"*)
        [ -n "$pid" ] && taskkill.exe /PID "$pid" /F >/dev/null 2>&1 && stopped=$((stopped+1)) || true
        rm -f "$j"
        ;;
      *) : ;;                       # 别的暂存目录/其它用户实例:不动
    esac
  done
else
  echo "提示: 未配置 FW_IDA_INSTANCES(或目录不存在),跳过 worker 注册表清理"
fi

if [ "$ALL" = "1" ]; then
  echo "警告: --all 会按镜像名终止同机**所有** idalib-mcp 实例"
  taskkill.exe /IM idalib-mcp.exe /F >/dev/null 2>&1 && stopped=$((stopped+1)) || true
fi

# 只清临时标记;IDB 只有显式 --purge 才删
rm -f "$FW_IDA_STAGE"/STOP* "$FW_IDA_STAGE"/READY* "$FW_IDA_STAGE"/BEAT* \
      "$FW_IDA_STAGE"/.port-* "$FW_IDA_STAGE"/boot_*.py 2>/dev/null
if [ "$PURGE" = "1" ]; then
  echo "警告: --purge 会删除 $FW_IDA_STAGE 下的 IDB/数据库"
  rm -f "$FW_IDA_STAGE"/*.i64 "$FW_IDA_STAGE"/*.id0 "$FW_IDA_STAGE"/*.id1 \
        "$FW_IDA_STAGE"/*.id2 "$FW_IDA_STAGE"/*.nam "$FW_IDA_STAGE"/*.til 2>/dev/null
fi

echo "stopped=$stopped (stage=$FW_IDA_STAGE)"
