"""MCP 图片/资源端到端（本地 Fake MCP + Fake model transport）。

不调用真实视觉 API；这里验证内部协议、模型适配层拿到的消息、资源读取、
以及 checkpoint 中不含 base64。
"""

from __future__ import annotations

import base64
import json
import os
import sys

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.mcp_client import ServerSpec
from fwcore.mcp_content import (MAX_IMAGE_BYTES, MAX_IMAGES_PER_RESULT,
                                 content_parts)
from fwcore.mcp_runtime import MCPBridge
from fwcore.model_client import to_api_messages
from fwcore.models import OpenAICompatAdapter, OpenAIResponsesAdapter, ScriptedAdapter
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner
from fwcore.tools import Registry, ToolContext

PNG = base64.b64encode(b"fake-png-for-fake-mcp").decode("ascii")

FAKE = r'''
import base64, json, sys

PNG = "''' + PNG + r'''"

TOOLS = [
    {"name": "mix", "description": "text+image", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "multi", "description": "two images", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "long", "description": "long text+image", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "errimg", "description": "error+image", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "badimg", "description": "invalid images", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "link", "description": "resource link", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "embedded", "description": "embedded resources", "inputSchema": {"type": "object", "properties": {}}},
]

RESOURCES = [
    {"uri": "res://one", "name": "one", "mimeType": "text/plain"},
]
TEMPLATES = [{"uriTemplate": "res://items/{id}", "name": "item", "mimeType": "text/plain"}]

def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    m, rid = msg.get("method"), msg.get("id")
    p = msg.get("params") or {}
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "serverInfo": {"name": "fake", "version": "1"},
            "capabilities": {"resources": {"listChanged": True}, "tools": {}}}})
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"tools": TOOLS}})
    elif m == "tools/call":
        name = p.get("name")
        if name == "mix":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "before"},
                            {"type": "image", "data": PNG, "mimeType": "image/png"},
                            {"type": "text", "text": "after"}],
                "structuredContent": {"answer": 1}}})
        elif name == "multi":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "image", "data": PNG, "mimeType": "image/png"},
                            {"type": "text", "text": "middle"},
                            {"type": "image", "data": PNG, "mimeType": "image/png"}]}})
        elif name == "long":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "image", "data": PNG, "mimeType": "image/png"},
                            {"type": "text", "text": "L" * 30000}]}})
        elif name == "errimg":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "isError": True,
                "content": [{"type": "text", "text": "ERR"},
                            {"type": "image", "data": PNG, "mimeType": "image/png"}]}})
        elif name == "badimg":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "image", "data": "!!!not-base64!!!", "mimeType": "image/png"},
                            {"type": "image", "data": PNG, "mimeType": "image/bmp"}]}})
        elif name == "link":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "resource_link", "uri": "res://one", "name": "one",
                              "mimeType": "text/plain", "size": 3}]}})
        elif name == "embedded":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "content": [
                    {"type": "resource", "resource": {"uri": "res://a",
                     "mimeType": "text/plain", "text": "EMB"}},
                    {"type": "resource", "resource": {"uri": "res://b",
                     "mimeType": "image/png", "blob": PNG}},
                    {"type": "resource", "resource": {"uri": "res://c",
                     "mimeType": "application/octet-stream",
                     "blob": base64.b64encode(b"BIN").decode()}}]}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no such tool"}})
    elif m == "resources/list":
        cur = p.get("cursor")
        if cur == "p2":
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "resources": [{"uri": "res://two", "name": "two"}]}})
        elif cur:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "bad cursor"}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "resources": RESOURCES, "nextCursor": "p2"}})
    elif m == "resources/templates/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"resourceTemplates": TEMPLATES}})
    elif m == "resources/read":
        uri = p.get("uri")
        if uri == "res://one":
            contents = [{"uri": uri, "mimeType": "text/plain", "text": "one resource"}]
            send({"jsonrpc": "2.0", "id": rid, "result": {"contents": contents}})
        elif uri == "res://text":
            contents = [{"uri": uri, "mimeType": "text/plain", "text": "hello resource"}]
            send({"jsonrpc": "2.0", "id": rid, "result": {"contents": contents}})
        elif uri == "res://image":
            contents = [{"uri": uri, "mimeType": "image/png", "blob": PNG}]
            send({"jsonrpc": "2.0", "id": rid, "result": {"contents": contents}})
        elif uri == "res://binary":
            contents = [{"uri": uri, "mimeType": "application/octet-stream",
                         "blob": base64.b64encode(b"\x00\x01BIN").decode()}]
            send({"jsonrpc": "2.0", "id": rid, "result": {"contents": contents}})
        elif uri == "res://audio":
            contents = [{"uri": uri, "mimeType": "audio/wav",
                         "blob": base64.b64encode(b"RIFF").decode()}]
            send({"jsonrpc": "2.0", "id": rid, "result": {"contents": contents}})
        elif uri == "res://multi":
            contents = [
                {"uri": "res://a", "mimeType": "text/plain", "text": "A"},
                {"uri": "res://b", "mimeType": "image/png", "blob": PNG},
                {"uri": "res://c", "mimeType": "application/octet-stream",
                 "blob": base64.b64encode(b"B").decode()}]
            send({"jsonrpc": "2.0", "id": rid, "result": {"contents": contents}})
        elif uri == "res://empty":
            send({"jsonrpc": "2.0", "id": rid, "result": {"contents": []}})
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "bad uri"}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}})
'''


