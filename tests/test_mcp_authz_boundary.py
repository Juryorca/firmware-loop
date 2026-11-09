"""MCP 的授权边界:启动前必须有授权;external 由配置声明(不是名字猜);撞名必须确定/拒绝。

这些用例直接打"安全语义",不依赖 Runner 的模型行为:
- 没有授权 ⇒ **不启动**服务器(不产生子进程/外网副作用)
- 过期/撤销/allow_mcp=false ⇒ 同样不启动;MCP 工具调用被 Policy 拒
- external:配置声明 false 才可自主;未声明 ⇒ 需要 confirm 或授权显式允许
- 规范化撞名 ⇒ 确定性消歧;消歧还撞 ⇒ 明确拒绝(绝不静默绑到别人的工具)
"""

from __future__ import annotations

import json
import os
import sys

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.mcp_client import ServerSpec
from fwcore.mcp_runtime import MCPBridge, disambiguated_name, tool_name
from fwcore.models import ScriptedAdapter
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner
from fwcore.tools import ToolContext

FAKE = r"""
import json, os, sys

def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

if os.environ.get("FAKE_PIDFILE"):
    with open(os.environ["FAKE_PIDFILE"], "w") as f:
        f.write(str(os.getpid()))

TOOL = os.environ.get("FAKE_TOOL", "echo")
TOOLS = [{"name": TOOL, "description": "echo", "inputSchema": {"type": "object", "properties": {}}}]

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
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif m == "tools/call":
        send({"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "ok"}]}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}})
"""


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _server(tmp_path, *, name="fake", tool="echo", pidfile=None, external=None):
    srv = tmp_path / f"{name.replace(' ', '_')}.py"
    srv.write_text(FAKE, encoding="utf-8")
    env = {"FAKE_TOOL": tool}
    if pidfile:
        env["FAKE_PIDFILE"] = str(pidfile)
    return ServerSpec(
        name=name, command=sys.executable, args=[str(srv)], env=env, external=external
    )


def _cfg(tmp_path, *specs, external: bool | None = None):
    cfg = tmp_path / "mcp.json"
    data = {}
    for s in specs:
        item = {"command": s.command, "args": s.args, "env": s.env}
        if external is not None:
            item["external"] = external
        data[s.name] = item
    cfg.write_text(json.dumps({"mcpServers": data}), encoding="utf-8")
    return str(cfg)


# ---------------- ① 启动前必须授权 ----------------


def test_no_authorization_means_no_server_spawned(tmp_path):
    con, _dom, root = _setup(tmp_path)
    pidfile = tmp_path / "pid"
    cfg = _cfg(tmp_path, _server(tmp_path, pidfile=pidfile, external=False))
    r = Runner(con, root, ScriptedAdapter(script=[]), mcp=True, mcp_config_paths=[cfg])
    r.start("t1")
    assert r.mcp_bridge is None, "没有授权就不许启动 MCP 服务器"
    assert not pidfile.exists(), "服务器进程不该被启动(有副作用!)"
    kinds = {row["kind"] for row in con.execute("SELECT kind FROM events")}
    assert "mcp.skipped" in kinds
    assert "authz grant" in (r.mcp_skipped_reason or "")


def test_expired_and_revoked_authorization_do_not_start_mcp(tmp_path):
    con, _dom, root = _setup(tmp_path)
    pidfile = tmp_path / "pid"
    cfg = _cfg(tmp_path, _server(tmp_path, pidfile=pidfile, external=False))

    az = Authz(roots=[root], expires_at=1.0)  # 早已过期
    r = Runner(
        con,
        root,
        ScriptedAdapter(script=[]),
        mcp=True,
        mcp_config_paths=[cfg],
        policy=Policy(authorization=az, allow_high_risk=True),
    )
    r.start("t1")
    assert r.mcp_bridge is None and not pidfile.exists()

    az2 = Authz(roots=[root], revoked=True)
    r2 = Runner(
        con,
        root,
        ScriptedAdapter(script=[]),
        mcp=True,
        mcp_config_paths=[cfg],
        policy=Policy(authorization=az2, allow_high_risk=True),
    )
    r2.start("t1")
    assert r2.mcp_bridge is None and not pidfile.exists()


def test_allow_mcp_false_skips_and_policy_denies(tmp_path):
    con, dom, root = _setup(tmp_path)
    cfg = _cfg(tmp_path, _server(tmp_path, external=False))
    az = Authz(roots=[root], allow_mcp=False)
    r = Runner(
        con,
        root,
        ScriptedAdapter(script=[]),
        mcp=True,
        mcp_config_paths=[cfg],
        policy=Policy(authorization=az, allow_high_risk=True),
    )
    r.start("t1")
    assert r.mcp_bridge is None
    assert "allow_mcp" in (r.mcp_skipped_reason or "")

    from fwcore.tools import Registry, ToolSpec

    reg2 = Registry()
    reg2.register(
        ToolSpec(
            name="mcp__fake__echo",
            description="x",
            input_schema={"type": "object", "properties": {}},
            handler=lambda ctx, a: {"status": "ok"},
            risk="high",
            requires="mcp",
            external=False,
        )
    )
    ctx = ToolContext(
        domain=dom, root=root, policy=Policy(authorization=az, allow_high_risk=True)
    )
    out = reg2.call(ctx, "mcp__fake__echo", {})
    assert out["status"] == "cancelled" and "allow_mcp" in out["error"]


