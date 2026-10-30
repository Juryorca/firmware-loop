#!/usr/bin/env bash
# 用法: ida_tools.sh —— 列出可用的 MCP 工具与参数说明
BASE_URL="${IDA_MCP_URL:-http://127.0.0.1:8745/mcp}"
H=$(curl -s --noproxy '*' --max-time 10 -i -X POST "$BASE_URL" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"fc","version":"1"}}}')
SID=$(echo "$H" | grep -i '^Mcp-Session-Id:' | tr -d '\r' | awk '{print $2}')
curl -s --noproxy '*' --max-time 10 -X POST "$BASE_URL" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
curl -s --noproxy '*' --max-time 30 -X POST "$BASE_URL" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -H "Mcp-Session-Id: $SID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
echo