def _start(tmp_path):
    script = tmp_path / "fake_mcp.py"
    script.write_text(FAKE, encoding="utf-8")
    cfg = tmp_path / "mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"fake": {
        "command": sys.executable, "args": [str(script)], "external": False}}}),
        encoding="utf-8")
    spec = ServerSpec(name="fake", command=sys.executable, args=[str(script)], external=False)
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    reg = Registry()
    bridge = MCPBridge(root=root, registry=reg, specs=[spec])
    bridge.start()
    ctx = ToolContext(domain=dom, root=root,
                      policy=Policy(authorization=Authz(roots=[root], allow_high_risk=True)))
    return con, dom, root, cfg, reg, bridge, ctx


def _paths(out):
    return [p.get("path") for p in out.get("parts", []) if p.get("type") == "image"]


def _cleanup(out):
    for path in _paths(out):
        if path and os.path.exists(path):
            os.unlink(path)


def test_mcp_mixed_text_and_image_are_ordered(tmp_path):
    con, dom, root, cfg, reg, bridge, ctx = _start(tmp_path)
    out: dict = {}
    try:
        out = reg.call(ctx, "mcp__fake__mix", {})
        assert out["status"] == "ok"
        assert [p["type"] for p in out["parts"]] == ["text", "image", "text"]
        assert out["parts"][1]["mimeType"] == "image/png"
        assert out["images"] and os.path.isfile(out["images"][0]["path"])
        assert out["text"] == "before\nafter"
        assert PNG not in json.dumps(out, ensure_ascii=False)
    finally:
        _cleanup(out)
        bridge.close()


def test_mcp_multiple_images_and_long_text_keep_images(tmp_path):
    con, dom, root, cfg, reg, bridge, ctx = _start(tmp_path)
    long: dict = {}
    try:
        multi = reg.call(ctx, "mcp__fake__multi", {})
        assert len(_paths(multi)) == 2
        _cleanup(multi)
        long = reg.call(ctx, "mcp__fake__long", {})
        assert long["status"] == "ok" and long["truncated"] is True
        assert len(_paths(long)) == 1
        assert os.path.isfile(long["full_output_path"])
        assert "chars truncated" in long["text"]
    finally:
        _cleanup(long)
        bridge.close()


