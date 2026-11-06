"""外层看护(watchdog)的对抗测试:不依赖被保护的循环配合。"""

from __future__ import annotations

import os
import subprocess
import threading
import time

from fwcore import db as _db
from fwcore.domain import Domain
from fwcore.limits import Limits
from fwcore.models import ScriptedAdapter
from fwcore.policy import Policy
from fwcore.runtime import Runner
from fwcore.watchdog import Watchdog, _proc_starttime


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
            f.write(f"{os.getpgid(child.pid)} {_proc_starttime(child.pid) or 0}\n")
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


# ---------- 守护线程的真实缺陷(2026-11-04 复核) ----------

def test_daemon_uses_own_connection_from_thread(tmp_path):
    """sqlite Connection 不能跨线程使用 ⇒ 守护线程必须用**自己的**连接(db_path)。"""
    con, dom, root, rid, hid = _mk(tmp_path)
    # 把心跳做旧,让看护该动手
    con.execute("UPDATE runs SET heartbeat_at='2000-01-01T00:00:00+00:00', pid=999999 WHERE id=?", (rid,))
    db_path = os.path.join(root, "state", "fwcore.sqlite3")
    wd = Watchdog(con=None, root=root, limits=Limits(), db_path=db_path,
                  heartbeat_timeout_s=1, poll_s=0.05)
    stop = threading.Event()
    th = wd.start_daemon(stop)
    ok = False
    for _ in range(40):
        state = con.execute("SELECT state FROM runs WHERE id=?", (rid,)).fetchone()["state"]
        if state == "paused":
            ok = True
            break
        time.sleep(0.05)
    stop.set()
    th.join(timeout=2)
    assert ok, "守护线程应能把失联 run 暂停(且不能因跨线程使用 Connection 报错)"
    assert wd.errors == 0


def test_daemon_stops_after_consecutive_errors(tmp_path):
    """看护自身持续失败时不能空转:连续 N 次失败后自行停止并记录原因。"""
    con, dom, root, rid, hid = _mk(tmp_path)
    bogus = os.path.join(root, "state")          # 一个**目录**:sqlite 打不开 ⇒ 每次扫描都抛错
    wd = Watchdog(con=None, root=root, limits=Limits(), db_path=bogus,
                  poll_s=0.01, max_consecutive_errors=3)
    stop = threading.Event()
    th = wd.start_daemon(stop)
    th.join(timeout=5)
    assert not th.is_alive() and wd.stopped_reason is not None
    stop.set()


# ---------- 自动启动(用户点名"是否真正自动启动") ----------

def test_runner_autostarts_watchdog_and_stops_it(tmp_path):
    """Runner(watchdog=True) 必须在运行期间自动起看护,并在结束时停掉线程。"""
    from fwcore.models import ScriptedAdapter
    from fwcore.runtime import Runner

    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    r = Runner(con, root, ScriptedAdapter(script=[{"content": "FINAL: 收工", "tool_calls": []}]),
               limits=Limits(max_model_calls=2), policy=Policy(allow_high_risk=True),
               watchdog=True, watchdog_poll_s=0.02)
    r.run(r.start("t1"), "t1")
    assert r.watchdog is not None, "看护未自动启动"
    assert r.watchdog.scans >= 1, "看护线程没有真正跑过扫描"
    assert r._wd_thread is None, "运行结束后看护线程必须停掉(不能泄漏)"


def test_runner_without_watchdog_does_not_start_one(tmp_path):
    from fwcore.models import ScriptedAdapter
    from fwcore.runtime import Runner

    root = str(tmp_path)
    con = _db.connect(os.path.join(root, "state", "fwcore.sqlite3"))
    dom = Domain(con, root)
    dom.create_target("t1", version="1")
    r = Runner(con, root, ScriptedAdapter(script=[{"content": "FINAL: 收工", "tool_calls": []}]),
               limits=Limits(max_model_calls=2), policy=Policy(allow_high_risk=True))
    r.run(r.start("t1"), "t1")
    assert r.watchdog is None and r._wd_thread is None


# ---------- PGID 误杀防护 + 看护可观测 ----------

def test_kill_pgids_refuses_reused_pid(tmp_path):
    """pgid 已被复用(starttime 不匹配)⇒ 绝不能杀;匹配才杀。"""
    from fwcore.tools import _proc_starttime

    con, dom, root, rid, hid = _mk(tmp_path)
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        pg = os.getpgid(child.pid)
        real = _proc_starttime(pg)
        art = os.path.join(root, "state", "artifacts", rid)
        os.makedirs(art, exist_ok=True)
        wd = Watchdog(con=con, root=root, limits=Limits())

        with open(os.path.join(art, ".pgids"), "w", encoding="utf-8") as f:
            f.write(f"{pg} {(real or 0) + 1}\n")      # 伪造"pid 复用"
        assert wd._kill_pgids(rid) == []
        time.sleep(0.2)
        assert child.poll() is None, "starttime 不匹配时不许杀"

        with open(os.path.join(art, ".pgids"), "w", encoding="utf-8") as f:
            f.write(f"{pg} {real}\n")                # 匹配 ⇒ 应当杀
        assert wd._kill_pgids(rid) == [pg]
        time.sleep(0.3)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()


