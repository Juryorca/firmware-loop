"""独立 MCP 客户端:用假 stdio server 验证协议、探针语义与不误报。"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fwcore.mcp_client import (
    MCPClient,
    MCPConfigError,
    MCPError,
    ServerSpec,
    load_servers,
    probe,
)

FAKE = r"""
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    m, rid = msg.get("method"), msg.get("id")
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05",
              "serverInfo": {"name": "fake", "version": "0.1"}, "capabilities": {}}})
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": [
            {"name": "echo", "description": "回声", "inputSchema": {"type": "object",
             "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}})
    elif m == "tools/call":
        args = (msg.get("params") or {}).get("arguments") or {}
        send({"jsonrpc": "2.0", "id": rid, "result": {"content": [
            {"type": "text", "text": "echo:" + str(args.get("text"))}]}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}})
"""

DEAD = "import sys; sys.exit(3)\n"


def _server(tmp_path, body: str, name: str = "fake.py") -> ServerSpec:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return ServerSpec(name="fake", command=sys.executable, args=[str(p)])


def test_client_initialize_list_and_call(tmp_path):
    spec = _server(tmp_path, FAKE)
    with MCPClient(spec) as c:
        info = c.initialize()
        assert info["serverInfo"]["name"] == "fake"
        tools = c.list_tools()
        assert tools[0]["name"] == "echo" and tools[0]["input_schema"]["required"] == [
            "text"
        ]
        out = c.call_tool("echo", {"text": "hi"})
        assert out["text"] == "echo:hi" and out["is_error"] is False


def test_probe_reports_reachable_with_tools(tmp_path):
    r = probe(_server(tmp_path, FAKE))
    assert (
        r["status"] == "reachable"
        and r["tools"] == ["echo"]
        and r["evidence"] == "reachable"
    )


def test_probe_reports_unreachable_when_server_dies(tmp_path):
    """服务起不来 ⇒ 必须是 unreachable,不能因为"配置存在"就报可用。"""
    r = probe(_server(tmp_path, DEAD), timeout=5)
    assert r["status"] == "unreachable" and r["evidence"] == "configured"


def test_probe_reports_disabled_and_http_unreachable():
    assert (
        probe(ServerSpec(name="x", command="true", enabled=False))["status"]
        == "disabled"
    )
    r = probe(ServerSpec(name="y", command="", url="http://127.0.0.1:1/mcp"), timeout=1)
    assert r["status"] == "unreachable"


def test_http_transport_initialize_list_and_call():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            m, rid = req.get("method"), req.get("id")
            if m == "initialize":
                out = {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "h", "version": "1"},
                        "capabilities": {},
                    },
                }
            elif m == "tools/list":
                out = {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "tools": [
                            {
                                "name": "echo",
                                "description": "e",
                                "inputSchema": {"type": "object"},
                            }
                        ]
                    },
                }
            elif m == "tools/call":
                out = {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"content": [{"type": "text", "text": "echo:http"}]},
                }
            else:
                out = {"jsonrpc": "2.0", "id": rid, "result": {}}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        spec = ServerSpec(
            name="http", command="", url=f"http://127.0.0.1:{srv.server_port}/mcp"
        )
        with MCPClient(spec, timeout=5) as c:
            c.initialize()
            assert c.list_tools()[0]["name"] == "echo"
            assert c.call_tool("echo", {"text": "hi"})["text"] == "echo:http"
    finally:
        srv.shutdown()
        th.join(timeout=2)


def test_load_servers_corrupt_config_is_explicit(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{bad", encoding="utf-8")
    with pytest.raises(MCPConfigError):
        load_servers([str(p)])


def test_bridge_from_config_reports_config_error(tmp_path):
    from fwcore.mcp_runtime import bridge_from_config
    from fwcore.tools import Registry

    p = tmp_path / "bad.json"
    p.write_text("{bad", encoding="utf-8")
    b = bridge_from_config(Registry(), str(tmp_path), [str(p)])
    st = b.start()
    assert st["_config"]["status"] == "config_error"
    assert "JSON" in st["_config"]["detail"]


def test_load_servers_follows_pi_project_precedence(tmp_path):
    own = tmp_path / "mcp.json"
    own.write_text(
        json.dumps(
            {"mcpServers": {"a": {"command": "own-a"}, "b": {"command": "own-b"}}}
        ),
        encoding="utf-8",
    )
    pi = tmp_path / "pi.json"
    pi.write_text(
        json.dumps(
            {"mcpServers": {"a": {"command": "pi-a"}, "c": {"command": "pi-c"}}}
        ),
        encoding="utf-8",
    )
    servers = load_servers([str(own), str(pi)])
    # Pi: later project entries replace earlier global entries.
    assert servers["a"].command == "pi-a"
    assert servers["c"].command == "pi-c" and "b" in servers


def test_unreachable_probe_does_not_leak_child(tmp_path):
    """探针失败后子进程必须已被回收(不留孤儿)。"""
    spec = _server(tmp_path, "import time\nwhile True: time.sleep(0.5)\n")
    r = probe(spec, timeout=1)
    assert r["status"] == "unreachable"
    assert "超时" in r["detail"] or "关闭" in r["detail"]


def test_mcp_child_pgid_registered_for_orphan_cleanup(tmp_path):
    """MCP 子进程要登记进 run 的 .pgids(硬崩溃后 claim/recover/看护能清孤儿)。"""
    import os

    from fwcore.watchdog import _proc_starttime

    pg = tmp_path / "art" / ".pgids"
    spec = ServerSpec(
        name="sleepy", command=sys.executable, args=["-c", "import time; time.sleep(5)"]
    )
    c = MCPClient(spec, timeout=5, pgids_path=str(pg))
    c.start()
    try:
        parts = pg.read_text(encoding="utf-8").strip().split()
        assert len(parts) == 2, parts
        assert c._proc is not None
        assert parts[0] == str(os.getpgid(c._proc.pid))
        assert int(parts[1]) == _proc_starttime(c._proc.pid)
    finally:
        c.close()


NOISY = r"""
import json, sys
sys.stderr.write("x" * 300000); sys.stderr.flush()

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line); rid = msg.get("id"); m = msg.get("method")
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05",
              "serverInfo": {"name": "noisy", "version": "1"}, "capabilities": {}}})
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": []}})
"""


def test_noisy_stderr_does_not_block_stdio(tmp_path):
    with MCPClient(_server(tmp_path, NOISY), timeout=5) as c:
        assert c.initialize()["serverInfo"]["name"] == "noisy"
        assert c.list_tools() == []
        assert len(c.last_stderr()) <= 8192


def test_load_servers_rejects_non_string_url_and_command(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(
        json.dumps({"mcpServers": {"x": {"command": "true", "url": 123}}}),
        encoding="utf-8",
    )
    with pytest.raises(MCPConfigError):
        load_servers([str(p)])
    p.write_text(json.dumps({"mcpServers": {"x": {"command": 123}}}), encoding="utf-8")
    with pytest.raises(MCPConfigError):
        load_servers([str(p)])


def test_http_transport_rejects_non_http_url_before_urllib():
    spec = ServerSpec(name="file", command="", url="file:///etc/passwd")
    with pytest.raises(MCPError):
        MCPClient(spec).request("initialize")


FORKED_IGNORING_TERM = r"""
import os, signal, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
pidfile = os.environ["FAKE_CHILD_PID"]
child = os.fork()
if child == 0:
    with open(pidfile, "w") as f:
        f.write(str(os.getpid()))
    while True:
        time.sleep(0.2)