def test_mcp_error_with_image_and_invalid_images_are_honest(tmp_path):
    con, dom, root, cfg, reg, bridge, ctx = _start(tmp_path)
    err: dict = {}
    try:
        err = reg.call(ctx, "mcp__fake__errimg", {})
        assert err["status"] == "error" and len(_paths(err)) == 1
        assert err["text"] if "text" in err else True
        bad = reg.call(ctx, "mcp__fake__badimg", {})
        assert bad["status"] == "ok" and not _paths(bad)
        assert "invalid base64" in bad["text"] and "unsupported MIME" in bad["text"]
    finally:
        _cleanup(err)
        bridge.close()


def test_resource_tools_list_pages_read_types_and_link(tmp_path):
    con, dom, root, cfg, reg, bridge, ctx = _start(tmp_path)
    try:
        names = {s["name"] for s in reg.describe()}
        assert {"list_mcp_resources", "list_mcp_resource_templates", "read_mcp_resource"} <= names
        one = reg.call(ctx, "list_mcp_resources", {"server": "fake"})
        assert one["status"] == "ok" and one["structured"]["nextCursor"] == "p2"
        assert one["structured"]["resources"][0]["server"] == "fake"
        two = reg.call(ctx, "list_mcp_resources", {"server": "fake", "cursor": "p2"})
        assert two["structured"]["resources"][0]["uri"] == "res://two"
        # 不带 server = 每台服务器的每一页都聚合；不应再有 nextCursor。
        all_pages = reg.call(ctx, "list_mcp_resources", {})
        assert all_pages["status"] == "ok" and "nextCursor" not in all_pages["structured"]
        assert {r["uri"] for r in all_pages["structured"]["resources"]} == {"res://one", "res://two"}
        tpl = reg.call(ctx, "list_mcp_resource_templates", {"server": "fake"})
        assert tpl["structured"]["resourceTemplates"][0]["uriTemplate"].startswith("res://items/")
        text = reg.call(ctx, "read_mcp_resource", {"server": "fake", "uri": "res://text"})
        assert "hello resource" in text["text"]
        img = reg.call(ctx, "read_mcp_resource", {"server": "fake", "uri": "res://image"})
        assert _paths(img) and os.path.isfile(_paths(img)[0])
        binary = reg.call(ctx, "read_mcp_resource", {"server": "fake", "uri": "res://binary"})
        assert "saved to" in binary["text"]
        multi = reg.call(ctx, "read_mcp_resource", {"server": "fake", "uri": "res://multi"})
        types = [p["type"] for p in multi["parts"]]
        assert types.count("image") == 1 and "res://a:" in multi["text"]
        assert "res://b:" in multi["text"] and "saved to" in multi["text"]
        _cleanup(multi)
        audio = reg.call(ctx, "read_mcp_resource", {"server": "fake", "uri": "res://audio"})
        assert "audio" in audio["text"] and "no audio input" in audio["text"]
        link = reg.call(ctx, "mcp__fake__link", {})
        assert "read_mcp_resource" in link["text"] and "res://one" in link["text"]
        linked = reg.call(ctx, "read_mcp_resource", {"server": "fake", "uri": "res://one"})
        assert linked["status"] == "ok" and "one resource" in linked["text"]
        embedded = reg.call(ctx, "mcp__fake__embedded", {})
        assert [p["type"] for p in embedded["parts"]] == ["text", "image", "text"]
        assert "saved to" in embedded["text"]
        _cleanup(embedded)
        bad = reg.call(ctx, "read_mcp_resource", {"server": "nope", "uri": "res://x"})
        assert bad["status"] == "error"
        _cleanup(img)
    finally:
        bridge.close()


