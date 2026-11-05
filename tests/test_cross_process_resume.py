"""跨进程恢复:新进程要能接着跑(授权/MCP/幂等),而不是"只在同一个对象里模拟重启"。

覆盖:
1. 进程 A 建研究 + 调 MCP 工具 → 暂停退出;
   进程 B 用同一 run_id 恢复 ⇒ MCP 仍被发现/可调用,已完成的调用不重复执行(幂等)。
2. 进程 A 在工具执行中被 SIGKILL ⇒ 独立看护收敛(owner_dead)⇒ 进程 B 恢复继续。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time

from fwcore import db as _db

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAKE = r'''
import json, os, sys

COUNTER = os.environ.get("FAKE_COUNTER")

def bump(tag):
    if not COUNTER:
        return
    with open(COUNTER, "a") as f:
        f.write(tag + "\n")

def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

TOOLS = [{"name": "echo", "description": "echo", "inputSchema": {"type": "object",
          "properties": {"text": {"type": "string"}}}}]

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
        bump("echo:" + str((p.get("arguments") or {}).get("text")))
        send({"jsonrpc": "2.0", "id": rid,
              "result": {"content": [{"type": "text", "text": "echo:ok"}]}})
    elif rid is not None:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "no method"}})
'''


def _cli(root, *a, timeout=180, env_extra=None):
    db = os.path.join(root, "state", "fwcore.sqlite3")
    env = dict(os.environ, PYTHONPATH=ROOT, FW_TARGET="t1")
    env.update(env_extra or {})
    return subprocess.run([sys.executable, "-m", "fwcore", "--root", root, "--db", db, *a],
                          cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout)


def _prep(tmp_path):
    root = str(tmp_path)
    os.makedirs(os.path.join(root, "state"), exist_ok=True)
    srv = os.path.join(root, "fake_mcp.py")
    with open(srv, "w", encoding="utf-8") as f:
        f.write(FAKE)
    counter = os.path.join(root, "calls.log")
    with open(os.path.join(root, "state", "mcp.json"), "w", encoding="utf-8") as f:
        json.dump({"mcpServers": {"fake": {"command": sys.executable, "args": [srv],
                                          "external": False,
                                          "env": {"FAKE_COUNTER": counter}}}}, f)
    assert _cli(root, "init", "t1", "--version", "1").returncode == 0
    assert _cli(root, "authz", "grant", "t1", "--roots", root, "--hours", "1").returncode == 0
    return root, counter


def _script(root, name, entries):
    p = os.path.join(root, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(entries, f)
    return p


def _calls(counter):
    if not os.path.isfile(counter):
        return []
    with open(counter, encoding="utf-8") as f:
        return [x for x in f.read().split("\n") if x]


def test_new_process_resumes_mcp_and_keeps_idempotency(tmp_path):
    root, counter = _prep(tmp_path)
    a = _script(root, "a.json", [
        {"content": "先问 MCP", "tool_calls": [{"id": "c1", "name": "mcp_fake_echo",
                                               "args": {"text": "a"}}]},
        {"content": "FINAL: 阶段一结束", "tool_calls": []},
    ])
    r1 = _cli(root, "run", "t1", "--model", "scripted", "--script", a, "--steps", "1",
              "--no-watchdog")
    assert r1.returncode == 0, r1.stderr[-600:]
    assert _calls(counter) == ["echo:a"], "进程 A 应只调用一次 MCP"

    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    rid = con.execute("SELECT id FROM runs").fetchone()["id"]
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "paused"

    b = _script(root, "b.json", [
        {"content": "重放同一个调用 id", "tool_calls": [{"id": "c1", "name": "mcp_fake_echo",
                                                        "args": {"text": "a"}}]},
        {"content": "再开一个新的", "tool_calls": [{"id": "c2", "name": "mcp_fake_echo",
                                                   "args": {"text": "b"}}]},
        {"content": "FINAL: 完成", "tool_calls": []},
    ])
    r2 = _cli(root, "resume", rid, "--model", "scripted", "--script", b, "--steps", "6",
              "--no-watchdog")
    assert r2.returncode == 0, r2.stderr[-600:]

    # 新进程里 MCP 必须**重新发现**:新调用成功 + provider 正确
    rows = con.execute("SELECT tool,status,provider,call_key FROM tool_calls WHERE tool='mcp_fake_echo'"
                       " ORDER BY created_at").fetchall()
    assert [r["status"] for r in rows] == ["ok", "ok"], [dict(r) for r in rows]
    assert all(r["provider"] == "mcp:fake" for r in rows)
    # 幂等:同一个 call_key 只有一行,且**没有**再次触发服务器端副作用
    assert len([r for r in rows if r["call_key"] == "c1"]) == 1
    assert _calls(counter) == ["echo:a", "echo:b"], _calls(counter)


def test_crash_watchdog_then_resume(tmp_path):
    root, _counter = _prep(tmp_path)
    a = _script(root, "a.json", [
        {"content": "跑个长工具", "tool_calls": [{"id": "t1", "name": "shell_exec",
                                              "args": {"command": "sleep 30", "timeout": 60}}]},
        {"content": "FINAL: never", "tool_calls": []},
    ])
    db = os.path.join(root, "state", "fwcore.sqlite3")
    env = dict(os.environ, PYTHONPATH=ROOT, FW_TARGET="t1")
    proc = subprocess.Popen([sys.executable, "-m", "fwcore", "--root", root, "--db", db,
                             "run", "t1", "--model", "scripted", "--script", a,
                             "--steps", "2", "--no-watchdog"],
                            cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, start_new_session=True)
    con = _db.connect(db)
    rid = None
    deadline = time.time() + 60
    while time.time() < deadline:
        row = con.execute("SELECT run_id FROM tool_calls WHERE tool='shell_exec' AND status='running'"
                          ).fetchone()
        if row:
            rid = row["run_id"]
            break
        time.sleep(0.1)
    assert rid, "长工具没进入 running(测试前置不成立)"

    os.kill(proc.pid, signal.SIGKILL)          # 模拟主进程异常终止(不 cleanup)
    proc.wait(timeout=30)

    wd = _cli(root, "watchdog", "--once", "--heartbeat-timeout", "1")
    assert wd.returncode == 0, wd.stderr[-500:]
    st = con.execute("SELECT state, handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
    assert st["state"] == "paused", "看护必须收敛死掉的 run"
    assert "watchdog_paused" in (st["handback_reason"] or ""), st["handback_reason"]
    # 工具进程组必须被清理(不能留下 sleep 30)
    row = con.execute("SELECT status FROM tool_calls WHERE run_id=? AND tool='shell_exec'",
                      (rid,)).fetchone()
    assert row["status"] in ("running", "cancelled", "error", "timeout")

    b = _script(root, "b.json", [{"content": "FINAL: 恢复后收工", "tool_calls": []}])
    r = _cli(root, "resume", rid, "--model", "scripted", "--script", b, "--steps", "2",
             "--no-watchdog")
    assert r.returncode == 0, r.stderr[-600:]
    final = con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"]
    assert final in ("finished", "paused")
