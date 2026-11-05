#!/usr/bin/env bash
# 用法: ida_call.sh <tool_name> '<json_args>'   json 里必须含 database=<会话id>(ida_list.sh 可查)
set -u
source "$(dirname "${BASH_SOURCE[0]}")/ida_env.sh"
source "$(dirname "${BASH_SOURCE[0]}")/ida_mcp_lib.sh"

TOOL="${1:?用法: ida_call.sh <tool_name> '<json_args>'}"
ARGS="${2:-{\}}"
SID=$(ida_mcp_session)
[ -n "$SID" ] || { echo "Supervisor 不可达: $FW_IDA_URL" >&2; exit 5; }
ida_mcp_post "$SID" '{"jsonrpc":"2.0","method":"notifications/initialized"}' >/dev/null
REQ=$(python3 - "$TOOL" "$ARGS" <<'PYJSON'
import json, sys
tool, raw = sys.argv[1], sys.argv[2]
try:
    args = json.loads(raw)
except json.JSONDecodeError as e:
    print(f"参数不是合法 JSON: {e}", file=sys.stderr)
    raise SystemExit(2)
print(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                  "params": {"name": tool, "arguments": args}}))
PYJSON
) || exit 2
ida_mcp_post "$SID" "$REQ"
echo