def test_model_adapter_receives_images_without_fake_user_message(tmp_path):
    path = tmp_path / "x.png"
    path.write_bytes(b"fake-png")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "mcp__fake__mix", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"status":"ok"}',
         "parts": [{"type": "text", "text": "before"},
                   {"type": "image", "mimeType": "image/png", "path": str(path),
                    "bytes": 8, "sha256": "x"}]},
    ]
    api = to_api_messages(messages, supports_vision=True, image_mode="tool_parts")
    assert all(m["role"] != "user" for m in api)
    tool = next(m for m in api if m["role"] == "tool")
    assert isinstance(tool["content"], list)
    assert tool["content"][0]["type"] == "text"
    assert tool["content"][1]["type"] == "image_url"
    assert tool["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    plain = to_api_messages(messages, supports_vision=False)
    tool_plain = next(m for m in plain if m["role"] == "tool")
    assert isinstance(tool_plain["content"], str)
    assert "text-only model" in tool_plain["content"]
    assert "image_url" not in tool_plain["content"]
    missing = dict(messages[1])
    missing["parts"] = [{**messages[1]["parts"][1], "path": str(tmp_path / "missing.png")}]
    api_missing = to_api_messages([messages[0], missing], supports_vision=True,
                                  image_mode="tool_parts")
    assert any("[image unavailable:" in str(p) for p in
               next(m for m in api_missing if m["role"] == "tool")["content"])


def test_checkpoint_persists_image_reference_without_base64(tmp_path):
    con, dom, root, cfg, reg, bridge, ctx = _start(tmp_path)
    bridge.close()
    call = {"id": "c1", "type": "function",
            "function": {"name": "mcp__fake__mix", "arguments": "{}"}}
    r = Runner(con, root, ScriptedAdapter(script=[
        {"content": "", "tool_calls": [call]},
        {"content": "FINAL: ok"}]),
        policy=Policy(authorization=Authz(roots=[root], allow_high_risk=True)),
        mcp=True, mcp_config_paths=[cfg])
    res = r.run(r.start("t1"), "t1")
    assert res.state == "finished"
    row = con.execute("SELECT messages FROM checkpoints ORDER BY step DESC LIMIT 1").fetchone()
    msgs = json.loads(row["messages"])
    tools = [m for m in msgs if m.get("role") == "tool"]
    assert tools and tools[0].get("parts")
    assert any(p.get("type") == "image" for p in tools[0]["parts"])
    assert PNG not in json.dumps(msgs, ensure_ascii=False)
    restored = to_api_messages(msgs, supports_vision=True, image_mode="tool_parts")
    tool_restored = next(m for m in restored if m["role"] == "tool")
    assert any(p.get("type") == "image_url" for p in tool_restored["content"])
    _cleanup({"parts": tools[0]["parts"]})


def test_image_content_safety_limits():
    import base64
    invalid = content_parts([{"type": "image", "data": "%%%", "mimeType": "image/png"}],
                            server="s")
    assert invalid[0]["type"] == "text" and "invalid base64" in invalid[0]["text"]
    huge = base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode("ascii")
    too_big = content_parts([{"type": "image", "data": huge, "mimeType": "image/png"}],
                            server="s")
    assert too_big[0]["type"] == "text" and "exceeds" in too_big[0]["text"]
    one = base64.b64encode(b"x").decode("ascii")
    many = content_parts([{"type": "image", "data": one, "mimeType": "image/png"}]
                         * (MAX_IMAGES_PER_RESULT + 1), server="s")
    for p in many:
        if p.get("type") == "image" and p.get("path"):
            os.unlink(p["path"])
    assert any(p["type"] == "text" and "more than" in p["text"] for p in many)


def test_openai_adapter_payload_sends_image_in_tool_parts_without_user(tmp_path):
    path = tmp_path / "x.png"
    path.write_bytes(b"fake-png")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "mcp__fake__mix", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"status":"ok"}',
         "parts": [{"type": "text", "text": "see"}, {"type": "image", "mimeType": "image/png",
                    "path": str(path), "bytes": 8, "sha256": "x"}]},
    ]
    class Capture:
        def __init__(self):
            self.payload = None
        def __call__(self, _url, _headers, payload):
            self.payload = payload
            return {"choices": [{"message": {"content": "FINAL: ok"}, "finish_reason": "stop"}]}
    cap = Capture()
    adapter = OpenAICompatAdapter(model="fake", api_key="k", transport=cap,
                                  supports_vision=True, image_mode="tool_parts")
    reply = adapter.chat(messages, [])
    assert reply.content == "FINAL: ok"
    assert cap.payload is not None
    api_msgs = cap.payload["messages"]
    assert all(m["role"] != "user" for m in api_msgs)
    tool = next(m for m in api_msgs if m["role"] == "tool")
    assert any(part.get("type") == "image_url" for part in tool["content"])


