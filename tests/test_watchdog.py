"""外层看护(watchdog)的对抗测试:不依赖被保护的循环配合。"""

from __future__ import annotations

import os
import subprocess
import time

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.runtime import Runner
from fwcore.models import ScriptedAdapter
from fwcore.watchdog import Watchdog


def _mk(tmp_path):
    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    dom.add_surface("t1", "x")
    hid = dom.add_hypothesis("t1", "h")
    rid = Runner(con, root, ScriptedAdapter(script=[]), limits=Limits(max_model_calls=2)).start("t1")
    return con, dom, root, rid, hid


def test_watchdog_pauses_stalled_run_and_kills_tool_process_group(tmp_path):
    con, dom, root, rid, hid = _mk(tmp_path)
    # 模拟"工具进程还在跑、循环已经卡死"
    child = subprocess.Popen(["sleep", "120"], start_new_session=True)
    try:
        art = os.path.join(root, "state", "artifacts", rid)
        os.makedirs(art, exist_ok=True)
        with open(os.path.join(art, ".pgids"), "w", encoding="utf-8") as f:
            f.write(f"{os.getpgid(child.pid)}\n")
        con.execute("UPDATE runs SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))

        acts = Watchdog(con, root, Limits(), heartbeat_timeout_s=60).scan_once()
        assert acts and acts[0]["paused"] is True
        assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "paused"
        time.sleep(0.4)
        assert child.poll() is not None, "看护必须杀掉被保护 run 的工具进程组"
        # 研究事实不被触碰
        assert con.execute("SELECT state FROM hypotheses WHERE id=?", (hid,)).fetchone()["state"] == "open"
        assert con.execute("SELECT COUNT(*) n FROM conclusions").fetchone()["n"] == 0
        kinds = [r["kind"] for r in con.execute("SELECT kind FROM events")]
        assert "watchdog.paused" in kinds
    finally:
        if child.poll() is None:
            child.kill()


def test_watchdog_pauses_on_db_growth(tmp_path):
    con, dom, root, rid, hid = _mk(tmp_path)
    dom.con.execute("INSERT INTO tool_calls(id,run_id,tool,args,status,created_at)"
                    " VALUES('TC1',?,'x','{}','ok','2026-01-01')", (rid,))
    acts = Watchdog(con, root, Limits(max_db_bytes=1)).scan_once()
    assert any(a.get("reason") == "max_db_bytes" for a in acts)
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "paused"


def test_watchdog_leaves_healthy_run_alone(tmp_path):
    con, dom, root, rid, hid = _mk(tmp_path)
    con.execute("UPDATE runs SET heartbeat_at=? WHERE id=?",
                (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), rid))
    assert Watchdog(con, root, Limits(), heartbeat_timeout_s=600).scan_once() == []
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "running"


def test_watchdog_daemon_is_independent_and_stoppable(tmp_path):
    import threading

    con, dom, root, rid, hid = _mk(tmp_path)
    stop = threading.Event()
    wd = Watchdog(con, root, Limits(), poll_s=0.05)
    th = wd.start_daemon(stop)
    time.sleep(0.2)
    stop.set()
    th.join(timeout=2)
    assert not th.is_alive()
    # 健康 run 未被误伤
    assert con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"] == "running"
