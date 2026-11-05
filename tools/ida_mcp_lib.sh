#!/usr/bin/env bash
# MCP 调用公共部分(initialize + session + tools/call),URL 来自配置。
ida_mcp_post() {  # $1=session(可空) $2=json
  curl -s --noproxy '*' --max-time "${IDA_MCP_TIMEOUT:-180}" -X POST "$FW_IDA_URL" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    ${1:+-H "Mcp-Session-Id: $1"} -d "$2"
}
ida_mcp_session() {
  local h
  h=$(curl -s --noproxy '*' --max-time 10 -i -X POST "$FW_IDA_URL" \
    -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"fwcore","version":"0.1"}}}')
  echo "$h" | grep -i '^Mcp-Session-Id:' | tr -d '\r' | awk '{print $2}'
}
