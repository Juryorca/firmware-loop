#!/usr/bin/env bash
# 用法: ida_open.sh <Linux二进制路径> [会话名]   会话名缺省 = 文件本名
# idb_open 开库: 每库一个独立 idalib worker(真隔离);幂等: 同名会话已开则直接返回。
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  sed -n '2,3p' "$0" | sed 's/^# //'
  exit 0
fi
set -u
source "$(dirname "${BASH_SOURCE[0]}")/ida_env.sh"
source "$(dirname "${BASH_SOURCE[0]}")/ida_mcp_lib.sh"

BIN="${1:?用法: ida_open.sh <Linux二进制路径> [会话名]}"
NAME="${2:-$(basename "$BIN")}"
[ -f "$BIN" ] || { echo "no such file: $BIN" >&2; exit 2; }

cp -f "$BIN" "$FW_IDA_STAGE/$NAME" || { echo "复制到暂存目录失败: $FW_IDA_STAGE" >&2; exit 4; }
WPATH="$FW_IDA_WIN_STAGE\\$NAME"

SID=$(ida_mcp_session)
[ -n "$SID" ] || { echo "Supervisor 不可达: $FW_IDA_URL" >&2; exit 5; }
ida_mcp_post "$SID" '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null

REQ=$(python3 - "$WPATH" <<'PYJSON'
import json, sys
print(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": "idb_open", "arguments": {"input_path": sys.argv[1]}}}))
PYJSON
)
ida_mcp_post "$SID" "$REQ"
echo
