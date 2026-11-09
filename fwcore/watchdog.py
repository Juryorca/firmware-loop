"""外层执行看护(watchdog):**独立于被保护的运行循环**。

为什么必须有这一层(2026-11-04 62GB 事故的教训):
被保护的循环如果自己写错(例如"空闲检测"漏了、模型永不 FINAL),它就没有机会执行自己的保护。
因此还需要一个**外部**观察者:只看可观测事实(心跳、库体积、检查点数量、墙钟、进程是否还在),
超阈值就强行把 run 置为 paused 并**杀掉该 run 的工具进程组**,不指望循环配合。

设计要点
- 只读事实 + 一处写(`runs.state='paused'`)+ 杀进程组;不碰任何研究事实(假设/证据/结论)。
- 阈值全部来自 `Limits`(硬保护恒在);可单次(`watchdog_once`)或常驻(`start_daemon`)。
- 进程组来源:`tool_calls` 里记录过的 pgid(见 tools.shell_exec),或 run 的 run_dir 下 `.pgids`。
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import signal
import sqlite3
import threading
import time
from dataclasses import dataclass, field

from . import db as _db
from .limits import MB, Limits

log = logging.getLogger("fwcore.watchdog")


@dataclass
class Watchdog:
    con: sqlite3.Connection | None
    root: str
    limits: Limits = field(default_factory=Limits)
    heartbeat_timeout_s: float = 900.0     # 心跳多久没更新算"卡死"
    poll_s: float = 5.0
    # 守护线程里**不能**复用主线程的 sqlite Connection(same-thread check)⇒ 传 db_path 让它自己开连接
    db_path: str | None = None
    max_consecutive_errors: int = 5
    errors: int = 0
    scans: int = 0
    paused_count: int = 0
    stopped_reason: str | None = None

    @contextlib.contextmanager
    def _conn(self):
        """每次扫描用**自己的**连接(可在任意线程使用),用完即关。"""
        if self.db_path:
            con = _db.connect(self.db_path, timeout=2.0)
            try:
                yield con
            finally:
                con.close()
        else:
            if self.con is None:
                raise sqlite3.ProgrammingError("Watchdog 需要 con 或 db_path 之一")
            yield self.con

    def _db_bytes(self, con: sqlite3.Connection | None = None) -> int:
        con = con or self.con
        if con is None:
            return 0
        with contextlib.suppress(sqlite3.Error):
            p = con.execute("PRAGMA database_list").fetchone()[2]
            return sum(os.path.getsize(p + s) for s in ("", "-wal", "-shm") if os.path.exists(p + s))
        return 0

    def _pgids_entries(self, run_id: str) -> list[tuple[int, int | None]]:
        """读该 run 的 .pgids 快照(扫描时先取,避免把接管后新 owner 的进程组算进来)。"""
        pgfile = os.path.join(self.root, "state", "artifacts", run_id, ".pgids")
        entries: list[tuple[int, int | None]] = []
        if os.path.isfile(pgfile):
            try:
                with open(pgfile, encoding="utf-8") as f:
                    for line in f:
                        parts = line.split()
                        if not parts or not parts[0].isdigit():
                            continue
                        start = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
                        entries.append((int(parts[0]), start))
            except OSError:
                return []
        return entries

    def _kill_pgids(self, run_id: str, entries: list[tuple[int, int | None]] | None = None) -> list[int]:
        """杀掉该 run 记录过的工具进程组(独立于循环);entries 给出时只杀这份快照。"""
        killed: list[int] = []
        if entries is None:
            entries = self._pgids_entries(run_id)
        for pg, start in entries:
            if start is None:
                # 没有 starttime 的记录无法证明 pgid 没被复用 ⇒ 宁可不杀(防误伤);
                # 当前 writer 总是写 starttime,这只影响旧格式残留
                continue
            cur = _proc_starttime(pg)
            if cur is None or cur != start:
                continue              # pid 已被复用(或进程已死)⇒ 不能杀
            try:
                os.killpg(pg, signal.SIGKILL)
                killed.append(pg)
            except (ProcessLookupError, PermissionError):
                continue
        return killed

    def health(self) -> dict:
        """看护自身的可观测状态:外部据此判断"看护是否还活着/是否失效"。"""
        return {
            "scans": self.scans,
            "errors": self.errors,
            "stopped_reason": self.stopped_reason,
            "paused_by_me": self.paused_count,
            "alive": self.stopped_reason is None,
        }

    def scan_once(self) -> list[dict]:
        """检查一轮:返回触发的动作列表(空 = 一切正常)。"""
        actions: list[dict] = []
        now = time.time()
        self.scans += 1
        with self._conn() as con:
            return self._scan(con, now, actions)

    def _scan(self, con: sqlite3.Connection, now: float, actions: list[dict]) -> list[dict]:
        # ① 磁盘:库体积超过硬顶 ⇒ 所有 running 的 run 暂停
        size = self._db_bytes(con)
        if size > self.limits.max_db_bytes:
            actions.append({"reason": "max_db_bytes", "db_bytes": size,
                            "limit": self.limits.max_db_bytes})

        rows = con.execute(
            "SELECT r.id, r.heartbeat_at, r.updated_at, r.state, r.pid, r.pid_starttime, r.host"
            " FROM runs r WHERE r.state='running'"
        ).fetchall()
        for r in rows:
            hb = _epoch(r["heartbeat_at"] or r["updated_at"])
            stale = (now - hb) > self.heartbeat_timeout_s if hb else True
            n_ck = con.execute(
                "SELECT COUNT(*) n FROM checkpoints WHERE run_id=?", (r["id"],)).fetchone()["n"]
            too_many = n_ck > max(self.limits.checkpoints_keep * 2, 10)
            over_db = size > self.limits.max_db_bytes
            # 主运行进程已死(或 pid 被复用)⇒ 这个 run 不可能再推进:
            # 看护作为**独立进程**必须能发现它(否则看护与主进程同生共死 = 没有保护)
            owner_dead = False
            if (r["host"] or "") == _host() and r["pid"]:
                if not _pid_alive(r["pid"]):
                    owner_dead = True
                elif r["pid_starttime"]:
                    cur = _proc_starttime(r["pid"])
                    owner_dead = cur is not None and cur != r["pid_starttime"]

            # 硬保护优先:磁盘/检查点超限与"心跳停滞"是两类事 ——
            # 长工具只能豁免"停滞",**不得**豁免磁盘与检查点硬顶(否则 fuzz 期间库爆盘无人管)
            hard = [name for name, hit in (("max_db_bytes", over_db),
                                           ("checkpoints", too_many),
                                           ("owner_dead", owner_dead)) if hit]
            if hard:
                why = "owner_dead" if "owner_dead" in hard else ",".join(hard)
                # 扫描时快照 .pgids:接管后的新 owner 会往里追加自己的进程组,
                # 按"现在的文件"杀会误杀新 owner 的工具
                snap = self._pgids_entries(r["id"])
                killed: list[int] = []
                with _db.Tx(con) as c:
                    # CAS 必须带扫描时看到的 owner(pid+starttime+host):
                    # 只看 state='running' 会把"接管后的新 owner"误暂停/误杀(扫描→更新之间可发生)
                    cur = c.execute("UPDATE runs SET state='paused', handback_reason=?, updated_at=?"
                                    " WHERE id=? AND state='running' AND IFNULL(pid,-1)=?"
                                    "   AND IFNULL(pid_starttime,-1)=? AND IFNULL(host,'')=?",
                                    (f"watchdog_paused: {why}", _now(), r["id"],
                                     -1 if r["pid"] is None else r["pid"],
                                     -1 if r["pid_starttime"] is None else r["pid_starttime"],
                                     r["host"] or ""))
                    if not cur.rowcount:
                        continue
                    killed = self._kill_pgids(r["id"], snap)
                    c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                              (_now(), "watchdog.paused", r["id"],
                               json.dumps({"hard": hard, "checkpoints": n_ck, "db_bytes": size,
                                           "stale_s": round(now - hb, 1) if hb else None,
                                           "killed_pgids": killed}, ensure_ascii=False)))
                actions.append({"run": r["id"], "paused": True, "hard": hard,
                                "killed_pgids": killed, "checkpoints": n_ck, "db_bytes": size})
                continue

            if stale and not owner_dead:
                # 长工具在飞 ≠ 研究停滞:只豁免"停滞",不豁免上面的硬保护
                # (主进程已死的 run 不适用宽限:没人等它了)
                inflight = con.execute(
                    "SELECT tool, created_at FROM tool_calls WHERE run_id=? AND status='running'"
                    " ORDER BY created_at DESC LIMIT 1", (r["id"],)).fetchone()
                if inflight:
                    started = _epoch(inflight["created_at"])
                    grace = max(float(self.limits.max_tool_seconds), self.heartbeat_timeout_s) + 120.0
                    if started and (now - started) < grace:
                        with _db.Tx(con) as c:
                            c.execute("UPDATE runs SET heartbeat_at=? WHERE id=?", (_now(), r["id"]))
                            c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                                      (_now(), "watchdog.tool_inflight", r["id"],
                                       json.dumps({"tool": inflight["tool"],
                                                   "running_s": round(now - started, 1),
                                                   "grace_s": grace}, ensure_ascii=False)))
                        actions.append({"run": r["id"], "kept_alive_by": "tool_inflight",
                                        "tool": inflight["tool"]})
                        continue
                # 与 hard 分支同一条规则:**先赢下状态竞争**再杀组/写事件;
                # 否则扫描→更新之间 run 可能已 finished/被 resume 接管,会误杀新 owner 的进程组
                snap = self._pgids_entries(r["id"])
                killed: list[int] = []
                with _db.Tx(con) as c:
                    cur = c.execute("UPDATE runs SET state='paused', handback_reason=?, updated_at=?"
                                    " WHERE id=? AND state='running' AND IFNULL(pid,-1)=?"
                                    "   AND IFNULL(pid_starttime,-1)=? AND IFNULL(host,'')=?",
                                    (f"watchdog_paused: stale {round(now - hb, 1) if hb else 'unknown'}s",
                                     _now(), r["id"],
                                     -1 if r["pid"] is None else r["pid"],
                                     -1 if r["pid_starttime"] is None else r["pid_starttime"],
                                     r["host"] or ""))
                    if not cur.rowcount:
                        continue
                    killed = self._kill_pgids(r["id"], snap)
                    c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                              (_now(), "watchdog.paused", r["id"],
                               json.dumps({"stale_s": round(now - hb, 1) if hb else None,
                                           "checkpoints": n_ck, "killed_pgids": killed,
                                           "db_bytes": size}, ensure_ascii=False)))
                actions.append({"run": r["id"], "paused": True, "killed_pgids": killed,
                                "stale_s": round(now - hb, 1) if hb else None, "checkpoints": n_ck})
        return actions

    def start_daemon(self, stop: threading.Event | None = None) -> threading.Thread:
        stop = stop or threading.Event()

        def _loop():
            backoff = self.poll_s
            while not stop.is_set():
                try:
                    self.scan_once()
                    self.errors = 0
                    backoff = self.poll_s
                except Exception:                      # 看护自己不能死,但必须留痕 + 退避
                    self.errors += 1
                    log.exception("watchdog scan failed (%d/%d)", self.errors, self.max_consecutive_errors)
                    if self.errors >= self.max_consecutive_errors:
                        self.stopped_reason = f"连续 {self.errors} 次扫描失败,守护线程停止"
                        log.error(self.stopped_reason)
                        return
                    backoff = min(self.poll_s * (2 ** self.errors), 60.0)
                stop.wait(backoff)

        th = threading.Thread(target=_loop, name="fwcore-watchdog", daemon=True)
        th.start()
        return th


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (PermissionError, TypeError, ValueError):
        return True
    return True


def _host() -> str:
    import socket

    return socket.gethostname()


def _proc_starttime(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            tail = f.read().rsplit(")", 1)[-1].split()
        return int(tail[19])
    except (OSError, IndexError, ValueError):
        return None


def _epoch(ts: str | None) -> float:
    import datetime as _d

    if not ts:
        return 0.0
    try:
        return _d.datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0


def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.UTC).isoformat(timespec="seconds")


__all__ = ["MB", "Watchdog"]