def test_runner_progress_exposes_watchdog_health(tmp_path):
    from fwcore.models import ScriptedAdapter
    from fwcore.runtime import Runner

    con, dom, root, rid, hid = _mk(tmp_path)
    r = Runner(con, root, ScriptedAdapter(script=[{"content": "FINAL: 收工", "tool_calls": []}]),
               limits=Limits(max_model_calls=2), policy=Policy(allow_high_risk=True),
               watchdog=True, watchdog_poll_s=0.02)
    res = r.run(rid, "t1")
    prog = r.progress(rid)
    assert prog["watchdog"] is not None
    assert prog["watchdog"]["alive"] is True and prog["watchdog"]["scans"] >= 1
    assert res.state in ("finished", "paused")


def test_watchdog_does_not_clobber_finished_run(tmp_path):
    """扫描到更新之间 run 已 finished ⇒ 看护不能把它改成 paused(条件更新保护)。"""
    con, dom, root, rid, hid = _mk(tmp_path)
    con.execute("UPDATE runs SET state='finished', pid=999999, pid_starttime=1,"
                " heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
    acts = Watchdog(con, root, Limits(), heartbeat_timeout_s=60).scan_once()
    row = con.execute("SELECT state,handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "finished"
    assert not (row["handback_reason"] or "").startswith("watchdog_paused")
    assert not [a for a in acts if a.get("run") == rid], acts


def test_watchdog_stale_branch_does_not_clobber_finished_run(tmp_path):
    """owner 还活着但心跳过期(长工具/挂起)时,stale 分支也不能把 finished 改成 paused。"""
    con, dom, root, rid, hid = _mk(tmp_path)
    con.execute("UPDATE runs SET state='finished', pid=?, pid_starttime=?, "
                " heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?",
                (os.getpid(), _proc_starttime(os.getpid()), rid))
    acts = Watchdog(con, root, Limits(), heartbeat_timeout_s=60).scan_once()
    row = con.execute("SELECT state,handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
    assert row["state"] == "finished"
    assert not (row["handback_reason"] or "").startswith("watchdog_paused")
    assert not [a for a in acts if a.get("run") == rid], acts


def test_watchdog_does_not_kill_pgids_of_finished_run(tmp_path):
    """扫描与更新之间 run 已 finished ⇒ 不能提前 kill 它的进程组(会误杀新 owner 的工具)。"""
    from fwcore.watchdog import _proc_starttime as _pst

    con, dom, root, rid, hid = _mk(tmp_path)
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        art = os.path.join(root, "state", "artifacts", rid)
        os.makedirs(art, exist_ok=True)
        with open(os.path.join(art, ".pgids"), "w", encoding="utf-8") as f:
            f.write(f"{os.getpgid(child.pid)} {_pst(child.pid) or 0}\n")
        con.execute("UPDATE runs SET state='finished', pid=999999, pid_starttime=1,"
                    " heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
        Watchdog(con, root, Limits(), heartbeat_timeout_s=60).scan_once()
        time.sleep(0.3)
        assert child.poll() is None, "run 已 finished,看护不应杀掉它的进程组"
    finally:
        child.kill()


def test_recover_stale_does_not_kill_pgids_cross_host(tmp_path):
    """跨主机 recover:pgid 是主机本地概念,不能按别的 host 写的 .pgids 杀本机进程。"""
    con, dom, root, rid, hid = _mk(tmp_path)
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        art = os.path.join(root, "state", "artifacts", rid)
        os.makedirs(art, exist_ok=True)
        with open(os.path.join(art, ".pgids"), "w", encoding="utf-8") as f:
            f.write(f"{os.getpgid(child.pid)}\n")
        con.execute("UPDATE runs SET state='running', host='other-host', pid=NULL,"
                    " heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
        fixed = Runner(con, root, ScriptedAdapter(script=[])).recover_stale(
            include_remote=True, older_than_s=1)
        assert fixed == [rid]
        time.sleep(0.3)
        assert child.poll() is None, "跨主机的 run 不能杀本机 pgid"
    finally:
        child.kill()


def test_kill_pgids_skips_entries_without_starttime(tmp_path):
    """旧格式 .pgids(只有 pgid)无法证明未被复用 ⇒ 看护不得 kill(防误伤无关进程)。"""
    con, dom, root, rid, hid = _mk(tmp_path)
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        art = os.path.join(root, "state", "artifacts", rid)
        os.makedirs(art, exist_ok=True)
        with open(os.path.join(art, ".pgids"), "w", encoding="utf-8") as f:
            f.write(f"{os.getpgid(child.pid)}\n")       # 无 starttime
        con.execute("UPDATE runs SET heartbeat_at='2000-01-01T00:00:00+00:00' WHERE id=?", (rid,))
        Watchdog(con, root, Limits(), heartbeat_timeout_s=60).scan_once()
        time.sleep(0.3)
        assert child.poll() is None, "无 starttime 的 pgid 不允许杀"
    finally:
        child.kill()


def test_recover_stale_does_not_kill_without_recorded_pid(tmp_path):
    """同主机但 run 没有 pid 记录(旧库):没有 owner 证据 ⇒ 只暂停,不杀 pgid。"""
    con, dom, root, rid, hid = _mk(tmp_path)
    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        art = os.path.join(root, "state", "artifacts", rid)
        os.makedirs(art, exist_ok=True)
        with open(os.path.join(art, ".pgids"), "w", encoding="utf-8") as f:
            f.write(f"{os.getpgid(child.pid)}\n")
        con.execute("UPDATE runs SET pid=NULL, heartbeat_at='2000-01-01T00:00:00+00:00'"
                    " WHERE id=?", (rid,))
        fixed = Runner(con, root, ScriptedAdapter(script=[])).recover_stale(older_than_s=1)
        assert fixed == [rid]
        time.sleep(0.3)
        assert child.poll() is None, "没有 pid 证据时不能杀"
    finally:
        child.kill()
