#!/usr/bin/env bash
# 用法: ida_open.sh <Linux二进制路径> [会话名]   会话名缺省 = 文件本名
# idb_open 开库: 每库一个独立 idalib worker 进程(真隔离);幂等: 同名会话已开则直接返回
set -u
BIN="${1:?用法: ida_open.sh <Linux二进制路径> [会话名]}"
NAME="${2:-$(basename "$BIN")}"
STAGE=/mnt/d/ida_test
[ -f "$BIN" ] || { echo "no such file: $BIN" >&2; exit 2; }
cp -f "$BIN" "$STAGE/$NAME"
WPATH="D:/ida_test/$NAME"
BASE_URL="${IDA_MCP_URL:-http://127.0.0.1:8745/mcp}"
H=$(curl -s --noproxy '*' --max-time 10 -i -X POST "$BASE_URL" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"fc","version":"1"}}}')
SID=$(echo "$H" | grep -i '^Mcp-Session-Id:' | tr -d '\r' | awk '{print $2}')
curl -s --noproxy '*' --max-time 10 -X POST "$BASE_URL" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
curl -s --noproxy '*' --max-time 180 -X POST "$BASE_URL" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H "Mcp-Session-Id: $SID" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"idb_open\",\"arguments\":{\"input_path\":\"$WPATH\",\"preferred_session_id\":\"$NAME\"}}}"
echo
