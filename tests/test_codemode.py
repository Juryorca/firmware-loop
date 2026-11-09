"""QuickJS Codemode: async tools, filtering, search, images, limits, persistence."""

from __future__ import annotations

import json
import os
import sys
import threading
import time

from fwcore import db
from fwcore.codemode import CodemodeExecutor
from fwcore.domain import Domain
from fwcore.tool_search import register_tool_search
from fwcore.tools import Registry, ToolContext, ToolSpec


def _workspace(tmp_path):
    root = str(tmp_path)
    con = db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    reg = Registry()
    ctx = ToolContext(domain=dom, root=root)
    return con, dom, reg, ctx


def test_codemode_parallel_calls_record_and_filter(tmp_path):
    con, _dom, reg, ctx = _workspace(tmp_path)
    # A barrier proves that the four calls are actually in flight together;
    # the previous wall-clock assertion was flaky under xdist load.
    barrier = threading.Barrier(4)

    def slow(tool_ctx, args):
        barrier.wait(timeout=2.0)
        time.sleep(0.02)
        return {
            "status": "ok",
            "value": args.get("v"),
            "text": f"value={args.get('v')}",
        }

    reg.register(
        ToolSpec(
            "slow_tool",
            "slow test tool",
            {"type": "object", "properties": {"v": {"type": "number"}}},
            slow,
            exposure="codemode",
        )
    )
    started = time.monotonic()
    out = CodemodeExecutor(reg, ctx).run(
        "const rs = await Promise.all([tools.slow_tool({v:1}), tools.slow_tool({v:2}), "
        "tools.slow_tool({v:3}), tools.slow_tool({v:4})]); "
        "return rs.map(x => x.value).join(',');"
    )
    elapsed = time.monotonic() - started
    assert out["status"] == "ok", out
    assert "1,2,3,4" in out["text"]
    assert elapsed < 2.0
    assert con.execute("SELECT COUNT(*) n FROM tool_calls").fetchone()["n"] == 4


def test_codemode_search_describe_and_error_catch(tmp_path):
    _con, _dom, reg, ctx = _workspace(tmp_path)
    reg.register(
        ToolSpec(
            "ida_decompile",
            "decompile a function with IDA",
            {"type": "object", "properties": {"addr": {"type": "string"}}},
            lambda c, a: {"status": "ok", "text": "decompiled"},
            exposure="codemode",
            namespace="ida",
        )
    )

    def fail(c, a):
        raise RuntimeError("ida backend unavailable")

    reg.register(
        ToolSpec(
            "ida_xrefs",
            "show cross references",
            {"type": "object", "properties": {}},
            fail,
            exposure="codemode",
            namespace="ida",
        )
    )
    out = CodemodeExecutor(reg, ctx).run(
        "const hits = searchTools('decompile', {limit: 2}); "
        "const d = describeTool('ida_decompile'); "
        "const ns = describeNamespace('ida'); "
        "let e = null; try { await tools.ida_xrefs({}); } catch (err) { e = String(err); } "
        "return {hits, d, ns, e};"
    )
    assert out["status"] == "ok", out
    assert "ida_decompile" in out["text"]
    assert "decompile a function" in out["text"]
    assert "ida backend unavailable" in out["text"]


def test_codemode_forwards_image_without_base64_text(tmp_path):
    _con, _dom, reg, ctx = _workspace(tmp_path)
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"not-a-real-png-but-a-file")
    reg.register(
        ToolSpec(
            "screenshot",
            "return a screenshot",
            {"type": "object", "properties": {}},
            lambda c, a: {
                "status": "ok",
                "images": [
                    {"path": str(image_path), "mimeType": "image/png", "bytes": 24}
                ],
            },
            exposure="codemode",
        )
    )
    out = CodemodeExecutor(reg, ctx).run(
        "const r = await tools.screenshot({}); image(r.images[0]); return 'done';"
    )
    assert out["status"] == "ok", out
    assert any(p.get("type") == "image" for p in out["parts"])
    assert "not-a-real-png" not in out["text"]


