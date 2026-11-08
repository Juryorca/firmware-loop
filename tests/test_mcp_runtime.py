"""MCP 真正接进 Runtime:注册 → 模型选择 → 执行 → 记录 → 清理(含失败路径)。"""

from __future__ import annotations

import json
import os
import sys
import time

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.mcp_client import ServerSpec
from fwcore.mcp_runtime import MCPBridge, tool_name
from fwcore.models import ScriptedAdapter
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner
from fwcore.tools import Registry, ToolSpec

FAKE = r'''
import json, sys, time

def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

TOOLS = [
    {"name": "echo", "description": "回声", "inputSchema": {"type": "object",
     "properties": {"text": {"type": "string"}}, "required": ["text"]}},
    {"name": "boom", "description": "总是报错", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "slow", "description": "慢", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "big", "description": "大输出", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "big_struct", "description": "大结构化结果", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "big_error", "description": "大错误结果", "inputSchema": {"type": "object", "properties": {}}},
]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    m, rid = msg.get("method"), msg.get("id")
    p = msg.get("params") or {}
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": "2024-11-05",
              "serverInfo": {"name": "fake", "version": "0.1"}, "capabilities": {}}})
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif m == "tools/call":
        name = p.get("name")
        if name == "echo":
            send({"jsonrpc": "2.0", "id": rid,
                  "result": {"content": [{"type": "text", "text": "echo:" + str((p.get("arguments") or {}).get("text"))}]}})
        elif name == "boom":
            send({"jsonrpc": "2.0", "id": rid,
                  "result": {"isError": True, "content": [{"type": "text", "text": "boom-failed"}]}})
        elif name == "slow":
            time.sleep(5)
            send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "late"}]}})
        elif name == "big":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "HEAD" + ("x" * 30000) + "TAIL"}],
                "structuredContent": {"answer": 42}}})
        elif name == "big_struct":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "ok"}],
                "structuredContent": {"blob": "S" * 30000}}})
        elif name == "big_error":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": "ERRHEAD" + ("e" * 30000) + "ERRTAIL"}],
                "structuredContent": {"detail": "D" * 30000}}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no such tool"}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}})
'''


def _spec(tmp_path, body: str = FAKE, name: str = "fake.py", server: str = "fake") -> tuple[ServerSpec, str]:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    # 配置里声明 external=False(本地分析服务器);手工构造 ServerSpec 时必须显式带上,
    # 因为没有声明 = external(默认拒绝)——这条语义就是本轮的授权边界
    spec = ServerSpec(name=server, command=sys.executable, args=[str(p)], external=False)
    cfg = tmp_path / f"{server}.json"
    cfg.write_text(json.dumps({"mcpServers": {server: {"command": sys.executable, "args": [str(p)],
                                            "external": False}}}),
                   encoding="utf-8")
    return spec, str(cfg)


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def test_tool_name_is_openai_compatible():
    assert tool_name("chrome-live", "take.snapshot") == "mcp__chrome_live__take_snapshot"
    assert len(tool_name("s", "x" * 100)) <= 64


def test_bridge_registers_tools_and_maps_schema(tmp_path):
    spec, _ = _spec(tmp_path)
    reg = Registry()
    b = MCPBridge(root=str(tmp_path), registry=reg, specs=[spec])
    st = b.start()
    try:
        assert st["fake"]["status"] == "ready"
        names = {t["name"] for t in reg.describe()}
        assert {"mcp__fake__echo", "mcp__fake__boom", "mcp__fake__slow"} <= names
        echo = next(t for t in reg.describe() if t["name"] == "mcp__fake__echo")
        assert echo["input_schema"]["required"] == ["text"]
    finally:
        b.close()


def test_runner_executes_mcp_tool_and_records_provider(tmp_path):
    spec, cfg = _spec(tmp_path)
    con, dom, root = _setup(tmp_path)
    call = {"id": "c1", "type": "function",
            "function": {"name": "mcp__fake__echo", "arguments": '{"text":"hi"}'}}
    r = Runner(con, root, ScriptedAdapter(script=[{"content": "", "tool_calls": [call]},
                                                 {"content": "FINAL: ok", "tool_calls": []}]),
               limits=Limits(max_model_calls=4),
               policy=Policy(authorization=Authz(roots=[root], allow_high_risk=True, reason="test")),
               mcp=True, mcp_config_paths=[cfg])
    res = r.run(r.start("t1"), "t1")
    assert res.state == "finished"
    row = con.execute("SELECT provider, capability, status FROM tool_calls ORDER BY created_at LIMIT 1").fetchone()
    assert row["status"] == "ok" and row["provider"] == "mcp:fake"
    tool_msgs = [m for m in res.history if m.get("role") == "tool"]
    assert tool_msgs and "echo:hi" in tool_msgs[0]["content"]
    assert r.mcp_bridge is not None
    assert r.mcp_bridge.clients == {}, "运行结束必须关闭 MCP 客户端"


def test_unreachable_server_is_reported_but_does_not_break_run(tmp_path):
    dead = tmp_path / "dead.py"
    dead.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
    cfg = tmp_path / "dead.json"
    cfg.write_text(json.dumps({"mcpServers": {"dead": {"command": sys.executable, "args": [str(dead)],
                                             "external": False}}}),
                   encoding="utf-8")
    con, dom, root = _setup(tmp_path)
    r = Runner(con, root, ScriptedAdapter(script=[{"content": "FINAL: ok", "tool_calls": []}]),
               limits=Limits(max_model_calls=2),
               policy=Policy(authorization=Authz(roots=[root], reason="test")),
               mcp=True, mcp_config_paths=[cfg])
    res = r.run(r.start("t1"), "t1")
    assert res.state == "finished"
    assert r.mcp_bridge is not None
    assert r.mcp_bridge.status["dead"]["status"] == "unreachable"
    assert not [t for t in r.registry.describe() if t["name"].startswith("mcp__dead__")]


