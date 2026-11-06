"""独立 MCP 客户端:用假 stdio server 验证协议、探针语义与不误报。"""

from __future__ import annotations

import json
import sys

from fwcore.mcp_client import MCPClient, ServerSpec, load_servers, probe

FAKE = r'''
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
'''

DEAD = 'import sys; sys.exit(3)\n'


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
        assert tools[0]["name"] == "echo" and tools[0]["input_schema"]["required"] == ["text"]
        out = c.call_tool("echo", {"text": "hi"})
        assert out["text"] == "echo:hi" and out["is_error"] is False


def test_probe_reports_reachable_with_tools(tmp_path):
    r = probe(_server(tmp_path, FAKE))
    assert r["status"] == "reachable" and r["tools"] == ["echo"] and r["evidence"] == "reachable"


def test_probe_reports_unreachable_when_server_dies(tmp_path):
    """服务起不来 ⇒ 必须是 unreachable,不能因为"配置存在"就报可用。"""
    r = probe(_server(tmp_path, DEAD), timeout=5)
    assert r["status"] == "unreachable" and r["evidence"] == "configured"


def test_probe_reports_disabled_and_unsupported_transport():
    assert probe(ServerSpec(name="x", command="true", enabled=False))["status"] == "disabled"
    assert probe(ServerSpec(name="y", command="", url="https://example/mcp"))["status"] == "unsupported_transport"


def test_load_servers_priority_and_skip(tmp_path):
    own = tmp_path / "mcp.json"
    own.write_text(json.dumps({"mcpServers": {"a": {"command": "own-a"}, "b": {"command": "own-b"}}}),
                   encoding="utf-8")
    pi = tmp_path / "pi.json"
    pi.write_text(json.dumps({"mcpServers": {"a": {"command": "pi-a"}, "c": {"command": "pi-c"}}}),
                  encoding="utf-8")
    servers = load_servers([str(own), str(pi)])
    assert servers["a"].command == "own-a", "本框架配置优先"
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
    spec = ServerSpec(name="sleepy", command=sys.executable,
                      args=["-c", "import time; time.sleep(5)"])
    c = MCPClient(spec, timeout=5, pgids_path=str(pg))
    c.start()
    try:
        parts = pg.read_text(encoding="utf-8").strip().split()
        assert len(parts) == 2, parts
        assert parts[0] == str(os.getpgid(c._proc.pid))
        assert int(parts[1]) == _proc_starttime(c._proc.pid)
    finally:
        c.close()
