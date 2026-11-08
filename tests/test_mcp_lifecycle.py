"""MCP 在**正式 CLI 路径**与**跨轮**生命周期里的行为(不是 Registry 单元测试)。

覆盖:
1. CLI `run` 真的接入 MCP:注册 → 模型选它 → 执行 → 记录 provider → 退出清理(无残留进程)
2. `--no-mcp` 对照:同一脚本调 MCP 工具会失败(证明开关真的有效)
3. 跨轮:`advance()` 第 2 轮的 MCP 调用仍然成功(此前 `_finish_state` 会关桥 ⇒ 第 2 轮全废)
4. 异常退出:MCP 关闭、看护停止、run 记为 paused
5. `close()` 幂等
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import ScriptedAdapter
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE = r'''
import json, os, sys

def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

if os.environ.get("FAKE_PIDFILE"):
    with open(os.environ["FAKE_PIDFILE"], "w") as f:
        f.write(str(os.getpid()))

TOOLS = [{"name": "echo", "description": "回声", "inputSchema": {"type": "object",
          "properties": {"text": {"type": "string"}}, "required": ["text"]}}]

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
        txt = (p.get("arguments") or {}).get("text")
        send({"jsonrpc": "2.0", "id": rid,
              "result": {"content": [{"type": "text", "text": "echo:" + str(txt)}]}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}})
'''


def _fake_server(tmp_path, name="fake"):
    srv = tmp_path / f"{name}.py"
    srv.write_text(FAKE, encoding="utf-8")
    return srv


def _write_mcp_cfg(root, srv, name="fake"):
    os.makedirs(os.path.join(root, "state"), exist_ok=True)
    with open(os.path.join(root, "state", "mcp.json"), "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {name: {"command": sys.executable, "args": [str(srv)],
                                         "enabled": True, "external": False}}}, f)


def _cli(root, db, *a, env_extra=None, timeout=180):
    env = dict(os.environ, PYTHONPATH=ROOT, FW_TARGET="t")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "fwcore", "--root", str(root), "--db", str(db), *a],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)


# ---------------- 1) 正式 CLI 路径 ----------------

def test_cli_run_uses_mcp_and_cleans_up(tmp_path):
    root = str(tmp_path)
    db = str(tmp_path / "c.sqlite3")
    pidfile = str(tmp_path / "fake.pid")
    _write_mcp_cfg(root, _fake_server(tmp_path))
    script = tmp_path / "s.json"
    script.write_text(json.dumps([
        {"content": "先问一下 MCP。", "tool_calls": [{"name": "mcp__fake__echo", "args": {"text": "cli"}}]},
        {"content": "FINAL: 完成", "tool_calls": []},
    ]), encoding="utf-8")

    assert _cli(root, db, "init", "t", "--version", "1").returncode == 0
    assert _cli(root, db, "authz", "grant", "t", "--roots", root, "--hours", "1").returncode == 0
    r = _cli(root, db, "run", "t", "--model", "scripted", "--script", str(script),
             "--steps", "4", "--no-watchdog", env_extra={"FAKE_PIDFILE": pidfile})
    assert r.returncode == 0, r.stderr[-800:]

    con = _db.connect(db)
    rows = con.execute("SELECT tool, status, provider FROM tool_calls").fetchall()
    assert rows, "CLI 路径里没有任何工具调用"
    mcp_rows = [x for x in rows if x["tool"] == "mcp__fake__echo"]
    assert mcp_rows and mcp_rows[0]["status"] == "ok", rows
    assert mcp_rows[0]["provider"] == "mcp:fake", mcp_rows[0]["provider"]

    # 退出后:子进程组已清理(不能留下 MCP 服务器进程)
    if os.path.isfile(pidfile):
        with open(pidfile, encoding="utf-8") as f:
            pid = int(f.read().strip())
        import time
        for _ in range(40):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f"MCP 服务器进程 {pid} 在 CLI 退出后仍存活")


def test_cli_no_mcp_control_fails_the_call(tmp_path):
    root = str(tmp_path)
    db = str(tmp_path / "c.sqlite3")
    _write_mcp_cfg(root, _fake_server(tmp_path))
    script = tmp_path / "s.json"
    script.write_text(json.dumps([
        {"content": "试试 MCP。", "tool_calls": [{"name": "mcp__fake__echo", "args": {"text": "x"}}]},
        {"content": "FINAL: 完", "tool_calls": []},
    ]), encoding="utf-8")
    assert _cli(root, db, "init", "t", "--version", "1").returncode == 0
    assert _cli(root, db, "authz", "grant", "t", "--roots", root, "--hours", "1").returncode == 0
    r = _cli(root, db, "run", "t", "--model", "scripted", "--script", str(script),
             "--steps", "4", "--no-watchdog", "--no-mcp")
    assert r.returncode == 0, r.stderr[-500:]
    con = _db.connect(db)
    row = con.execute("SELECT status, note FROM tool_calls WHERE tool='mcp__fake__echo'").fetchone()
    assert row is not None and row["status"] == "error", dict(row) if row else None


# ---------------- 2) 跨轮 ----------------

def _mk(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "state", "artifacts"), exist_ok=True)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def test_advance_keeps_mcp_alive_across_rounds(tmp_path):
    con, dom, root = _mk(tmp_path)
    _write_mcp_cfg(root, _fake_server(tmp_path))
    echo = {"tool_calls": [{"name": "mcp__fake__echo", "args": {"text": "r"}}]}
    script = [echo, {"content": "FINAL: 第1轮", "tool_calls": []},
              echo, {"content": "FINAL: 第2轮", "tool_calls": []}]
    pol = Policy(authorization=Authz(roots=[root]), allow_high_risk=True)
    r = Runner(con, root, ScriptedAdapter(script=script), limits=Limits(max_model_calls=6),
               policy=pol, mcp=True, mcp_config_paths=[os.path.join(root, "state", "mcp.json")])
    res = r.advance("t1", "目标", max_rounds=2)
    rows = con.execute("SELECT status, provider FROM tool_calls WHERE tool='mcp__fake__echo'").fetchall()
    assert len(rows) >= 2, f"跨轮没跑满: {rows}"
    assert all(x["status"] == "ok" for x in rows), rows
    assert all(x["provider"] == "mcp:fake" for x in rows), rows
    # 结束后必须真的关掉(不残留客户端/进程)
    assert res.state in ("finished", "paused")
    assert r.mcp_bridge is not None and r.mcp_bridge.closed is True
    assert r.mcp_bridge.clients == {}


def test_runner_reopens_mcp_if_closed_between_turns(tmp_path):
    con, dom, root = _mk(tmp_path)
    _write_mcp_cfg(root, _fake_server(tmp_path))
    echo = {"tool_calls": [{"name": "mcp__fake__echo", "args": {"text": "again"}}]}
    pol = Policy(authorization=Authz(roots=[root]), allow_high_risk=True)
    r = Runner(con, root, ScriptedAdapter(script=[echo, {"content": "FINAL: a", "tool_calls": []},
                                                  echo, {"content": "FINAL: b", "tool_calls": []}]),
               limits=Limits(max_model_calls=6), policy=pol, mcp=True,
               mcp_config_paths=[os.path.join(root, "state", "mcp.json")])
    rid = r.start("t1")
    r.turn(rid, "t1", None, [])
    r.close()                      # 人为关掉(模拟外部清理/上一轮结束)
    r.turn(rid, "t1", None, [])    # 下一轮必须自己把桥拉起来
    rows = con.execute("SELECT status FROM tool_calls WHERE tool='mcp__fake__echo'").fetchall()
    assert len(rows) == 2 and all(x["status"] == "ok" for x in rows), rows
    kinds = [x["kind"] for x in con.execute("SELECT kind FROM events")]
    assert "mcp.reopened" in kinds
    r.close()


# ---------------- 3) 异常退出与幂等 ----------------

class _Boom(ScriptedAdapter):
    def chat(self, messages, tools):
        raise RuntimeError("模型端点炸了")


def test_exception_path_closes_mcp_and_watchdog(tmp_path):
    con, dom, root = _mk(tmp_path)
    _write_mcp_cfg(root, _fake_server(tmp_path))
    r = Runner(con, root, _Boom(script=[]), limits=Limits(max_model_calls=2),
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
               mcp=True, watchdog=True, watchdog_poll_s=0.02,
               mcp_config_paths=[os.path.join(root, "state", "mcp.json")])
    rid = r.start("t1")
    try:
        r.run(rid, "t1")
    except RuntimeError:
        pass
    else:
        raise AssertionError("异常应当向外传播")
    assert r.mcp_bridge is not None and r.mcp_bridge.closed is True
    assert r.mcp_bridge.clients == {}
    assert r._wd_thread is None
    state = con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"]
    assert state == "paused"


def test_close_is_idempotent(tmp_path):
    con, dom, root = _mk(tmp_path)
    _write_mcp_cfg(root, _fake_server(tmp_path))
    r = Runner(con, root, ScriptedAdapter(script=[]), mcp=True,
               policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True),
               mcp_config_paths=[os.path.join(root, "state", "mcp.json")])
    r.start("t1")                       # 授权确定后才启动 MCP
    r.close()
    assert r.mcp_bridge is not None and r.mcp_bridge.closed is True
    r.close()                           # 幂等
    assert r.mcp_bridge.closed is True