while True:
    time.sleep(0.2)
"""


def test_close_kills_forked_child_that_ignores_sigterm(tmp_path):
    """Pi closes the whole process group: a cooperative parent is not enough."""
    import os
    import time

    pidfile = tmp_path / "child.pid"
    spec = ServerSpec(
        name="forked",
        command=sys.executable,
        args=["-c", FORKED_IGNORING_TERM],
        env={"FAKE_CHILD_PID": str(pidfile)},
    )
    client = MCPClient(spec, timeout=5)
    client.start()
    assert client._proc is not None
    parent = client._proc.pid
    for _ in range(100):
        if pidfile.is_file() and pidfile.read_text().strip():
            break
        time.sleep(0.02)
    else:
        client.close()
        raise AssertionError("forked child did not start")

    child = int(pidfile.read_text())
    client.close()
    for pid in (parent, child):
        for _ in range(100):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        else:
            raise AssertionError(f"process {pid} survived MCP close")


def test_http_read_only_retry_but_not_tools_call():
    class Handler(BaseHTTPRequestHandler):
        list_attempts = 0
        call_attempts = 0

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            req = json.loads(self.rfile.read(n) or b"{}")
            m, rid = req.get("method"), req.get("id")
            if m == "tools/list":
                Handler.list_attempts += 1
                if Handler.list_attempts <= 2:
                    self.send_response(503)
                    self.end_headers()
                    self.wfile.write(b"temporary")
                    return
                out = {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "tools": [{"name": "echo", "inputSchema": {"type": "object"}}]
                    },
                }
            elif m == "tools/call":
                Handler.call_attempts += 1
                self.send_response(503)
                self.end_headers()
                self.wfile.write(b"do not retry calls")
                return
            elif m == "initialize":
                out = {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "serverInfo": {"name": "retry", "version": "1"},
                    },
                }
            else:
                out = {"jsonrpc": "2.0", "id": rid, "result": {}}
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    try:
        spec = ServerSpec(
            name="retry", command="", url=f"http://127.0.0.1:{srv.server_port}/mcp"
        )
        client = MCPClient(spec, timeout=5)
        client.initialize()
        assert client.list_tools()[0]["name"] == "echo"
        assert Handler.list_attempts == 3
        with pytest.raises(MCPError):
            client.call_tool("echo", {})
        assert Handler.call_attempts == 1
    finally:
        srv.shutdown()
        th.join(timeout=2)