# ---------------- ② external 由配置声明,不靠名字猜 ----------------


def test_undeclared_server_is_external_and_needs_confirmation(tmp_path):
    _con, dom, root = _setup(tmp_path)
    az = Authz(roots=[root])  # allow_external_mcp=False(默认)
    reg_bridge = MCPBridge(
        root=root,
        registry=__import__("fwcore.tools", fromlist=["Registry"]).Registry(),
        specs=[_server(tmp_path, external=None)],
        call_timeout=5,
    )
    reg_bridge.start()
    try:
        name = tool_name("fake", "echo")
        spec = reg_bridge.registry.specs[name]
        assert spec.external is True, "未声明 external ⇒ 默认按 external 处理"
        ctx = ToolContext(
            domain=dom, root=root, policy=Policy(authorization=az, allow_high_risk=True)
        )
        out = reg_bridge.registry.call(ctx, name, {})
        assert out["status"] == "cancelled" and "确认" in out["error"]
    finally:
        reg_bridge.close()


def test_keyword_name_does_not_override_explicit_external_false(tmp_path):
    """名字像外部动作,但服务器显式声明 external=false:仍按配置走,并留审计提示。"""
    _con, dom, root = _setup(tmp_path)
    az = Authz(roots=[root])
    b = MCPBridge(
        root=root,
        registry=__import__("fwcore.tools", fromlist=["Registry"]).Registry(),
        specs=[_server(tmp_path, tool="submit_report", external=False)],
        call_timeout=5,
    )
    b.start()
    try:
        name = tool_name("fake", "submit_report")
        assert b.registry.specs[name].external is False
        assert b.status["fake"]["external_declared"] is True
        assert b.status["fake"]["external_hint"] == ["submit_report"]
        ctx = ToolContext(
            domain=dom, root=root, policy=Policy(authorization=az, allow_high_risk=True)
        )
        assert b.registry.call(ctx, name, {})["status"] == "ok", (
            "授权内的分析工具应自主执行"
        )
    finally:
        b.close()


def test_authorization_can_allow_external_mcp_without_percall_confirm(tmp_path):
    _con, dom, root = _setup(tmp_path)
    az = Authz(roots=[root], allow_external_mcp=True)
    b = MCPBridge(
        root=root,
        registry=__import__("fwcore.tools", fromlist=["Registry"]).Registry(),
        specs=[_server(tmp_path, external=None)],
        call_timeout=5,
    )
    b.start()
    try:
        ctx = ToolContext(
            domain=dom, root=root, policy=Policy(authorization=az, allow_high_risk=True)
        )
        out = b.registry.call(ctx, tool_name("fake", "echo"), {})
        assert out["status"] == "ok"
    finally:
        b.close()


# ---------------- ③ 名称碰撞:确定性消歧 / 明确拒绝 ----------------


def test_collision_between_servers_is_deterministic_not_silent():
    a = tool_name("a-b", "c")  # mcp__a_b__c
    b = tool_name("a_b", "c")  # mcp__a_b__c
    assert a == b, "构造前提:两个不同 server 规范化后同名"
    assert disambiguated_name("a-b", "c") != disambiguated_name("a_b", "c")
    # 同一输入永远得到同名(跨轮/重启稳定)
    assert disambiguated_name("a-b", "c") == disambiguated_name("a-b", "c")


def test_same_tool_reopened_is_idempotent(tmp_path):
    _con, _dom, root = _setup(tmp_path)
    from fwcore.tools import Registry

    reg = Registry()
    spec = _server(tmp_path, external=False)
    b = MCPBridge(root=root, registry=reg, specs=[spec], call_timeout=5)
    b.start()
    n1 = len(reg.specs)
    b.close()
    b.start()  # 重开(跨轮)不能因为重名而炸
    assert len(reg.specs) == n1
    b.close()


def test_hidden_exposure_does_not_register_tools(tmp_path):
    """Pi exposure=hidden must not silently become a direct fwcore tool."""
    _con, _dom, root = _setup(tmp_path)
    spec = _server(tmp_path, external=False)
    spec.exposure = "hidden"
    b = MCPBridge(
        root=root,
        registry=__import__("fwcore.tools", fromlist=["Registry"]).Registry(),
        specs=[spec],
        call_timeout=5,
    )
    b.start()
    try:
        assert b.registry.specs == {}
        assert b.status["fake"]["tools"] == []
        assert b.status["fake"]["hidden_tools"] == ["echo"]
    finally:
        b.close()


def test_hidden_tool_override_does_not_register(tmp_path):
    _con, _dom, root = _setup(tmp_path)
    spec = _server(tmp_path, external=False)
    spec.tool_exposure = {"echo": "hidden"}
    b = MCPBridge(
        root=root,
        registry=__import__("fwcore.tools", fromlist=["Registry"]).Registry(),
        specs=[spec],
        call_timeout=5,
    )
    b.start()
    try:
        assert b.registry.specs == {}
        assert b.status["fake"]["hidden_tools"] == ["echo"]
    finally:
        b.close()
