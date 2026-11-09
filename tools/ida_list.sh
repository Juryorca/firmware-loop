#!/usr/bin/env bash
# 用法: ida_list.sh —— 列出已打开的数据库会话
set -u
source "$(dirname "${BASH_SOURCE[0]}")/ida_env.sh"
source "$(dirname "${BASH_SOURCE[0]}")/ida_mcp_lib.sh"
SID=$(ida_mcp_session)
[ -n "$SID" ] || { echo "Supervisor 不可达: $FW_IDA_URL" >&2; exit 5; }
ida_mcp_post "$SID" '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
ida_mcp_post "$SID" '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"idb_list","arguments":{}}}'
echo
