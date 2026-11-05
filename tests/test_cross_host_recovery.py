"""跨主机/跨重启的失联判定:只收敛真的断了的 run,不误伤活跃与长工具。"""

from __future__ import annotations

import os
import subprocess
import sys
import time

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.models import ScriptedAdapter
from fwcore.runtime import Runner, _host, _proc_starttime


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _runner(con, root):
    return Runner(con, root, ScriptedAdapter(script=[]))


def _mk_run(con, root, *, host=None, pid=None, starttime=None, age_s=0.0):
    r = _runner(con, root)
    rid = r.start("t1")
    hb = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - age_s))
    con.execute("UPDATE runs SET host=?, pid=?, pid_starttime=?, heartbeat_at=? WHERE id=?",
                (host or _host(), pid if pid is not None else os.getpid(),
                 starttime if starttime is not None else _proc_starttime(os.getpid()), hb, rid))
    return rid


def _state(con, rid):
    return con.execute("SELECT state, handback_reason FROM runs WHERE id=?", (rid,)).fetchone()


def test_live_process_is_never_recovered(tmp_path):
    con, dom, root = _setup(tmp_path)
    rid = _mk_run(con, root, age_s=10000)
    assert _runner(con, root).recover_stale() == []
    assert _state(con, rid)["state"] == "running"


def test_dead_pid_is_recovered_immediately(tmp_path):
    con, dom, root = _setup(tmp_path)
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    rid = _mk_run(con, root, pid=dead.pid, starttime=12345, age_s=1)
    assert _runner(con, root).recover_stale() == [rid]
    row = _state(con, rid)
    assert row["state"] == "paused" and row["handback_reason"].startswith("recovered_stale")


def test_pid_reuse_is_detected_via_starttime(tmp_path):
    """pid 还活着但启动时间对不上 ⇒ 是别人的进程,不能当成本 run 活着。"""
    con, dom, root = _setup(tmp_path)
    rid = _mk_run(con, root, age_s=5000, starttime=(_proc_starttime(os.getpid()) or 1) + 999)
    assert _runner(con, root).recover_stale() == [rid]


def test_remote_host_needs_explicit_optin(tmp_path):
    con, dom, root = _setup(tmp_path)
    rid = _mk_run(con, root, host="other-host", pid=None, starttime=None, age_s=4000)
    r = _runner(con, root)
    assert r.recover_stale() == [], "跨主机默认不动"
    assert r.recover_stale(include_remote=True, remote_factor=6.0) == [rid]


def test_remote_host_fresh_heartbeat_is_kept(tmp_path):
    con, dom, root = _setup(tmp_path)
    rid = _mk_run(con, root, host="other-host", pid=None, starttime=None, age_s=5)
    assert _runner(con, root).recover_stale(include_remote=True) == []
    assert _state(con, rid)["state"] == "running"


def test_inflight_long_tool_prevents_cross_host_recovery(tmp_path):
    con, dom, root = _setup(tmp_path)
    rid = _mk_run(con, root, host="other-host", pid=None, starttime=None, age_s=5000)
    con.execute("INSERT INTO tool_calls(id,run_id,tool,args,status,created_at) VALUES"
                "('TC1',?,'fuzz_cgi','{}','running',strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))", (rid,))
    assert _runner(con, root).recover_stale(include_remote=True) == []
