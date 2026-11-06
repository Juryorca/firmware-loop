"""长工具与取消语义:看护不能误杀正常长工具;取消要说是"取消";重试不重复副作用。"""

from __future__ import annotations

import os
import threading
import time

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.policy import Authz, Policy
from fwcore.runtime import Runner
from fwcore.tools import ToolContext, default_registry
from fwcore.watchdog import Watchdog


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _ctx(dom, root, rid=None):
    return ToolContext(domain=dom, root=root, run_id=rid,
                       policy=Policy(authorization=Authz(roots=[root]), allow_high_risk=True))


class _Stub:
    """只为建 run 用的适配器(本文件不驱动模型)。"""

    name = "stub"

    def chat(self, messages, tools):
        from fwcore.models import Reply

        return Reply(content="FINAL: stub", tool_calls=[])


def _mk_run(con, dom, root):
    r = Runner(con, root, model=_Stub(), limits=Limits(max_tool_seconds=600))
    rid = r.start("t1")
    return r, rid


def test_watchdog_keeps_long_tool_alive(tmp_path):
    con, dom, root = _setup(tmp_path)
    _r, rid = _mk_run(con, dom, root)
    # 心跳已经很旧(模拟"模型在等一个 10 分钟的工具")
    con.execute("UPDATE runs SET heartbeat_at='2026-01-01T00:00:00+00:00', state='running' WHERE id=?", (rid,))
    con.execute("INSERT INTO tool_calls(id,run_id,tool,args,status,created_at) VALUES"
                "('TC1',?,'fuzz_cgi','{}','running',strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))", (rid,))

    wd = Watchdog(con=None, root=root, limits=Limits(max_tool_seconds=600),
                  db_path=os.path.join(root, "state", "fwcore.sqlite3"),
                  heartbeat_timeout_s=30, poll_s=0.01)
    actions = wd.scan_once()
    assert any(a.get("kept_alive_by") == "tool_inflight" for a in actions), actions
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "running"


def test_watchdog_still_pauses_when_tool_exceeds_grace(tmp_path):
    con, dom, root = _setup(tmp_path)
    _r, rid = _mk_run(con, dom, root)
    con.execute("UPDATE runs SET heartbeat_at='2026-01-01T00:00:00+00:00', state='running' WHERE id=?", (rid,))
    con.execute("INSERT INTO tool_calls(id,run_id,tool,args,status,created_at) VALUES"
                "('TC1',?,'fuzz_cgi','{}','running','2026-01-01T00:00:00+00:00')", (rid,))

    wd = Watchdog(con=None, root=root, limits=Limits(max_tool_seconds=60),
                  db_path=os.path.join(root, "state", "fwcore.sqlite3"),
                  heartbeat_timeout_s=30, poll_s=0.01)
    wd.scan_once()
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "paused"


def test_cancel_marks_inflight_tool_cancelled(tmp_path):
    con, dom, root = _setup(tmp_path)
    r, rid = _mk_run(con, dom, root)
    out_box: dict = {}

    def _run_tool():
        out_box["out"] = default_registry().call(_ctx(dom, root, rid), "shell_exec",
                                                {"command": "sleep 30", "timeout": 60})

    t = threading.Thread(target=_run_tool, daemon=True)
    t.start()
    row = None
    for _ in range(300):                      # 全量套件下机器负载高:给到 15 s 再判"没进 running"
        row = con.execute("SELECT id FROM tool_calls WHERE run_id=? AND status='running'",
                          (rid,)).fetchone()
        if row:
            break
        time.sleep(0.05)
    assert row, "工具没进入 running 状态"
    r.cancel(rid, "用户取消")
    t.join(timeout=15)

    st = con.execute("SELECT status FROM tool_calls WHERE id=?", (row["id"],)).fetchone()["status"]
    assert st == "cancelled", st
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "paused"


def test_same_call_key_does_not_repeat_side_effect(tmp_path):
    con, dom, root = _setup(tmp_path)
    path = os.path.join(root, "state", "artifacts", "side.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ctx = _ctx(dom, root)
    cmd = f"sh -c 'echo x >> {path}'"      # shell_exec 用 shlex 切分,不用 shell ⇒ 自己调 sh
    a = default_registry().call(ctx, "shell_exec", {"command": cmd, "timeout": 10}, call_key="k1")
    b = default_registry().call(ctx, "shell_exec", {"command": cmd, "timeout": 10}, call_key="k1")
    assert a["status"] == "ok"
    assert b.get("replayed") is True, b
    with open(path, encoding="utf-8") as f:
        assert f.read().count("x") == 1, "同一个 call_key 不能重复产生副作用"