def test_codemode_timeout_and_output_limit(tmp_path):
    _con, _dom, reg, ctx = _workspace(tmp_path)
    started = time.monotonic()
    out = CodemodeExecutor(reg, ctx).run(
        '// @options: {"timeout_ms": 120}\nwhile (true) {}'
    )
    assert out["status"] == "timeout", out
    assert time.monotonic() - started < 2.0

    out = CodemodeExecutor(reg, ctx).run(
        '// @options: {"max_output_tokens": 5}\nreturn "x".repeat(5000);'
    )
    assert out["status"] == "ok", out
    assert out["truncated"] is True
    assert out.get("full_output_path")
    assert len(out["text"]) < 5000


def test_tool_search_activates_deferred_only_for_model(tmp_path):
    _con, _dom, reg, ctx = _workspace(tmp_path)
    reg.register(
        ToolSpec("direct_read", "read a file", {"type": "object"}, lambda c, a: {})
    )
    reg.register(
        ToolSpec(
            "deferred_decompile",
            "decompile function",
            {"type": "object"},
            lambda c, a: {},
            exposure="deferred",
        )
    )
    reg.register(
        ToolSpec(
            "code_only_xref",
            "cross references",
            {"type": "object"},
            lambda c, a: {},
            exposure="codemode",
        )
    )
    reg.register(
        ToolSpec(
            "hidden_tool",
            "hidden",
            {"type": "object"},
            lambda c, a: {},
            exposure="hidden",
        )
    )
    register_tool_search(reg)
    assert {t["name"] for t in reg.describe_model()} == {"direct_read", "tool_search"}
    out = reg.specs["tool_search"].handler(ctx, {"query": "decompile"})
    assert out["status"] == "ok" and "deferred_decompile" in out["loaded"]
    assert "deferred_decompile" in {t["name"] for t in reg.describe_model()}
    assert "code_only_xref" not in {t["name"] for t in reg.describe_model()}
    assert "hidden_tool" not in {t["name"] for t in reg.describe_model()}


def test_runner_codemode_executes_nested_registry_and_records(tmp_path):
    from fwcore.limits import Limits
    from fwcore.models import ScriptedAdapter
    from fwcore.runtime import Runner

    con, dom, _reg, _ctx = _workspace(tmp_path)
    dom.create_target("t1", version="1")
    (tmp_path / "x.txt").write_text("hello", encoding="utf-8")
    script = [
        {
            "content": "run codemode",
            "tool_calls": [
                {
                    "id": "co1",
                    "name": "codemode",
                    "args": {
                        "code": "const r = await tools.fs_hash({path: 'x.txt'}); return r.sha256;"
                    },
                }
            ],
        },
        {"content": "FINAL: ok", "tool_calls": []},
    ]
    r = Runner(
        con,
        str(tmp_path),
        ScriptedAdapter(script=script),
        limits=Limits(max_model_calls=4),
        mcp=False,
        watchdog=False,
    )
    res = r.run(r.start("t1"), "t1")
    assert res.state == "finished"
    rows = con.execute(
        "SELECT tool, status FROM tool_calls ORDER BY created_at"
    ).fetchall()
    names = [x["tool"] for x in rows]
    assert "codemode" in names and "fs_hash" in names, names
    assert all(x["status"] == "ok" for x in rows), [dict(x) for x in rows]