def test_mcp_tool_error_and_timeout_are_distinct(tmp_path):
    spec, _ = _spec(tmp_path)
    con, dom, root = _setup(tmp_path)
    reg = Registry()
    b = MCPBridge(root=root, registry=reg, specs=[spec], call_timeout=0.5)
    b.start()
    try:
        from fwcore.tools import ToolContext

        ctx = ToolContext(domain=dom, root=root,
                          policy=Policy(authorization=Authz(roots=[root], allow_high_risk=True)))
        ok = reg.call(ctx, "mcp__fake__echo", {"text": "x"})
        err = reg.call(ctx, "mcp__fake__boom", {})
        tmo = reg.call(ctx, "mcp__fake__slow", {})
        assert ok["status"] == "ok"
        assert err["status"] == "error" and "boom-failed" in err["error"]
        assert tmo["status"] == "timeout"
    finally:
        b.close()
    time.sleep(0.2)
    assert b.clients == {}


def test_mcp_large_result_uses_pi_middle_truncation_and_structured(tmp_path):
    spec, _ = _spec(tmp_path)
    con, dom, root = _setup(tmp_path)
    reg = Registry()
    b = MCPBridge(root=root, registry=reg, specs=[spec])
    b.start()
    try:
        from fwcore.tools import ToolContext
        ctx = ToolContext(domain=dom, root=root,
                          policy=Policy(authorization=Authz(roots=[root], allow_high_risk=True)))
        out = reg.call(ctx, "mcp__fake__big", {})
        assert out["status"] == "ok" and out["structured"] == {"answer": 42}
        assert out["truncated"] is True
        assert out["text"].startswith("Warning: truncated output")
        assert "HEAD" in out["text"] and "TAIL" in out["text"]
        assert "chars truncated" in out["text"]
        path = out["full_output_path"]
        assert path and os.path.isfile(path)
        try:
            assert "TAIL" in open(path, encoding="utf-8").read()
        finally:
            os.unlink(path)
    finally:
        b.close()


def test_mcp_large_structured_is_bounded_and_full_json_saved(tmp_path):
    spec, _ = _spec(tmp_path)
    con, dom, root = _setup(tmp_path)
    reg = Registry()
    b = MCPBridge(root=root, registry=reg, specs=[spec])
    b.start()
    paths = []
    try:
        from fwcore.tools import ToolContext, fs_read
        ctx = ToolContext(domain=dom, root=root,
                          policy=Policy(authorization=Authz(roots=[root], allow_high_risk=True)))
        out = reg.call(ctx, "mcp__fake__big_struct", {})
        assert out["status"] == "ok"
        assert out["structured"]["_truncated"] is True
        path = out["structured_full_output_path"]
        paths.append(path)
        assert path and os.path.isfile(path)
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        assert payload == {"blob": "S" * 30000}
        # 模型可见的结构化对象只剩摘要，不携带 30 KB 字段；完整 JSON 可读。
        assert len(json.dumps(out["structured"], ensure_ascii=False)) < 1000
        read = fs_read(ctx, {"path": path})
        assert read["status"] == "ok" and "S" * 100 in read["text"]
    finally:
        b.close()
        for path in paths:
            if path and os.path.exists(path):
                os.unlink(path)


def test_mcp_large_error_uses_middle_truncation_and_saves_full(tmp_path):
    spec, _ = _spec(tmp_path)
    con, dom, root = _setup(tmp_path)
    reg = Registry()
    b = MCPBridge(root=root, registry=reg, specs=[spec])
    b.start()
    paths = []
    try:
        from fwcore.tools import ToolContext, fs_read
        ctx = ToolContext(domain=dom, root=root,
                          policy=Policy(authorization=Authz(roots=[root], allow_high_risk=True)))
        out = reg.call(ctx, "mcp__fake__big_error", {})
        assert out["status"] == "error"
        assert out["truncated"] is True and "chars truncated" in out["error"]
        path = out["full_output_path"]
        paths.append(path)
        assert path and "ERRTAIL" in open(path, encoding="utf-8").read()
        # 完整临时输出必须能被 fs_read 读回，否则 full_output_path 没有意义。
        read = fs_read(ctx, {"path": path})
        assert read["status"] == "ok" and "ERRTAIL" in read["text"]
        assert out["structured"]["_truncated"] is True
        paths.append(out["structured_full_output_path"])
    finally:
        b.close()
        for path in paths:
            if path and os.path.exists(path):
                os.unlink(path)


def test_tools_list_changed_refresh_replaces_stale_tools(tmp_path):
    spec = ServerSpec(name="fake", command="x", args=[], external=False)
    reg = Registry()
    reg.register(ToolSpec("mcp__fake__old", "old", {"type": "object", "properties": {}},
                          lambda ctx, args: {"status": "ok"}))

    class Changing:
        supports_resources = False
        tools_changed = True
        def list_tools(self):
            return [{"name": "new", "description": "new",
                     "input_schema": {"type": "object", "properties": {}}}]
        def close(self):
            pass

    bridge = MCPBridge(root=str(tmp_path), registry=reg, specs=[spec])
    bridge.clients["fake"] = Changing()
    bridge.registered["fake"] = ["mcp__fake__old"]
    bridge.claimed["mcp__fake__old"] = ("fake", "old")
    bridge.status["fake"] = {"status": "ready", "server": "fake"}
    assert bridge.refresh_tools(force=True) is True
    assert "mcp__fake__old" not in reg.specs
    assert "mcp__fake__new" in reg.specs
    assert bridge.registered["fake"] == ["mcp__fake__new"]
    assert bridge.clients["fake"].tools_changed is False
