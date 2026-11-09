#!/usr/bin/env bash
# 用法: ida_start.sh [初始二进制的Linux路径]
# 启动 idalib-mcp supervisor(headless);幂等: 已在则直接返回。
# 全部路径/端口/日志来自 config(tools/ida/ida_env.sh ← fwcore/winenv.py)。
set -u
source "$(dirname "${BASH_SOURCE[0]}")/ida_env.sh"

code=$(curl -s --noproxy '*' --max-time 3 -o /dev/null -w '%{http_code}' "$FW_IDA_URL" 2>/dev/null || true)
[ "$code" != "000" ] && { echo "supervisor already up ($code)"; exit 0; }

EXE="$FW_IDA_MCP_EXE"
[ -x "$EXE" ] || [ -f "$EXE" ] || { echo "idalib-mcp 不存在: $EXE(用 FW_IDA_MCP_EXE 配置)" >&2; exit 3; }
[ -d "$FW_IDA_STAGE" ] || { echo "暂存目录不存在: $FW_IDA_STAGE(用 FW_IDA_STAGE 配置)" >&2; exit 3; }

# 只清**临时**标记文件;不删 IDB/数据库(那是共享研究数据)。需要彻底清理用 --purge-idb。
rm -f "$FW_IDA_STAGE"/STOP* "$FW_IDA_STAGE"/READY* "$FW_IDA_STAGE"/BEAT* \
      "$FW_IDA_STAGE"/.port-* "$FW_IDA_STAGE"/boot_*.py 2>/dev/null
if [ "${1:-}" = "--purge-idb" ]; then
  shift
  rm -f "$FW_IDA_STAGE"/*.i64 "$FW_IDA_STAGE"/*.id0 "$FW_IDA_STAGE"/*.id1 \
        "$FW_IDA_STAGE"/*.id2 "$FW_IDA_STAGE"/*.nam "$FW_IDA_STAGE"/*.til 2>/dev/null
fi

ARGS=()
INIT="${1:-}"
if [ -n "$INIT" ]; then
  [ -f "$INIT" ] || { echo "初始二进制不存在: $INIT" >&2; exit 2; }
  BASE="$(basename "$INIT")"
  if [ "$(dirname "$INIT")" != "$FW_IDA_STAGE" ]; then
    cp -f "$INIT" "$FW_IDA_STAGE/$BASE" || { echo "复制到暂存目录失败: $FW_IDA_STAGE" >&2; exit 4; }
  fi
  ARGS=("$FW_IDA_WIN_STAGE\\$BASE")
fi

# 从 URL 解析 host/port(端口不再写死)
eval "$(python3 - "$FW_IDA_URL" <<'PY'
import sys, urllib.parse
u = urllib.parse.urlsplit(sys.argv[1])
print(f"HOST={u.hostname or '127.0.0.1'}")
print(f"PORT={u.port or 8745}")
PY
)"

mkdir -p "$(dirname "$FW_IDA_LOG")" 2>/dev/null || true
setsid "$EXE" --host "$HOST" --port "$PORT" "${ARGS[@]}" >"$FW_IDA_LOG" 2>&1 < /dev/null &
SUP_PID=$!
printf '%s\n' "$SUP_PID" > "$FW_IDA_STAGE/supervisor.pid" 2>/dev/null || true

for i in $(seq 1 75); do
  sleep 2
  code=$(curl -s --noproxy '*' --max-time 3 -o /dev/null -w '%{http_code}' "$FW_IDA_URL" 2>/dev/null || true)
  [ "$code" != "000" ] && { echo "supervisor ready ($HOST:$PORT, pid=$SUP_PID)"; exit 0; }
done
echo "TIMEOUT: supervisor 未就绪($HOST:$PORT);日志: $FW_IDA_LOG" >&2
tail -8 "$FW_IDA_LOG" 2>/dev/null
echo "若报 'worker exited early': 先激活 idalib —" >&2
echo "  $FW_IDA_WIN_PYTHON $FW_IDA_WIN_DIR\\idalib\\python\\py-activate-idalib.py -d $FW_IDA_WIN_DIR" >&2
exit 1
