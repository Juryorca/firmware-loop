"""MCP Streamable HTTP 的 SSE 读取:增量、按请求 id 过滤、不能等连接关闭。"""

from __future__ import annotations

import http.server
import json
import threading
import time

from fwcore.mcp_client import MCPClient, ServerSpec


class _SSEHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        rid = req.get("id")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b": heartbeat\n\n")
        self.wfile.write(b'data: {"jsonrpc":"2.0","id":999,"result":{"other":true}}\n\n')
        self.wfile.flush()
        body = json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"ok": True}}).encode()
        self.wfile.write(b"data: " + body + b"\n\n")
        self.wfile.flush()
        # 故意保持连接不关；客户端必须在拿到匹配响应后立即返回。
        time.sleep(0.4)

    def log_message(self, format, *args):  # noqa: A002
        return


def test_http_sse_reads_incrementally_and_filters_request_id():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _SSEHandler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/mcp"
    c = MCPClient(ServerSpec(name="sse", command="", url=url, external=False), timeout=3)
    try:
        c.start()
        t0 = time.monotonic()
        result = c.request("tools/list")
        elapsed = time.monotonic() - t0
        assert result == {"ok": True}
        assert elapsed < 2.0
    finally:
        c.close()
        srv.shutdown()
        srv.server_close()