def test_fake_mcp_100_tools_codemode_search_filter_and_schema_savings(tmp_path):
    from fwcore.codemode import register_codemode
    from fwcore.mcp_client import ServerSpec
    from fwcore.mcp_runtime import MCPBridge
    from fwcore.tool_search import register_tool_search

    srv = tmp_path / "fake_ida.py"
    srv.write_text(
        r"""
import json, sys
TOOLS = [
    {"name": f"ida_{i}", "description": f"reverse engineering tool {i}",
     "inputSchema": {"type": "object", "properties": {"addr": {"type": "string"}}}}
    for i in range(100)
]
def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    m = json.loads(line); r = m.get("id"); method = m.get("method")
    if method == "initialize":
        send({"jsonrpc":"2.0","id":r,"result":{"protocolVersion":"2024-11-05",
              "serverInfo":{"name":"fake-ida","version":"1"},"capabilities":{}}})
    elif method == "tools/list":
        send({"jsonrpc":"2.0","id":r,"result":{"tools":TOOLS}})
    elif method == "tools/call":
        p = m.get("params") or {}
        send({"jsonrpc":"2.0","id":r,"result":{"content":[
            {"type":"text","text":"decompiled " + str((p.get("arguments") or {}).get("addr"))}]}})
    elif r is not None:
        send({"jsonrpc":"2.0","id":r,"error":{"code":-32601,"message":"no"}})
""",
        encoding="utf-8",
    )
    db_con, _dom, reg, ctx = _workspace(tmp_path)
    from fwcore.policy import DirectPolicy

    ctx.policy = DirectPolicy()
    register_tool_search(reg)
    register_codemode(reg)
    bridge = MCPBridge(
        root=str(tmp_path),
        registry=reg,
        specs=[
            ServerSpec(
                name="fake", command=sys.executable, args=[str(srv)], external=False
            )
        ],
    )
    bridge.start()
    try:
        model_schema = reg.describe_model()
        all_schema = reg.describe()
        assert len(model_schema) == 2, [t["name"] for t in model_schema]
        assert len(all_schema) >= 100
        model_chars = len(json.dumps(model_schema, ensure_ascii=False))
        all_chars = len(json.dumps(all_schema, ensure_ascii=False))
        assert model_chars < all_chars / 5

        code = (
            "const hits = searchTools('reverse engineering', {limit: 3});"
            "const vals = await Promise.all(hits.map(h => tools[h.name]({addr: '0x41'})));"
            "return vals.map(v => v.text).join('|');"
        )
        out = CodemodeExecutor(reg, ctx).run(code)
        assert out["status"] == "ok", out
        assert "decompiled 0x41" in out["text"]
        assert "ida_99" not in out["text"]
        assert (
            db_con.execute(
                "SELECT COUNT(*) n FROM tool_calls WHERE tool LIKE 'mcp__fake__%'"
            ).fetchone()["n"]
            == 3
        )
    finally:
        bridge.close()


def test_tool_search_activation_survives_checkpoint_and_resume(tmp_path):
    from fwcore.limits import Limits
    from fwcore.models import ScriptedAdapter
    from fwcore.runtime import Runner

    con, dom, reg, _ctx = _workspace(tmp_path)
    dom.create_target("t1", version="1")
    reg.register(
        ToolSpec(
            "deferred_ida",
            "decompile with IDA",
            {"type": "object"},
            lambda c, a: {"status": "ok"},
            exposure="deferred",
        )
    )
    register_tool_search(reg)
    r = Runner(
        con,
        str(tmp_path),
        ScriptedAdapter(script=[]),
        registry=reg,
        limits=Limits(max_model_calls=1),
        mcp=False,
        watchdog=False,
    )
    rid = r.start("t1")
    history = [
        {
            "role": "tool",
            "name": "tool_search",
            "content": json.dumps({"status": "ok", "loaded": ["deferred_ida"]}),
        }
    ]
    assert r._checkpoint(rid, 1, history)
    r2 = Runner(
        con,
        str(tmp_path),
        ScriptedAdapter(script=[]),
        registry=Registry(),
        limits=Limits(max_model_calls=1),
        mcp=False,
        watchdog=False,
    )
    loaded = r2._load_history(rid)
    # A fresh Registry must restore the checkpointed activation, not a process set.
    register_tool_search(r2.registry)
    r2.registry.register(
        ToolSpec(
            "deferred_ida",
            "decompile with IDA",
            {"type": "object"},
            lambda c, a: {"status": "ok"},
            exposure="deferred",
        )
    )
    r2._restore_tool_activation(loaded)
    assert "deferred_ida" in {t["name"] for t in r2.registry.describe_model()}
    r2.registry.specs["deferred_ida"].exposure = "hidden"
    r2._restore_tool_activation(loaded)
    assert "deferred_ida" not in {t["name"] for t in r2.registry.describe_model()}


def test_codemode_cancellation_stops_worker(tmp_path):
    con, dom, reg, ctx = _workspace(tmp_path)
    dom.create_target("t1", version="1")
    from fwcore.models import ScriptedAdapter
    from fwcore.runtime import Runner

    r = Runner(
        con,
        str(tmp_path),
        ScriptedAdapter(script=[]),
        registry=reg,
        mcp=False,
        watchdog=False,
    )
    rid = r.start("t1")
    con.execute("UPDATE runs SET handback_reason='user_stop: test' WHERE id=?", (rid,))
    ctx.run_id = rid
    started = time.monotonic()
    out = CodemodeExecutor(reg, ctx).run("while (true) {}")
    assert out["status"] == "cancelled", out
    assert time.monotonic() - started < 2.0
    r.close()
