"""看护的优先级:硬保护(磁盘/检查点)不能被"长工具宽限"绕过;独立进程也能兜底。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import ScriptedAdapter
from fwcore.runtime import Runner
from fwcore.watchdog import Watchdog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _setup(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    return con, dom, root


def _run_with_inflight_tool(con, root, *, age_s=1.0):
    r = Runner(con, root, ScriptedAdapter(script=[]))
    rid = r.start("t1")
    hb = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 5000))
    con.execute("UPDATE runs SET heartbeat_at=? WHERE id=?", (hb, rid))
    con.execute("INSERT INTO tool_calls(id,run_id,tool,args,status,created_at) VALUES"
                "('TC1',?,'fuzz_cgi','{}','running',strftime('%Y-%m-%dT%H:%M:%S+00:00','now'))", (rid,))
    return rid


def _wd(con, root, **limits):
    return Watchdog(con=None, root=root, limits=Limits(**limits),
                    db_path=con.execute("PRAGMA database_list").fetchone()[2],
                    heartbeat_timeout_s=300, poll_s=0.01)


def test_inflight_tool_still_kept_alive_when_caps_ok(tmp_path):
    """对照:没有硬超限时长工具仍然保活(不能因为这次改动把宽限也弄坏)。"""
    con, _dom, root = _setup(tmp_path)
    rid = _run_with_inflight_tool(con, root)
    acts = _wd(con, root).scan_once()
    assert any(a.get("kept_alive_by") == "tool_inflight" for a in acts), acts
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "running"


def test_standalone_supervisor_pauses_dead_run(tmp_path):
    """主进程死了以后,独立看护进程仍然能收敛(看守与运行不同生共死)。"""
    con, _dom, root = _setup(tmp_path)
    r = Runner(con, root, ScriptedAdapter(script=[]))
    rid = r.start("t1")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    con.execute("UPDATE runs SET pid=?, pid_starttime=12345, state='running' WHERE id=?", (dead.pid, rid))

    db = con.execute("PRAGMA database_list").fetchone()[2]
    out = subprocess.run([sys.executable, "-m", "fwcore", "--root", root, "--db", db,
                          "watchdog", "--once"], cwd=ROOT,
                         env=dict(os.environ, PYTHONPATH=ROOT),
                         capture_output=True, text=True, timeout=120, check=False)
    assert out.returncode == 0, out.stderr[-500:]
    report = json.loads(out.stdout)
    assert report["actions"], "独立看护什么都没做"
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "paused"