def test_openai_responses_adapter_uses_legal_tool_image_output(tmp_path):
    path = tmp_path / "x.png"
    path.write_bytes(b"fake-png")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_1", "type": "function",
             "function": {"name": "mcp__fake__mix", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": '{"status":"ok"}',
         "parts": [{"type": "text", "text": "see"}, {"type": "image", "mimeType": "image/png",
                    "path": str(path), "bytes": 8, "sha256": "x"}]},
    ]
    class Capture:
        def __init__(self):
            self.payload = None
        def __call__(self, _url, _headers, payload):
            self.payload = payload
            return {"status": "completed", "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "done"}]}]}
    cap = Capture()
    adapter = OpenAIResponsesAdapter(model="fake", api_key="k", transport=cap,
                                     supports_vision=True)
    reply = adapter.chat(messages, [])
    assert reply.content == "done" and cap.payload is not None
    items = cap.payload["input"]
    assert all(item.get("role") != "user" for item in items)
    output = next(item for item in items if item.get("type") == "function_call_output")
    assert isinstance(output["output"], list)
    assert any(part.get("type") == "input_image" for part in output["output"])


def test_bridge_parts_reach_model_adapter_end_to_end(tmp_path):
    con, dom, root, cfg, reg, bridge, ctx = _start(tmp_path)
    out = {}
    try:
        out = reg.call(ctx, "mcp__fake__mix", {})
        assert out["status"] == "ok"
        history = [
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function",
                 "function": {"name": "mcp__fake__mix", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": '{"status":"ok"}',
             "parts": out["parts"]},
        ]
        api = to_api_messages(history, supports_vision=True, image_mode="tool_parts")
        assert all(m["role"] != "user" for m in api)
        tool = next(m for m in api if m["role"] == "tool")
        assert isinstance(tool["content"], list)
        assert any(p.get("type") == "image_url" for p in tool["content"])
        # 图片没被文本截断/JSON 序列化丢掉。
        assert any(p.get("type") == "text" and p.get("text") == "after" for p in tool["content"])
    finally:
        _cleanup(out)
        bridge.close()


RES_ONLY = r"""
import json, sys

def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

for line in sys.stdin:
    msg = json.loads(line)
    m, rid = msg.get("method"), msg.get("id")
    if m == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"resources": {}},
            "serverInfo": {"name": "resonly", "version": "1"}}})
    elif m == "tools/list":
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no tools"}})
    elif m == "resources/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "resources": [{"uri": "res://only", "name": "only", "mimeType": "text/plain"}]}})
    elif m == "resources/templates/list":
        send({"jsonrpc": "2.0", "id": rid, "result": {"resourceTemplates": []}})
    elif m == "resources/read":
        send({"jsonrpc": "2.0", "id": rid, "result": {
            "contents": [{"uri": "res://only", "mimeType": "text/plain", "text": "only text"}]}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}})
"""


def test_resource_only_server_registers_resources_without_tools(tmp_path):
    script = tmp_path / "resonly.py"
    script.write_text(RES_ONLY, encoding="utf-8")
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    reg = Registry()
    bridge = MCPBridge(root=root, registry=reg,
                       specs=[ServerSpec(name="resonly", command=sys.executable,
                                         args=[str(script)], external=False)])
    try:
        status = bridge.start()
        assert status["resonly"]["status"] == "ready"
        assert status["resonly"]["resources"] is True
        ctx = ToolContext(domain=dom, root=root,
                          policy=Policy(authorization=Authz(roots=[root], allow_high_risk=True)))
        names = {s["name"] for s in reg.describe()}
        assert "read_mcp_resource" in names
        out = reg.call(ctx, "read_mcp_resource", {"server": "resonly", "uri": "res://only"})
        assert out["status"] == "ok" and "only text" in out["text"]
    finally:
        bridge.close()
