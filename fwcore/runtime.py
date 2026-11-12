"""运行时:模型↔工具循环 + 心跳/失联判定 + 幂等工具调用 + 自动继续。

语义分层(这轮修正的重点)
- **研究轮次(round)**:一条假设被推到判定性结果为一轮;`runs.rounds` 与 `round.ended` 事件记录。
- **执行检查点(checkpoint)**:只记录 run 的 step 指针;完整消息历史在 append-only `conversation_entries`。
- **Run 暂停(paused)**:预算耗尽 / 进程中断 / 显式暂停 —— **不是**研究结论。
- **交回控制权(handback)**:只有三种合法原因 —— 收尾前置全清(closed)、需要工作区外的东西(needs_user_input)、用户要求停(user_stop)。

自动继续:模型输出 `FINAL` 只表示"本轮我想说的说完了";若程序算出的收尾阻塞仍非空,
`advance()` 会带着阻塞清单开下一轮(有上限),而不是直接结束。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from . import db as _db
from .compact import (
    SUMMARY_HEADER,
    SummarizationIncomplete,
    compact_history,
    estimate_tokens,
    should_compact,
)
from .compact import find_cut as _find_cut
from .domain import ConversationPersistError, Domain, new_id
from .limits import Limits
from .models import Adapter, ModelHTTPError, Reply, ScriptedAdapter, assistant_message  # noqa: F401
from .output import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_LINES,
    limit_structured_for_model,
    truncate_tail,
)
from .policy import DirectPolicy, Policy
from .research import Research
from .streaming import StreamCancelled, StreamControl, StreamError, as_event
from .toolkit import register_repo_tools
from .tools import Registry, ToolContext, default_registry

# FINAL 可能出现在多段文字里的任意**行首**(模型常写"本轮已收口…\nFINAL: …")
_FINAL_RE = __import__("re").compile(r"(?m)^\s*FINAL:")

# 兼容旧名(测试与脚本可能仍在用)
ScriptedModel = ScriptedAdapter

HANDOVER = ("closed", "needs_user_input", "user_stop")
STALE_DEFAULT_S = 300.0

# Fixed SQL only: callers choose an operation key, never a SQL fragment.
_RUNNING_UPDATES = {
    "limit": "UPDATE runs SET state='paused', updated_at=?, budget_note=?"
             " WHERE id=? AND state='running'"
             " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
    "reserve": "UPDATE runs SET model_calls=MAX(model_calls,?), updated_at=?"
               " WHERE id=? AND state='running'"
               " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
    "pause": "UPDATE runs SET state='paused', handback_reason=?, updated_at=?"
             " WHERE id=? AND state='running'"
             " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
    "round": "UPDATE runs SET rounds=?, updated_at=?"
             " WHERE id=? AND state='running'"
             " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
    "stall": "UPDATE runs SET state='paused', updated_at=?"
             " WHERE id=? AND state='running'"
             " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
}
_OWNER_COUNTERS = (
    "UPDATE runs SET model_calls=MAX(model_calls,?), tool_calls=MAX(tool_calls,?),"
    " elapsed_s=MAX(elapsed_s,?)"
    " WHERE id=? AND state IN ('running','paused')"
    " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?"
)


@dataclass
class RunResult:
    run_id: str
    state: str
    steps: int
    rounds: int
    final: str
    ready: bool
    blockers: list[str]
    handback: str | None
    history: list[dict]
    finding: bool = False

    # 兼容 dict 访问(旧测试/CLI 用 res["state"] 等)
    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "state": self.state, "steps": self.steps, "rounds": self.rounds,
                "final": self.final, "ready": self.ready, "blockers": self.blockers,
                "handback": self.handback, "history": self.history,
                "finding": self.finding}

    def __getitem__(self, k: str):
        return self.as_dict()[k]

    def get(self, k: str, default=None):
        return self.as_dict().get(k, default)

    def items(self):
        return self.as_dict().items()

    def keys(self):
        return self.as_dict().keys()


class Runner:
    def __init__(self, con, root: str, model: Adapter, *, registry: Registry | None = None,
                 max_steps: int | None = None, timeout: float = 60.0, policy: Policy | None = None,
                 research: Research | None = None, max_seconds: float | None = None,
                 stall_repeats: int = 3, limits: Limits | None = None,
                 prepare: Callable[[dict], dict] | None = None, prepare_timeout: float = 15.0,
                 watchdog: bool = False, watchdog_poll_s: float = 5.0,
                 watchdog_heartbeat_timeout: float = 900.0,
                 mcp: bool = False, mcp_config_paths: list[str] | None = None,
                 mcp_timeout: float = 20.0, mcp_call_timeout: float = 120.0,
                 summarizer: Callable[[list[dict]], str] | None = None,
                 on_stream: Callable[[dict], None] | None = None,
                 stream: bool = True,
                 stop_event=None,
                 on_session_target: Callable[[str], None] | None = None,
                 on_steering: Callable[[str, str], dict | None] | None = None,
                 on_steering_ack: Callable[[str], None] | None = None):
        self.con = con
        self.root = root
        self.domain = Domain(con, root)
        self.model = model
        self.registry = registry or default_registry()
        register_repo_tools(self.registry, root)
        # 步数不是"研究预算":默认不设上限,由模型自己输出 FINAL 或交回控制权;
        # 只保留**执行安全限制**:重复无进展检测 + 可选墙钟上限。
        self.max_steps = max_steps
        self.max_seconds = max_seconds
        self.stall_repeats = stall_repeats
        # 每轮最多模型调用次数:执行层保护(上下文/成本),触发时**暂停**而不是收口研究
        self.limits = limits or Limits()
        # 轮前准备:每轮模型调用前可做"准备"(压缩上下文/取外部状态),
        # **必须可取消且有超时**;钩子返回 {"context_note": str} 或 {"cancel": True}
        self.prepare = prepare
        self.prepare_timeout = prepare_timeout
        self.prepare_timeouts = 0
        self.prepare_abandoned = 0
        self._watchdog_enabled = watchdog
        self._watchdog_poll_s = watchdog_poll_s
        self._watchdog_heartbeat = watchdog_heartbeat_timeout
        self._wd_thread = None
        self._wd_stop = None
        self.watchdog = None
        # MCP:真接入工具注册与执行链路(不可达服务器如实记录,不注册)
        self._mcp_enabled = mcp
        self._mcp_config_paths = mcp_config_paths
        self._mcp_timeout = mcp_timeout
        self._mcp_call_timeout = mcp_call_timeout
        # 上下文压缩的摘要器(测试注入 fake;默认用模型,计入预算)
        self.summarizer = summarizer
        # 流式:事件回调(worker 线程调用)+ 当前请求的取消令牌
        self.on_stream = on_stream
        self.stream_enabled = stream
        # 会话级取消(不等 DB 写锁):turn 循环在安全点检查
        self.stop_event = stop_event
        self.on_session_target = on_session_target
        # A session-level queue may hand one user message in at a safe model
        # boundary.  The callback persists the queued entry (queued -> ok) and
        # returns its text; the core only inserts it into the current history.
        self.on_steering = on_steering
        self.on_steering_ack = on_steering_ack
        self.stream_control: StreamControl | None = None
        self._last_usage: dict = {}
        self._compact_cooldown_until = -1
        self._last_compact: dict | None = None
        # Pi context-awareness self-compaction: a model-called compact_now is
        # deferred until the current tool batch is persisted.
        self._compact_pending: dict | None = None
        self._compact_auto_resumes = 0
        self._compact_percent_after: float | None = None
        self.mcp_bridge = None
        self.mcp_skipped_reason: str | None = None
        # 注意:**构造时不启动 MCP**。MCP 服务器启动本身就有副作用(子进程/外网/登录态),
        # 而授权是 `start()` 按目标加载的 ⇒ 必须等授权确定后再决定"能不能启动"。
        self._mcp_config_paths = mcp_config_paths
        self._prepare_state: dict = {}
        self._pending_pause: tuple[str, dict] | None = None
        self.timeout = timeout
        self.policy = policy or Policy()
        self.research = research or Research(root, self.domain)

    # ---------------- 运行生命周期 ----------------
    def _load_authorization(self, target: str) -> None:
        """按目标加载研究授权(幂等)。**每个入口**都要做:新进程里没有构造时的那份 policy。

        `start()` 之前只有构造参数;`resume()`/`advance(run_id=...)` 是**新进程**接着跑,
        如果不在这里加载,新进程会以为"没有授权"⇒ 不启动 MCP/拒绝高风险工具 = 恢复不完整。
        """
        if self.policy.authorization is not None:
            return
        from . import authz as _authz

        az = _authz.load(self.root, target)
        if az is None:
            return
        self.policy = Policy(authorization=az, confirm=self.policy.confirm,
                             max_timeout=self.policy.max_timeout,
                             sandbox_roots=self.policy.sandbox_roots)
        _authz.audit(self.con, "authz.loaded", target,
                     {"roots": len(az.roots), "active": az.active(),
                      "mcp": az.allow_mcp, "external_mcp": az.allow_external_mcp})

    def _budget_snapshot(self) -> dict:
        """本次调用生效的执行边界(持久化到 runs.budget_note,resume 时可核对)。"""
        return {"max_model_calls": self.limits.max_model_calls,
                "max_seconds": self.limits.max_seconds,
                "max_tool_seconds": self.limits.max_tool_seconds,
                "context_tokens": self.limits.context_tokens}

    def start(self, target: str, goal: str | None = None) -> str:
        t = self.domain.require_active_target(target)
        self._load_authorization(target)
        rid = new_id("R")
        now = _now()
        note = json.dumps({"limits": self._budget_snapshot(), "set_at": now, "via": "start"},
                          ensure_ascii=False)
        with _db.Tx(self.con) as c:
            c.execute("INSERT INTO runs(id,target_id,state,step,rounds,goal,pid,pid_starttime,host,"
                      "model,heartbeat_at,started_at,updated_at,budget_note)"
                      " VALUES(?,?,'running',0,0,?,?,?,?,?,?,?,?,?)",
                      (rid, t.id, goal, os.getpid(), _proc_starttime(os.getpid()), _host(),
                       getattr(self.model, "name", None), now, now, now, note))
            _event(c, "run.started", rid, {"target": target, "goal": goal,
                                            "limits": self._budget_snapshot()})
        if goal:
            try:
                self._conversation_write(rid, "user", goal, client_id=f"start:{rid}",
                                         meta={"source": "start"})
            except ConversationPersistError as e:
                self._set_paused(rid, f"conversation_error: {e}")
                raise
        # 授权已确定 ⇒ 这时才允许启动 MCP(启动有副作用:子进程/外网/登录态);
        # rid 已存在 ⇒ MCP 的 pgid 能登记进本 run(崩溃后可清孤儿)
        self._ensure_mcp(rid)
        return rid

    def _watchdog_start(self) -> None:
        if not self._watchdog_enabled or self._wd_thread is not None:
            return
        import threading

        from .watchdog import Watchdog

        db_path = self.con.execute("PRAGMA database_list").fetchone()[2]
        self.watchdog = Watchdog(con=None, root=self.root, limits=self.limits, db_path=db_path,
                                 heartbeat_timeout_s=self._watchdog_heartbeat, poll_s=self._watchdog_poll_s)
        self._wd_stop = threading.Event()
        self._wd_thread = self.watchdog.start_daemon(self._wd_stop)

    def _watchdog_stop(self) -> None:
        if self._wd_thread is None or self._wd_stop is None:
            return
        self._wd_stop.set()
        self._wd_thread.join(timeout=2)
        self._wd_thread = None

    def progress(self, rid: str) -> dict:
        """结构化进度快照(给外部监控用;只读)。"""
        row = self.con.execute(
            "SELECT r.id,r.state,r.step,r.rounds,r.handback_reason,r.goal,r.heartbeat_at,"
            "       r.model_calls,r.tool_calls,r.elapsed_s,r.budget_note,"
            "       t.name AS target FROM runs r JOIN targets t ON t.id=r.target_id WHERE r.id=?",
            (rid,)).fetchone()
        if not row:
            raise KeyError(f"run 不存在: {rid}")
        target = row["target"]
        events = [dict(e) for e in self.con.execute(
            "SELECT ts,kind,ref FROM events WHERE ref IN (?, (SELECT id FROM targets WHERE name=?))"
            " ORDER BY id DESC LIMIT 5", (rid, target)).fetchall()]
        return {"run_id": row["id"], "target": target, "state": row["state"],
                "steps": row["step"], "rounds": row["rounds"],
                  "model_calls": row["model_calls"], "tool_calls": row["tool_calls"],
                  "elapsed_s": row["elapsed_s"],
                "handback": row["handback_reason"], "goal": row["goal"],
                "heartbeat_at": row["heartbeat_at"],
                "blockers": self.domain.closure_blockers(target),
                "prepare_timeouts": self.prepare_timeouts,
                "prepare_abandoned": self.prepare_abandoned,
                "watchdog": (self.watchdog.health() if self.watchdog is not None else None),
                "mcp": self.mcp_health(), "mcp_skipped": self.mcp_skipped_reason,
                "limits": self.limits.describe(),
                  "budget": _safe_json(row["budget_note"]),
                "recent_events": events}

    def cancel(self, rid: str, reason: str = "用户取消") -> None:
        """请求取消一次运行(在安全点停下)。

        语义:在**安全点**停下(不打断工具内部),停 = paused + handback=user_stop,
        并杀掉该 run 的工具进程组;不写任何研究结论。
        """
        won = False
        with _db.Tx(self.con) as c:
            # 条件更新:扫描/调用之间 run 可能已 finished ⇒ 不能把完成态改回 paused
            cur = c.execute("UPDATE runs SET state='paused', handback_reason=?, updated_at=?"
                            " WHERE id=? AND state='running'",
                            (f"user_stop: {reason}", _now(), rid))
            if cur.rowcount:
                won = True
                _event(c, "run.cancelled", rid, {"reason": reason})
        if won:
            with contextlib.suppress(Exception):
                from .watchdog import Watchdog
                Watchdog(self.con, self.root)._kill_pgids(rid)
        if self.mcp_bridge is not None:
            self.mcp_bridge.close()

    def _run_prepare(self, target: str, rid: str, step: int) -> dict:
        """跑轮前准备钩子(带超时)。**超时后不等待**:钩子跑在 daemon 线程里,超时就放弃它。

        为什么不继续用 ThreadPoolExecutor:`with ThreadPoolExecutor(...)` 退出时会 `shutdown(wait=True)`
        —— 超时的任务仍在跑 ⇒ 主流程照样被拖死 ✗(实测过)。
        语义界定:Python 线程不可强杀 ⇒ 我们能保证的是"**主 Agent 不被拖死 + 迟到结果不被采纳**"。
        """
        prep = self.prepare
        if prep is None:
            return {}
        import threading

        payload = {"target": target, "run_id": rid, "step": step,
                   "derived": self.domain.derived(target)}
        box: dict = {}

        def _call():
            try:
                box["out"] = prep(payload)
            except Exception as e:                      # 钩子自己坏了也不能毁掉研究  # noqa: BLE001
                box["err"] = f"{type(e).__name__}: {e}"

        th = threading.Thread(target=_call, name="fwcore-prepare", daemon=True)
        th.start()
        th.join(timeout=self.prepare_timeout)
        if th.is_alive():
            self.prepare_timeouts += 1
            self.prepare_abandoned += 1                 # 可观测:有被放弃的钩子(可能仍在跑)
            _event(self.con, "run.prepare_timeout", rid,
                   {"timeout_s": self.prepare_timeout, "abandoned": True})
            return {}
        if "err" in box:
            _event(self.con, "run.prepare_error", rid, {"error": box["err"][:300]})
            return {}
        out = box.get("out") or {}
        if isinstance(out, dict) and out.get("context_note"):
            self._prepare_state = {"context_note": str(out["context_note"])[:4000]}
        return out if isinstance(out, dict) else {}

    def _exec_tool_call(self, ctx, call: dict, rid: str) -> dict:
        """执行一次工具调用;参数截断/非法 JSON 时**不执行**,显式失败并留痕。"""
        if call.get("args_invalid"):
            err = "tool arguments 截断或非法 JSON ⇒ 未执行(请重发完整参数)"
            _record_tool(ctx, call.get("name") or "?", call.get("args") or {}, "error", err, call.get("id"))
            return {"status": "error", "error": err, "not_executed": True}
        name = call.get("name") or ""
        if name not in self.registry.specs:
            # 模型幻觉/工具未启用(如 --no-mcp)都必须**留痕**:审计里要能看到"它试图调什么"
            err = f"工具不存在: {name}"
            _record_tool(ctx, name or "?", call.get("args") or {}, "error", err, call.get("id"))
            return {"status": "error", "error": err}
        return self.registry.call(ctx, name, call.get("args") or {},
                                  call_key=call.get("id"), run_id=rid)

    def _external_stop(self, rid: str) -> str | None:
        """外部是否要求停(取消/别的进程置 paused)。"""
        row = self.con.execute("SELECT state, handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
        if not row:
            return "run 不存在"
        if (row["handback_reason"] or "").startswith("user_stop"):
            return row["handback_reason"]
        if row["state"] != "running":
            return f"run 状态已变为 {row['state']}"
        return None

    def _heartbeat(self, rid: str) -> bool:
        """刷新心跳(仅当自己仍是 owner)。CAS:pid/starttime 对不上 ⇒ 不抢回所有权。

        为什么必须条件更新:若 A 接管了 run,B 的下一轮 turn 若无条件写 pid,会把 owner
        抢回来 ⇒ 两个进程来回"抢所有权"各自继续跑(重复付费/副作用)。返回 False = 已失去所有权。
        """
        cur = None
        me = os.getpid()
        st = _proc_starttime(me)
        with _db.Tx(self.con) as c:
            cur = c.execute(
                "UPDATE runs SET heartbeat_at=?, pid=?, pid_starttime=?, host=?, updated_at=?"
                " WHERE id=? AND (pid IS NULL OR (pid=? AND (pid_starttime IS NULL OR pid_starttime=?)))",
                (_now(), me, st, _host(), _now(), rid, me, st))
        return cur is not None and cur.rowcount > 0

    def _update_if_running(self, rid: str, operation: str, params: tuple) -> bool:
        """CAS:只有"自己仍是 owner 且 run 仍 running"才写;检查与写入是同一条 UPDATE。

        `operation` selects a fixed SQL statement; no caller-controlled SQL
        fragment is concatenated into the query.
        """
        if operation not in _RUNNING_UPDATES:
            raise ValueError(f"未知 run 更新操作: {operation}")
        me = os.getpid()
        st = _proc_starttime(me)
        cur = None
        with _db.Tx(self.con) as c:
            cur = c.execute(_RUNNING_UPDATES[operation], (*params, rid, me, st))
        return cur is not None and cur.rowcount > 0

    def _owns(self, rid: str) -> bool:
        """本进程是否当前 owner(pid + starttime)。pid 为 NULL 的旧行按"无主"放行。"""
        row = self.con.execute("SELECT pid,pid_starttime FROM runs WHERE id=?", (rid,)).fetchone()
        if not row:
            return False
        if row["pid"] is None:
            return True
        if row["pid"] != os.getpid():
            return False
        cur = _proc_starttime(os.getpid())
        return row["pid_starttime"] in (None, cur)

    def is_running_owner(self, rid: str) -> bool:
        """统一检查:run 仍 running 且当前进程是 owner。

        `_owns` 只回答"pid/starttime 是否还是我",不回答"run 是否已被取消";
        模型请求前必须用这个更严格的检查。
        """
        row = self.con.execute(
            "SELECT state,pid,pid_starttime FROM runs WHERE id=?", (rid,)).fetchone()
        if not row or row["state"] != "running":
            return False
        if row["pid"] is None:
            return True
        if row["pid"] != os.getpid():
            return False
        cur = _proc_starttime(os.getpid())
        return row["pid_starttime"] in (None, cur)

    def _claim_run(self, rid: str) -> None:
        """原子接管 run:活主存在 ⇒ 拒绝;CAS 失败 ⇒ 被别人抢先。

        为什么需要:两个进程同时 resume 同一 run 会各跑一份 —— 双倍模型调用(付费)、
        重复工具副作用、checkpoint 分叉。SQLite 的写事务只保证单条 UPDATE 原子,
        不保证"读状态→接管"这段逻辑,必须用 CAS 把竞争收成一次条件更新。
        """
        row = self.con.execute(
            "SELECT state,pid,pid_starttime,host FROM runs WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError(f"run 不存在: {rid}")
        if row["pid"] and row["pid"] != os.getpid() and (row["host"] or "") == _host() \
                and _pid_alive(row["pid"]):
            cur = _proc_starttime(row["pid"])
            if row["pid_starttime"] in (None, cur):
                raise RuntimeError(f"run {rid} 正在被 pid {row['pid']} 执行(拒绝并发 resume)")
        from .watchdog import Watchdog
        wd = Watchdog(self.con, self.root)
        snap = wd._pgids_entries(rid)      # 接管前快照:接管后新 owner 追加的进程组不能杀
        note = json.dumps({"limits": self._budget_snapshot(), "set_at": _now(), "via": "resume"},
                          ensure_ascii=False)
        with _db.Tx(self.con) as c:
            cur = c.execute(
                "UPDATE runs SET pid=?, pid_starttime=?, host=?, heartbeat_at=?, state='running',"
                " handback_reason=NULL, budget_note=?, updated_at=?"
                " WHERE id=? AND state=? AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
                (os.getpid(), _proc_starttime(os.getpid()), _host(), _now(), note, _now(),
                 rid, row["state"], -1 if row["pid"] is None else row["pid"],
                 -1 if row["pid_starttime"] is None else row["pid_starttime"]))
            if not cur.rowcount:
                raise RuntimeError("run 已被其他进程接管(状态已变化);拒绝继续")
        with _db.Tx(self.con) as c:
            # 旧 owner 崩溃时留下的 running 工具行:没有进程会再更新它 ⇒ 标 cancelled,
            # 否则恢复后重放同一 call_key 会永远撞"正在运行",状态也永远失真
            c.execute("UPDATE tool_calls SET status='cancelled',"
                      " note=COALESCE(note,'') || ' [owner taken over]'"
                      " WHERE run_id=? AND status='running'", (rid,))
        dead_owner = bool(row["pid"]) and (row["host"] or "") == _host() and not _pid_alive(row["pid"])
        if dead_owner:
            # 旧 owner 已死 ⇒ 孤儿进程组清掉;只用接管前快照,别杀新 owner 刚起的工具
            with contextlib.suppress(Exception):
                wd._kill_pgids(rid, snap)

    def context(self, target: str) -> dict:
        d = self.domain.derived(target)
        events = [dict(r) for r in self.con.execute(
            "SELECT ts,kind,ref,payload FROM events ORDER BY id DESC LIMIT 20").fetchall()]
        events.reverse()
        return {"derived": d, "recent_events": events}

    def _restore_tool_activation(self, history: list[dict], rid: str | None = None) -> None:
        """Rebuild deferred-tool activation from session state, not one result."""
        names: list[str] = []
        if rid:
            names.extend(self.domain.active_tools(rid))
        for msg in history:
            if msg.get("role") != "tool" or msg.get("name") != "tool_search":
                continue
            try:
                data = json.loads(msg.get("content") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            loaded = data.get("loaded") if isinstance(data, dict) else None
            if isinstance(loaded, list):
                names.extend(str(x) for x in loaded if isinstance(x, str))
        self.registry.reset_active_deferred()
        if names:
            self.registry.activate_deferred(names)

    def _session_rows(self, rid: str):
        """Rows that belong to the model-visible session.

        Unlike tool output, a cancelled tool result is still required for the
        tool_call/tool_result protocol; cancelled/queued user input is not.
        """
        rows = self.con.execute(
            "SELECT * FROM conversation_entries WHERE run_id=? ORDER BY created_at, rowid",
            (rid,)).fetchall()
        out = []
        for row in rows:
            role = row["role"]
            status = row["status"]
            if role == "user" and status in ("queued", "cancelled"):
                continue
            if role == "assistant" and status == "queued":
                continue
            if role == "tool" and not row["tool_call_id"]:
                continue
            out.append(row)
        return out

    def _model_id(self) -> str:
        return str(getattr(self.model, "model", "") or getattr(self.model, "name", "") or "")

    def _model_scope(self) -> tuple[str, str, str]:
        """Provider/api/model identity used for thinking replay (Pi semantics)."""
        return (str(getattr(self.model, "name", "") or ""),
                str(getattr(self.model, "api", "") or ""),
                self._model_id())

    def _load_history(self, rid: str, current_model: str | None = None,
                      current_scope: tuple[str, str, str] | None = None) -> list[dict]:
        """Rebuild the active branch from append-only conversation entries.

        Pi keeps session entries and appends a compaction marker; it does not
        replace the transcript with a full-history checkpoint.  This loader does
        the same: raw entries stay, and a compaction only changes the projection
        used for the next request.
        """
        rows = self._session_rows(rid)
        if not rows:
            return []

        def _tool(cid):
            row = self.con.execute(
                "SELECT tool,args FROM tool_calls WHERE id=? OR call_key=?"
                " ORDER BY rowid DESC LIMIT 1", (cid, cid)).fetchone()
            return dict(row) if row else None

        comps = self.con.execute(
            "SELECT summary,first_kept_id,created_at FROM compactions WHERE run_id=?"
            " ORDER BY rowid DESC", (rid,)).fetchall()
        start = 0
        messages: list[dict] = []
        chosen = None
        # The latest compaction is a projection hint, not the session itself.
        # If its cut was lost (old DB/partial write), fall back to the previous
        # valid compaction; if none is valid, replay the raw append-only rows.
        # Never append a summary to the full raw history: that duplicates and
        # can contradict the summarised content.
        for comp in comps:
            fk = comp["first_kept_id"]
            if fk is None:                    # explicit retain-none marker
                # The compaction entry itself is the boundary: keep entries
                # appended after it.  A bare summary would drop every future
                # message if a later turn appended after a retain-none cut.
                start = len(rows)
                for i, row in enumerate(rows):
                    if str(row["created_at"]) > str(comp["created_at"]):
                        start = i
                        break
                chosen = comp
                break
            for i, row in enumerate(rows):
                if row["id"] == fk:
                    start = i
                    chosen = comp
                    break
            if chosen is not None:
                break
        if chosen is not None:
            # A tool result cannot be the first message after a summary: its
            # assistant tool_call would be missing.  Walk back to the owning
            # assistant turn (Pi keeps whole tool groups at the cut boundary).
            while start > 0 and start < len(rows) and rows[start]["role"] == "tool":
                start -= 1
            messages.append({"role": "assistant",
                             "content": SUMMARY_HEADER + "\n" + (chosen["summary"] or "")})

        for row in rows[start:]:
            role = row["role"]
            content = row["content"] or ""
            meta = {}
            try:
                meta = json.loads(row["meta"] or "{}")
            except (TypeError, ValueError):
                meta = {}
            if role == "user":
                if content.strip():
                    messages.append({"role": "user", "content": content})
            elif role == "assistant":
                calls = []
                full = meta.get("tool_calls_full") or []
                if full:
                    for c in full:
                        calls.append({
                            "id": c.get("id"), "type": "function",
                            "function": {"name": c.get("name") or "",
                                         "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False)}})
                else:
                    for cid in meta.get("tool_calls") or []:
                        tc = _tool(cid)
                        if tc:
                            calls.append({
                                "id": cid, "type": "function",
                                "function": {"name": tc.get("tool") or "",
                                             "arguments": tc.get("args") or "{}"}})
                if content or calls:
                    msg = {"role": "assistant", "content": content}
                    # Pi replays thinking only for the model that produced it.
                    # Compare provider/api/model; legacy rows missing a scope
                    # component remain replayable for compatibility.
                    if meta.get("reasoning"):
                        replay = True
                        if current_scope is not None:
                            sp, sa, sm = current_scope
                            if ((meta.get("provider") and meta["provider"] != sp)
                                    or (meta.get("api") and meta["api"] != sa)
                                    or (meta.get("model") and meta["model"] != sm)):
                                replay = False
                        elif (current_model and meta.get("model")
                              and meta.get("model") != current_model):
                            replay = False
                        if replay:
                            msg["reasoning_content"] = meta["reasoning"]
                            if meta.get("reasoning_signature"):
                                msg["reasoning_signature"] = meta["reasoning_signature"]
                            if meta.get("reasoning_redacted"):
                                msg["reasoning_redacted"] = True
                    if calls:
                        msg["tool_calls"] = calls
                    messages.append(msg)
            elif role == "tool" and row["tool_call_id"]:
                # Keep the model-visible payload shape: the DB call id is
                # db_tool_call_id, never the protocol tool_call_id.
                try:
                    payload = json.loads(content)
                except (TypeError, ValueError):
                    payload = None
                if isinstance(payload, dict) and "tool_call_id" in payload \
                        and "db_tool_call_id" not in payload:
                    payload["db_tool_call_id"] = payload.pop("tool_call_id")
                    content = json.dumps(payload, ensure_ascii=False)
                msg = {"role": "tool", "tool_call_id": row["tool_call_id"],
                       "content": content}
                if meta.get("tool"):
                    msg["name"] = meta["tool"]
                if meta.get("parts"):
                    msg["parts"] = meta["parts"]
                messages.append(msg)
        # Legacy/cancelled rows can contain an assistant tool_call whose result
        # never existed.  Keep the assistant text but do not expose an unpaired
        # call; a call with at least one result keeps the paired calls.
        pruned: list[dict] = []
        i = 0
        while i < len(messages):
            msg = messages[i]
            if msg.get("role") == "assistant" and msg.get("tool_calls"):
                j = i + 1
                tool_msgs = []
                while j < len(messages) and messages[j].get("role") == "tool":
                    tool_msgs.append(messages[j])
                    j += 1
                result_ids = {m.get("tool_call_id") for m in tool_msgs}
                calls = msg.get("tool_calls") or []
                kept = [c for c in calls if c.get("id") in result_ids]
                if len(kept) != len(calls):
                    msg = dict(msg)
                    if kept:
                        msg["tool_calls"] = kept
                    else:
                        msg.pop("tool_calls", None)
                pruned.append(msg)
                pruned.extend(tool_msgs)
                i = j
            else:
                pruned.append(msg)
                i += 1
        messages = pruned
        # A compaction boundary can start at a tool result whose assistant
        # tool_call was just before the boundary.  Do not return a dangling
        # tool result: reattach a minimal assistant call using the DB args.
        repaired: list[dict] = []
        pending: set[str] = set()
        for msg in messages:
            if msg.get("role") == "assistant":
                pending.update(c.get("id") for c in (msg.get("tool_calls") or []))
            elif msg.get("role") == "tool":
                cid = msg.get("tool_call_id")
                if cid and cid not in pending:
                    tc = _tool(cid)
                    if tc:
                        repaired.append({
                            "role": "assistant", "content": "",
                            "tool_calls": [{"id": cid, "type": "function",
                                            "function": {"name": tc.get("tool") or "",
                                                         "arguments": tc.get("args") or "{}"}}],
                        })
                        pending.add(cid)
            repaired.append(msg)
        messages = repaired
        try:
            return _sanitize_history(messages, _tool)
        except (TypeError, ValueError) as e:
            with _db.Tx(self.con) as c:
                _event(c, "run.session_corrupt", rid, {"error": str(e)[:200]})
            return []

    def _artifact_of(self, tool_call_id: str | None) -> str | None:
        """恢复用:工具调用 → 产物路径(偏好 stdout,其次 result)。"""
        if not tool_call_id:
            return None
        # 历史消息里的 id 是**协议** id(call_…),DB 主键是 TC-…;call_key 保存了协议 id
        row = self.con.execute(
            "SELECT stdout_path, result_path FROM tool_calls WHERE id=? OR call_key=?",
            (tool_call_id, tool_call_id)).fetchone()
        if not row:
            return None
        p = row["stdout_path"] or row["result_path"]
        if not p:
            return None
        return os.path.relpath(p, self.root) if os.path.isabs(p) else p

    def _latest_compaction_summary(self, rid: str) -> str:
        row = self.con.execute(
            "SELECT summary FROM compactions WHERE run_id=? ORDER BY rowid DESC LIMIT 1",
            (rid,)).fetchone()
        return (row["summary"] or "") if row else ""

    def _context_usage_for_tool(self, rid: str) -> dict | None:
        used = self._last_main_prompt_tokens(rid)
        window = self.limits.context_tokens or 0
        if not used or not window:
            return None
        return {"used": used, "window": window, "percent": used / window * 100.0}

    def _request_compaction(self, rid: str, keep: str, next_step: str) -> dict:
        """Validate/defer a model-called compact_now request (Pi fuel-gauge)."""
        if self._compact_pending is not None:
            return {"status": "error",
                    "error": "A compaction is already queued or running."}
        usage = self._context_usage_for_tool(rid)
        if usage is None:
            return {"status": "error",
                    "error": "Not compacting: current context usage is unknown."}
        percent = usage["percent"]
        try:
            minimum = float(os.environ.get("FW_COMPACT_MIN_PERCENT", "75"))
        except ValueError:
            minimum = 75.0
        if percent < minimum and percent < 80.0:
            return {"status": "error",
                    "error": f"Not compacting: context is only {percent:.1f}% full; "
                             f"compact at {minimum:.0f}% or more."}
        if (percent < 80.0 and self._compact_percent_after is not None
                and percent < self._compact_percent_after + 20.0):
            return {"status": "error",
                    "error": "Not compacting: context has not grown enough since the last compaction."}
        if self._compact_auto_resumes >= 5:
            return {"status": "error",
                    "error": "Not compacting: self-compaction limit reached for this session (5)."}
        self._compact_pending = {
            "keep": str(keep).strip()[:8000],
            "next_step": str(next_step).strip()[:4000],
        }
        return {"status": "ok", "queued": True,
                "note": "Compaction queued for the idle boundary. Stop here; "
                        "the next step will resume automatically."}

    def compact_now(self, rid: str, instructions: str = "") -> dict:
        """Pi-style manual compaction: append a summary marker, keep raw entries."""
        row = self.con.execute("SELECT target_id,state FROM runs WHERE id=?", (rid,)).fetchone()
        if not row:
            raise KeyError(f"run 不存在: {rid}")
        history = self._load_history(rid, self._model_id(), self._model_scope())
        if not history:
            raise RuntimeError("当前 session 没有可压缩的历史")
        before = estimate_tokens(history)
        previous_summary = self._latest_compaction_summary(rid)
        _event(self.con, "run.compacting", rid, {"tokens": before, "manual": True})

        def _summarize(chunk):
            if self.summarizer:
                return self.summarizer(chunk)
            text = _render_for_summary(chunk, previous_summary)
            if instructions.strip():
                text += "\n\nAdditional focus: " + instructions.strip()[:4000]
            reply = _coerce_reply(self.model.chat(_summary_request(text), []))
            _event(self.con, "model.call", rid, {
                "kind": "summarize", "manual": True, "n_messages": len(chunk),
                "usage_known": bool(reply.usage), "usage": reply.usage or {},
                "prompt_tokens": (reply.usage or {}).get("prompt_tokens"),
                "completion_tokens": (reply.usage or {}).get("completion_tokens"),
                "cached_tokens": ((reply.usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens"),
            })
            return _summary_reply(reply)

        def _summarize_prefix(chunk):
            if self.summarizer:
                return self.summarizer(chunk)
            text = _render_turn_prefix(chunk, instructions)
            reply = _coerce_reply(self.model.chat(_summary_request(text), []))
            _event(self.con, "model.call", rid, {
                "kind": "summarize", "manual": True, "split_prefix": True,
                "n_messages": len(chunk),
                "usage_known": bool(reply.usage), "usage": reply.usage or {},
                "prompt_tokens": (reply.usage or {}).get("prompt_tokens"),
                "completion_tokens": (reply.usage or {}).get("completion_tokens"),
                "cached_tokens": ((reply.usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens"),
            })
            return _summary_reply(reply)

        try:
            new_history, info = compact_history(
                history, _summarize, self.limits.keep_recent_tokens,
                chunk_tokens=self.limits.summarize_chunk_tokens,
                max_calls=self.limits.summarize_max_calls, single_call=True,
                previous_summary=previous_summary, summarize_prefix=_summarize_prefix)
            if not info.get("compacted"):
                raise RuntimeError("没有可压缩的完整协议组")
            summary = new_history[0].get("content", "")
            if SUMMARY_HEADER in summary:
                summary = summary.split(SUMMARY_HEADER, 1)[1].lstrip("\n")
            recent_count = max(0, len(new_history) - 1)
            recent = self._session_rows(rid)
            recent = recent[-recent_count:] if recent_count else []
            first_kept_id = recent[0]["id"] if recent else None
            self.domain.add_compaction(rid, summary, first_kept_id, before)
        except Exception as e:
            _event(self.con, "run.compact_failed", rid,
                   {"tokens": before, "manual": True, "error": str(e)[:300]})
            raise
        result = {"before_tokens": before, "after_tokens": estimate_tokens(new_history),
                  "manual": True, **info}
        self._last_compact = result
        _event(self.con, "run.compacted", rid, result)
        return result

    def _checkpoint(self, rid: str, step: int, history: list[dict]) -> bool:
        """Advance the run pointer; history lives in append-only conversation entries."""
        me = os.getpid()
        st = _proc_starttime(me)
        ok = False
        with _db.Tx(self.con) as c:
            cur = c.execute(
                "UPDATE runs SET step=?, updated_at=? WHERE id=?"
                " AND state IN ('running','paused')"
                " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
                (step, _now(), rid, me, st))
            if not cur.rowcount:
                return False
            # The model history is append-only in conversation_entries.  Keep
            # only tiny step pointers here for compatibility/inspection; never
            # write a full-history snapshot.
            c.execute("INSERT INTO checkpoints(id,run_id,step,messages,created_at)"
                      " VALUES(?,?,?,?,?)", (new_id("CK"), rid, step, '{"type":"pointer"}', _now()))
            c.execute("DELETE FROM checkpoints WHERE run_id=? AND id NOT IN "
                      "(SELECT id FROM checkpoints WHERE run_id=? ORDER BY step DESC LIMIT 2)",
                      (rid, rid))
            ok = True
        return ok

    def _limit_hit(self, rid: str, reason: str, detail: dict | None = None) -> None:
        """记录"执行边界触发"。**只暂停**,不写任何研究结论。"""
        row = self.con.execute("SELECT budget_note FROM runs WHERE id=?", (rid,)).fetchone()
        try:
            note = json.loads(row["budget_note"]) if row and row["budget_note"] else {}
        except (TypeError, ValueError):
            note = {}
        note["last_limit"] = {"reason": reason, "detail": detail or {}, "at": _now()}
        # 只有"仍 running 且仍是 owner"才写:已被取消/看护暂停时不覆盖对方的状态与理由
        if not self._update_if_running(
                rid, "limit", (_now(), json.dumps(note, ensure_ascii=False))):
            return
        _event(self.con, "run.limit_hit", rid, {"reason": reason, **(detail or {})})

    # ---------------- 流式模型调用 ----------------
    def _reserve_call(self, rid: str, n: int) -> bool:
        """发起请求前先把计数落库;只有仍是 running 的 owner 才能预留。"""
        return self._update_if_running(rid, "reserve", (n, _now()))

    def _emit_stream(self, payload: dict) -> None:
        cb = self.on_stream
        if cb is None:
            return
        with contextlib.suppress(Exception):
            cb(payload)

    def _set_paused(self, rid: str, reason: str) -> bool:
        """原子暂停;锁竞争短重试。失败返回 False(由调用方决定是否降级)。"""
        for attempt in range(5):
            try:
                return self._update_if_running(
                    rid, "pause", (reason[:400], _now()))
            except _db.LockedError:
                time.sleep(0.05 * (attempt + 1))
        return False

    def _conversation_write(self, *args, **kwargs):
        """对话写入:短重试只针对锁竞争;最终失败必须显式抛错,不能吞掉。"""
        last: Exception | None = None
        for attempt in range(3):
            try:
                return self.domain.add_conversation(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                last = e
                if isinstance(e, _db.LockedError) or "locked" in str(e).lower():
                    time.sleep(0.05 * (attempt + 1))
                    continue
                break
        raise ConversationPersistError(f"对话写入失败: {last}") from last

    def _persist_counters(self, rid: str, calls: int, tools: int, elapsed: float) -> bool:
        """计数落库也必须带 owner 条件(检查与写入同一条 UPDATE)。"""
        me = os.getpid()
        st = _proc_starttime(me)
        cur = None
        with _db.Tx(self.con) as c:
            cur = c.execute(
                "UPDATE runs SET model_calls=MAX(model_calls,?),"
                " tool_calls=MAX(tool_calls,?), elapsed_s=MAX(elapsed_s,?)"
                " WHERE id=? AND state IN ('running','paused')"
                " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
                (calls, tools, elapsed, rid, me, st),
            )
        return cur is not None and cur.rowcount > 0

    def _persist_assistant(self, rid: str, reply: Reply, *, status: str,
                           call_seq: int, error: str = "") -> None:
        """对话记录只存**面向用户**的内容;reasoning 只记长度,不混进正文。"""
        if not (reply.content or reply.reasoning or reply.tool_calls):
            return
        scope = self._model_scope()
        meta = {
            "provider": scope[0],
            "api": scope[1],
            "model": scope[2],
            "finish_reason": reply.finish_reason or "",
            "usage": reply.usage or {},
            "tool_calls": [c.get("id") for c in reply.tool_calls],
            "tool_calls_full": [{"id": c.get("id"), "name": c.get("name"),
                                  "args": c.get("args") or {}} for c in reply.tool_calls],
            "reasoning": reply.reasoning or "",
            "reasoning_len": len(reply.reasoning or ""),
            "reasoning_signature": reply.reasoning_signature or "",
            "reasoning_redacted": bool(reply.reasoning_redacted),
        }
        if error:
            meta["error"] = error[:300]
        try:
            self._conversation_write(
                rid, "assistant", reply.content, status=status,
                client_id=f"asst:{rid}:{call_seq}", meta=meta)
        except ConversationPersistError as e:
            e.reply = reply
            self._emit_stream({"type": "conversation_error", "run": rid,
                               "call": call_seq, "phase": "assistant",
                               "error": str(e)[:300]})
            raise

    @staticmethod
    def _reply_from_stream(content: list[str], reasoning: list[str],
                           calls: dict, finish: str, usage: dict) -> Reply:
        out = []
        for idx in sorted(calls):
            slot = calls[idx]
            raw = "".join(slot.get("args") or [])
            invalid = bool(slot.get("invalid"))
            try:
                args = json.loads(raw or "{}")
                if not isinstance(args, dict):
                    raise TypeError("tool arguments 不是对象")
            except (json.JSONDecodeError, TypeError):
                args = {"__invalid_json__": raw}
                invalid = True
            out.append({"id": slot.get("id") or f"call_{idx}",
                        "name": slot.get("name") or "",
                        "args": args, "args_raw": raw, "args_invalid": invalid})
        if not finish:
            finish = "tool_calls" if out else "stop"
        return Reply(content="".join(content), reasoning="".join(reasoning),
                     tool_calls=out, finish_reason=finish, usage=usage or {})

    @staticmethod
    def _reply_status(reply: Reply) -> str:
        return ("partial" if (reply.finish_reason or "").lower() in ("length", "content_filter")
                else "ok")

    @staticmethod
    def _protocol_safe_reply(reply: Reply, status: str) -> Reply:
        """Never expose a tool call that did not complete and cannot be executed.

        A cancelled/errored stream (or a length/content-filter stop) can contain
        a partial tool call.  Persisting it without a matching tool result makes
        the restored context invalid.  Keep the text/reasoning, drop the
        incomplete call group; a later turn may issue a fresh, complete call.
        """
        if status not in ("cancelled", "error", "partial") or not reply.tool_calls:
            return reply
        safe = Reply(content=reply.content, reasoning=reply.reasoning,
                     finish_reason=reply.finish_reason, usage=reply.usage or {},
                     tool_calls=[], dropped_tool_calls=len(reply.tool_calls))
        return safe

    def _model_call(self, rid: str, msgs: list[dict], tools: list[dict],
                    call_seq: int) -> Reply:
        """一次模型调用:流式事件 → 完整 Reply;工具参数只有收全后才组装。"""
        ctrl = StreamControl()
        self.stream_control = ctrl
        streamer = getattr(self.model, "stream", None)
        streamer_fn: Any = streamer if callable(streamer) else None
        if getattr(self.model, "supports_stream", True) is False:
            streamer_fn = None
        if not (self.stream_enabled and streamer_fn is not None):
            try:
                reply = _coerce_reply(self.model.chat(msgs, tools))
            finally:
                self.stream_control = None
            self._emit_stream({"type": "assistant_start", "run": rid, "call": call_seq})
            if reply.content:
                self._emit_stream({"type": "assistant_delta", "run": rid,
                                   "call": call_seq, "text": reply.content})
            if reply.reasoning:
                self._emit_stream({"type": "reasoning_delta", "run": rid,
                                   "call": call_seq, "text": reply.reasoning})
            status = self._reply_status(reply)
            safe = self._protocol_safe_reply(reply, status)
            if safe is not reply:
                _event(self.con, "model.tool_calls_not_persisted", rid,
                       {"status": status, "finish_reason": reply.finish_reason or "",
                        "tool_calls": len(reply.tool_calls)})
            self._emit_stream({"type": "assistant_done", "run": rid, "call": call_seq,
                               "text": safe.content, "reasoning": safe.reasoning,
                               "finish_reason": safe.finish_reason,
                               "usage": safe.usage or {}, "status": status,
                               "tool_calls": len(safe.tool_calls)})
            self._persist_assistant(rid, safe, status=status, call_seq=call_seq)
            return safe
        content: list[str] = []
        reasoning: list[str] = []
        calls: dict = {}
        finish = ""
        usage: dict = {}
        started = False

        def start() -> None:
            nonlocal started
            if not started:
                started = True
                self._emit_stream({"type": "assistant_start", "run": rid, "call": call_seq})

        try:
            for raw in streamer_fn(msgs, tools, control=ctrl):
                ev = as_event(raw)
                if ev.type == "start":
                    start()
                elif ev.type == "text_delta":
                    start()
                    content.append(ev.text)
                    self._emit_stream({"type": "assistant_delta", "run": rid,
                                       "call": call_seq, "text": ev.text})
                elif ev.type == "reasoning_delta":
                    start()
                    reasoning.append(ev.text)
                    self._emit_stream({"type": "reasoning_delta", "run": rid,
                                       "call": call_seq, "text": ev.text})
                elif ev.type == "toolcall_start":
                    start()
                    slot = calls.setdefault(ev.index, {"id": "", "name": "", "args": [],
                                                       "invalid": False})
                    if ev.call_id:
                        slot["id"] = ev.call_id
                    if ev.name:
                        slot["name"] = ev.name
                    if ev.invalid:
                        slot["invalid"] = True
                    self._emit_stream({"type": "toolcall_start", "run": rid, "call": call_seq,
                                       "index": ev.index, "call_id": slot["id"],
                                       "name": slot["name"]})
                elif ev.type == "toolcall_delta":
                    start()
                    slot = calls.setdefault(ev.index, {"id": "", "name": "", "args": [],
                                                       "invalid": False})
                    slot["args"].append(ev.arguments)
                    self._emit_stream({"type": "toolcall_delta", "run": rid, "call": call_seq,
                                       "index": ev.index, "text": ev.arguments})
                elif ev.type == "done":
                    finish = ev.finish_reason or finish
                    if ev.usage:
                        usage = ev.usage
                elif ev.type == "error":
                    raise StreamError(ev.error or "stream error")
            start()
            reply = self._reply_from_stream(content, reasoning, calls, finish, usage)
            status = self._reply_status(reply)
            safe = self._protocol_safe_reply(reply, status)
            if safe is not reply:
                _event(self.con, "model.tool_calls_not_persisted", rid,
                       {"status": status, "finish_reason": reply.finish_reason or "",
                        "tool_calls": len(reply.tool_calls)})
            self._emit_stream({"type": "assistant_done", "run": rid, "call": call_seq,
                               "text": safe.content, "reasoning": safe.reasoning,
                               "finish_reason": safe.finish_reason,
                               "usage": safe.usage or {}, "status": status,
                               "tool_calls": len(safe.tool_calls)})
            self._persist_assistant(rid, safe, status=status, call_seq=call_seq)
            return safe
        except ConversationPersistError:
            # The assistant session write failed; turn() owns the pause path.
            # Re-persisting it as a stream error would duplicate or mislabel it.
            raise
        except StreamCancelled as exc:
            reply = self._reply_from_stream(content, reasoning, calls, finish, usage)
            safe = self._protocol_safe_reply(reply, "cancelled")
            exc.reply = safe
            if safe is not reply:
                _event(self.con, "model.tool_calls_not_persisted", rid,
                       {"status": "cancelled", "finish_reason": reply.finish_reason or "",
                        "tool_calls": len(reply.tool_calls)})
            self._emit_stream({"type": "assistant_done", "run": rid, "call": call_seq,
                               "text": safe.content, "reasoning": safe.reasoning,
                               "finish_reason": safe.finish_reason, "usage": safe.usage,
                               "status": "cancelled", "tool_calls": len(safe.tool_calls)})
            self._persist_assistant(rid, safe, status="cancelled", call_seq=call_seq)
            raise
        except Exception as e:
            reply = self._reply_from_stream(content, reasoning, calls, finish, usage)
            safe = self._protocol_safe_reply(reply, "error")
            if safe is not reply:
                _event(self.con, "model.tool_calls_not_persisted", rid,
                       {"status": "error", "finish_reason": reply.finish_reason or "",
                        "tool_calls": len(reply.tool_calls)})
            self._emit_stream({"type": "assistant_error", "run": rid, "call": call_seq,
                               "text": safe.content, "reasoning": safe.reasoning,
                               "error": str(e)[:500], "tool_calls": len(safe.tool_calls)})
            self._persist_assistant(rid, safe, status="error", call_seq=call_seq,
                                    error=str(e))
            if isinstance(e, StreamError):
                e.reply = safe
                raise
            if isinstance(e, ModelHTTPError):
                wrapped = StreamError(str(e))
                wrapped.reply = safe
                raise wrapped from e
            raise
        finally:
            self.stream_control = None

    def _record_tool_conversation(self, rid: str, call: dict, out: dict,
                                  content: str | None = None) -> None:
        status = str(out.get("status") or "ok")
        cstatus = {"ok": "ok", "error": "error", "timeout": "error",
                   "cancelled": "cancelled"}.get(status, "error")
        # Persist the exact model-visible projection.  A field whitelist here
        # made resume see a different result than the live turn (structured,
        # observation, result_path, tool_search loaded/tools, ...).
        # Callers pass the already-built string so one tool result cannot get
        # two different temp paths in memory and SQLite.
        content = content if content is not None else _bounded_tool_result(out)

        call_id = str(call.get("id") or call.get("name") or "?")
        try:
            self._conversation_write(
                rid, "tool", content, status=cstatus,
                tool_call_id=call.get("id"),
                client_id=f"tool:{rid}:{call_id}",
                meta={"tool": call.get("name"), "status": status,
                      "parts": out.get("parts") or []})
        except ConversationPersistError as e:
            self._emit_stream({"type": "conversation_error", "run": rid,
                               "phase": "tool", "tool": call.get("name"),
                               "error": str(e)[:300]})
            raise

    def _record_cancelled_tool_results(self, rid: str, calls: list[dict], reason: str,
                                       history: list[dict]) -> bool:
        """Persist a tool result for every call that will not execute.

        This is used when cancellation/finalization happens *after* an assistant
        reply with tool calls was already persisted.  Without this, the session
        would contain an assistant tool_call with no matching tool result.
        """
        ok = True
        for c in calls:
            cid = c.get("id")
            content = json.dumps({"status": "cancelled", "error": reason},
                                 ensure_ascii=False)
            history.append({"role": "tool", "tool_call_id": cid,
                            "name": c.get("name"), "content": content})
            try:
                self._conversation_write(
                    rid, "tool", content, status="cancelled", tool_call_id=cid,
                    client_id=f"tool:{rid}:{cid}",
                    meta={"tool": c.get("name"), "status": "cancelled"})
            except ConversationPersistError as e:
                ok = False
                self._emit_stream({"type": "conversation_error", "run": rid,
                                   "phase": "cancelled-tool-batch",
                                   "error": str(e)[:300]})
        return ok

    # ---------------- 一轮 = 一个模型-工具交互序列 ----------------
    def _inject_steering(self, rid: str, target: str, history: list[dict]) -> str:
        """Persist and insert one queued user message before the next request.

        Returns "none" when there is no queued item, "ok" after a successful
        insert, and "error" when the session write failed (the caller must
        pause; the pending item is not acknowledged and remains queued).
        """
        callback = self.on_steering
        if callback is None:
            return "none"
        try:
            item = callback(rid, target)
        except Exception as e:  # noqa: BLE001 - a queue failure must not kill the turn
            with contextlib.suppress(Exception):
                _event(self.con, "run.steering_failed", rid,
                       {"error": str(e)[:300]})
            return "none"
        if not item:
            return "none"
        text = str(item.get("text") or "").strip()
        client_id = str(item.get("client_id") or "")
        if not text:
            return "none"
        if not self.is_running_owner(rid):
            return "none"
        try:
            # Runner's own connection owns the turn; do not write through the
            # UI/application connection from the worker thread.
            self._conversation_write(
                rid, "user", text, status="ok", client_id=client_id or None,
                meta={"source": "steer"})
        except ConversationPersistError as e:
            with contextlib.suppress(Exception):
                _event(self.con, "run.steering_failed", rid,
                       {"error": str(e)[:300], "client_id": client_id})
            self._set_paused(rid, f"conversation_error: {str(e)[:300]}")
            return "error"
        ack = self.on_steering_ack
        if ack is not None and client_id:
            with contextlib.suppress(Exception):
                ack(client_id)
        history.append({"role": "user", "content": text})
        with contextlib.suppress(Exception):
            _event(self.con, "run.steering", rid, {"client_id": client_id})
        return "ok"

    def turn(self, rid: str, target: str, goal: str | None, history: list[dict],
             budget: int | None = None, *, inject_state: bool = True) -> RunResult:
        budget = self.max_steps if budget is None else budget      # None = 不限步数
        step = self.con.execute("SELECT step,rounds FROM runs WHERE id=?", (rid,)).fetchone()["step"]
        rounds = self.con.execute("SELECT rounds FROM runs WHERE id=?", (rid,)).fetchone()["rounds"]
        final = ""
        stopped_by_final = False
        state = "running"
        if not self._heartbeat(rid):     # 先确认/刷新 owner;已被接管 ⇒ 立即退出
            return RunResult(rid, "paused", step, rounds, "", False, [], "owner_lost", history)
        me = os.getpid()
        st = _proc_starttime(me)
        row = None
        with _db.Tx(self.con) as c:
            # 检查 handback / owner / 状态与更新必须是同一条 UPDATE:
            # 否则取消(写 user_stop)可能正好落在检查与写 running 之间。
            c.execute(
                "UPDATE runs SET state='running', updated_at=?"
                " WHERE id=? AND state IN ('paused','finished')"
                " AND (handback_reason IS NULL OR handback_reason NOT LIKE 'user_stop%')"
                " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
                (_now(), rid, me, st))
            row = c.execute(
                "SELECT state,handback_reason,pid,pid_starttime FROM runs WHERE id=?",
                (rid,)).fetchone()
        if row is None:
            return RunResult(rid, "paused", step, rounds, "", False, [], "owner_lost", history)
        if row["pid"] not in (None, me) or row["pid_starttime"] not in (None, st):
            return RunResult(rid, "paused", step, rounds, "", False, [], "owner_lost", history)
        if row["state"] != "running":
            reason = row["handback_reason"] or "run_not_running"
            return RunResult(rid, "paused", step, rounds, "", False, [], reason, history)
        self._watchdog_start()           # 心跳在起看护之前,避免旧 pid 被误判 owner_dead
        self._ensure_mcp(rid)          # 跨轮/恢复:MCP 必须真的可用(被关过就重开)
        if self.mcp_bridge is not None:
            # Background startup is allowed, but the first model request must
            # not silently miss direct/deferred tools.
            self.mcp_bridge.wait_ready(10.0)
            # tools/list_changed 可能只在请求时被读到；每个 turn 开头主动刷新一次，
            # 避免模型长期拿旧工具清单。刷新失败保留旧工具。
            self.mcp_bridge.refresh_tools(force=True)
        self._restore_tool_activation(history, rid)
        # run 级预算:从数据库读累计值(暂停/恢复/多轮都不重置)。
        # 定义在 try 之外 ⇒ 异常路径也能安全落库(避免 _persist 未绑定)
        row0 = self.con.execute("SELECT model_calls, tool_calls, elapsed_s FROM runs WHERE id=?",
                                (rid,)).fetchone()
        try:
            run_calls = int(row0["model_calls"] or 0) if row0 else 0
            run_tools = int(row0["tool_calls"] or 0) if row0 else 0
            run_elapsed = float(row0["elapsed_s"] or 0.0) if row0 else 0.0
        except (TypeError, ValueError):
            run_calls = run_tools = 0
            run_elapsed = 0.0
        t0 = time.time()
        # Conversation entries are the append-only session log.  Never invent a
        # user-only history when the append-only session log is missing; only append the
        # current turn's goal if it is not already the tail.
        if goal:
            tail = history[-1] if history else None
            if not tail or tail.get("role") != "user" or (tail.get("content") or "") != goal:
                history.append({"role": "user", "content": goal})

        def _persist() -> None:
            # 检查与写入同一条 UPDATE;并发接管后旧 owner 不能改计数
            self._persist_counters(rid, run_calls, run_tools,
                                   run_elapsed + (time.time() - t0))

        try:
            seen: dict[str, int] = {}
            state_injected = not inject_state
            empty_streak = 0
            last_content_hashes: list[str] = []
            since = _now()
            while budget is None or budget > 0:
                if not self.is_running_owner(rid):   # 被接管/已暂停:立刻退出
                    state = "paused"
                    if self._owns(rid):
                        # 仍是 owner 但状态已不是 running:外部取消/预算暂停等
                        stopped = self._external_stop(rid) or "run 状态已变化"
                        _event(self.con, "run.stopped_external", rid, {"reason": stopped})
                    else:
                        _event(self.con, "run.owner_lost", rid, {"pid": os.getpid()})
                    break
                stopped = self._external_stop(rid)
                if stopped:
                    state = "paused"
                    _event(self.con, "run.stopped_external", rid, {"reason": stopped})
                    break
                if self.stop_event is not None and self.stop_event.is_set():
                    self._set_paused(rid, "user_stop: 用户取消")
                    state = "paused"
                    _persist()
                    break
                if budget is not None:
                    budget -= 1
                if self.limits.time_exhausted(run_elapsed + (time.time() - t0)):
                    state = "paused"
                    _persist()
                    self._limit_hit(rid, "max_seconds",
                                    {"elapsed_s": round(run_elapsed + (time.time() - t0), 1),
                                     "budget_s": self.limits.max_seconds})
                    break
                prep = self._run_prepare(target, rid, step)
                if prep.get("cancel"):
                    self._limit_hit(rid, "prepare_cancel", {"note": str(prep.get("why") or "")[:120]})
                    state = "paused"
                    break
                # 上下文生命周期:接近配置窗口才压缩,保留最近一段;
                # 摘要是一次**模型调用**(计入预算),不是静默丢内容
                _last_main = self._last_main_prompt_tokens(rid)
                if (run_calls >= self._compact_cooldown_until
                        and should_compact(history, self.limits.context_tokens,
                                           self.limits.output_reserve_tokens,
                                           used_tokens=_last_main)
                        and _find_cut(history, self.limits.keep_recent_tokens) > 0):
                    before = estimate_tokens(history)
                    previous_summary = self._latest_compaction_summary(rid)
                    _event(self.con, "run.compacting", rid, {"tokens": before})

                    def _summarize(chunk, _previous_summary=previous_summary):
                        nonlocal run_calls
                        if self.summarizer:
                            return self.summarizer(chunk)
                        # 预算检查必须在**发起请求之前**(与普通调用同一条规则)
                        if self.limits.calls_exhausted(run_calls + 1):
                            raise RuntimeError("预算不足:拒绝摘要调用(历史保持原样)")
                        run_calls += 1
                        if not self._reserve_call(rid, run_calls):
                            raise RuntimeError("预算预留失败: 所有权/状态已变化")
                        if not self.is_running_owner(rid):
                            raise RuntimeError("摘要请求前所有权/状态已变化")
                        text = _render_for_summary(chunk, _previous_summary)
                        reply = _coerce_reply(self.model.chat(_summary_request(text), []))
                        _event(self.con, "model.call", rid, {
                            "kind": "summarize", "n_messages": len(chunk),
                            "usage_known": bool(reply.usage), "usage": reply.usage or {},
                            "prompt_tokens": (reply.usage or {}).get("prompt_tokens"),
                            "completion_tokens": (reply.usage or {}).get("completion_tokens"),
                            "cached_tokens": ((reply.usage or {}).get("prompt_tokens_details")
                                              or {}).get("cached_tokens"),
                        })
                        return _summary_reply(reply)

                    def _summarize_prefix(chunk):
                        if self.summarizer:
                            return self.summarizer(chunk)
                        text = _render_turn_prefix(chunk)
                        reply = _coerce_reply(self.model.chat(_summary_request(text), []))
                        _event(self.con, "model.call", rid, {
                            "kind": "summarize", "split_prefix": True,
                            "n_messages": len(chunk),
                            "usage_known": bool(reply.usage), "usage": reply.usage or {},
                            "prompt_tokens": (reply.usage or {}).get("prompt_tokens"),
                            "completion_tokens": (reply.usage or {}).get("completion_tokens"),
                            "cached_tokens": ((reply.usage or {}).get("prompt_tokens_details")
                                              or {}).get("cached_tokens"),
                        })
                        return _summary_reply(reply)

                    try:
                        new_history, _info = compact_history(
                            history, _summarize, self.limits.keep_recent_tokens,
                            chunk_tokens=self.limits.summarize_chunk_tokens,
                            max_calls=self.limits.summarize_max_calls, single_call=True,
                            previous_summary=previous_summary,
                            summarize_prefix=_summarize_prefix)
                        if not _info.get("compacted"):        # 切不出东西 ⇒ 不动历史
                            raise RuntimeError("没有可压缩的完整协议组(keep_recent 过大)")
                    except Exception as e:  # noqa: BLE001
                        # 失败:历史原样保留 + 明确事件 + 冷却(不无限重试、不重复付费)
                        _event(self.con, "run.compact_failed", rid,
                               {"tokens": before, "error": str(e)[:300]})
                        self._compact_cooldown_until = run_calls + self.limits.compact_retry_cooldown_calls
                    else:
                        summary = new_history[0].get("content", "")
                        if SUMMARY_HEADER in summary:
                            summary = summary.split(SUMMARY_HEADER, 1)[1].lstrip("\n")
                        recent_count = max(0, len(new_history) - 1)
                        recent = self._session_rows(rid)
                        recent = recent[-recent_count:] if recent_count else []
                        first_kept_id = recent[0]["id"] if recent else None
                        self.domain.add_compaction(rid, summary, first_kept_id, before)
                        history = new_history
                        self._last_compact = {"before_tokens": before,
                                              "after_tokens": estimate_tokens(history), **_info}
                        _event(self.con, "run.compacted", rid, self._last_compact)
                # A queued user message is steering input: deliver it at the
                # next model boundary, after the current tool batch and before
                # the next request.  It must be part of this turn's history.
                steering = self._inject_steering(rid, target, history)
                if steering == "error":
                    state = "paused"
                    _persist()
                    self._checkpoint(rid, step, history)
                    break
                if steering == "ok":
                    _persist()
                # dynamic state is a turn boundary input, not a per-model-call
                # trailer: injecting the full state after every tool result
                # duplicates several KB of context without adding information.
                if not state_injected:
                    # 前缀缓存:system 必须**静态**(规则),动态状态放历史之后的 user 消息;
                    # 否则 derived/进度一变,整段前缀作废(实测命中率 98% → 1%)
                    _sys = self.research.rules_prompt(target)
                    # Only a compact card is injected.  Full hypotheses,
                    # evidence, observations and conversation stay in the
                    # authoritative store and are fetched on demand.
                    _state = self.research.state_card(
                        target, rid, self._prepare_state.get("context_note"))
                    # A failed session write must not consume a model call.
                    next_call = run_calls + 1
                    tail = history[-1] if history else None
                    already = bool(tail and tail.get("role") == "user"
                                   and tail.get("_context_only")
                                   and tail.get("content") == _state)
                    if not already:
                        try:
                            self._conversation_write(
                                rid, "user", _state,
                                client_id=f"context:{rid}:{step}:{next_call}",
                                meta={"context_only": True, "source": "context"})
                        except ConversationPersistError as e:
                            _event(self.con, "context.persist_failed", rid,
                                   {"error": str(e)[:300]})
                            # A context that the model would have seen but the
                            # session did not record makes resume diverge. Fail closed.
                            self._set_paused(rid, f"context_persist_failed: {str(e)[:300]}")
                            state = "paused"
                            _persist()
                            self._checkpoint(rid, step, history)
                            break
                        history.append({"role": "user", "content": _state,
                                        "_context_only": True})
                    state_injected = True
                else:
                    _sys = self.research.rules_prompt(target)
                # 预算判定必须在**计数之前**:被挡住的调用不该记账(时间/次数都一样)
                if self.limits.time_exhausted(run_elapsed + (time.time() - t0)):
                    state = "paused"
                    _persist()
                    self._limit_hit(rid, "max_seconds",
                                    {"elapsed_s": round(run_elapsed + (time.time() - t0), 1),
                                     "budget_s": self.limits.max_seconds})
                    break
                if self.limits.calls_exhausted(run_calls + 1):
                    state = "paused"
                    _persist()
                    self._limit_hit(rid, "max_model_calls",
                                    {"calls": run_calls, "limit": self.limits.max_model_calls})
                    break
                run_calls += 1
                if not self._reserve_call(rid, run_calls):
                    run_calls -= 1
                    state = "paused"
                    _persist()
                    break
                # Reserve happened, but the run may have been taken over or
                # cancelled; never issue the model request in that case.
                if not self.is_running_owner(rid) or (self.stop_event is not None
                                                      and self.stop_event.is_set()):
                    state = "paused"
                    _persist()
                    break
                msgs = [{"role": "system", "content": _sys}] + history
                _ctx = _context_stats(_sys, history)
                try:
                    reply = self._model_call(rid, msgs, self.registry.describe_model(), run_calls)
                except StreamCancelled as exc:
                    r = getattr(exc, "reply", None)
                    if r is not None and (r.content or r.reasoning or r.tool_calls):
                        history.append(r.assistant_message())
                    _event(self.con, "model.cancelled", rid, {"calls": run_calls})
                    state = "paused"
                    _persist()
                    self._checkpoint(rid, step, history)
                    break
                except StreamError as e:
                    r = getattr(e, "reply", None)
                    if r is not None and (r.content or r.reasoning or r.tool_calls):
                        history.append(r.assistant_message())
                    _event(self.con, "model.error", rid,
                           {"error": str(e)[:300], "phase": "stream", "calls": run_calls})
                    self._set_paused(rid, f"model_error: {str(e)[:300]}")
                    state = "paused"
                    _persist()
                    self._checkpoint(rid, step, history)
                    break
                except ConversationPersistError as e:
                    with contextlib.suppress(Exception):
                        _event(self.con, "conversation.error", rid,
                               {"error": str(e)[:300], "phase": "assistant"})
                    # The assistant was not written to the authoritative session.
                    # Do not invent it in memory: resume would then differ.
                    self._set_paused(rid, f"conversation_error: {str(e)[:300]}")
                    state = "paused"
                    _persist()
                    self._checkpoint(rid, step, history)
                    break
                if self.stop_event is not None and self.stop_event.is_set():
                    # 非流式 chat 期间到达的取消:返回后立刻停,不执行工具。
                    # Assistant 已持久化 tool_calls，必须补 cancelled 结果。
                    if reply.content or reply.tool_calls or reply.reasoning:
                        history.append(reply.assistant_message())
                    if reply.tool_calls:
                        self._record_cancelled_tool_results(
                            rid, reply.tool_calls, "user_stop: 用户取消", history)
                    self._set_paused(rid, "user_stop: 用户取消")
                    state = "paused"
                    _persist()
                    self._checkpoint(rid, step, history)
                    break
                _finish_l = (reply.finish_reason or "").lower()
                _incomplete = _finish_l in ("length", "content_filter")
                if _incomplete and (_finish_l == "content_filter" or reply.tool_calls
                                     or reply.dropped_tool_calls
                                     or _FINAL_RE.search(reply.content or "")):
                    # 截断响应里的 FINAL/工具调用都不完整:保留部分文本,暂停,不执行工具
                    _event(self.con, "model.incomplete", rid,
                           {"finish_reason": _finish_l,
                            "tool_calls": len(reply.tool_calls),
                            "content_len": len(reply.content or "")})
                    self._set_paused(rid, f"model_incomplete: finish_reason={_finish_l}")
                    state = "paused"
                    _persist()
                    self._checkpoint(rid, step, history)
                    break
                # **每一次**真实调用都记账(含 tool_calls / FINAL / 推理-only / 截断);
                # 服务端没给 usage ⇒ usage_known=false,不猜 token 数
                _u = dict(reply.usage or {})
                if _u.get("prompt_tokens"):
                    self._last_usage = {"prompt_tokens": _u.get("prompt_tokens"),
                                        "cached_tokens": (_u.get("prompt_tokens_details") or {}).get(
                                            "cached_tokens", _u.get("prompt_cache_hit_tokens"))}
                else:
                    # Do not let a previous main call's usage masquerade as
                    # the current context measurement.
                    self._last_usage = {}
                _event(self.con, "model.call", rid, {
                    "step": step, "n_messages": len(msgs),
                    "system_chars": _ctx["system"], "history_chars": _ctx["total"],
                    "tool_chars": _ctx["tool"], "assistant_chars": _ctx["assistant"],
                    "usage_known": bool(_u), "usage": _u,
                    "prompt_tokens": _u.get("prompt_tokens"),
                    "completion_tokens": _u.get("completion_tokens"),
                    "cached_tokens": (_u.get("prompt_tokens_details") or {}).get(
                        "cached_tokens", _u.get("prompt_cache_hit_tokens")),
                    "reasoning_tokens": (_u.get("completion_tokens_details") or {}).get(
                        "reasoning_tokens"),
                    "finish_reason": reply.finish_reason or "",
                    "tool_calls": len(reply.tool_calls),
                    "streamed": bool(self.stream_enabled and callable(
                        getattr(self.model, "stream", None))),
                    "status": ("partial" if _incomplete else "ok"),
                })
                # 推理-only 的回复(content 空但 reasoning 非空)也要按 assistant 记录,
                # 否则历史里只剩一条空白回复,既丢诊断信息也会让 thinking 模式协议不完整
                if reply.content or reply.tool_calls or reply.reasoning:
                    history.append(reply.assistant_message())
                _final_hit = _FINAL_RE.search(reply.content or "")
                if not reply.tool_calls and not _final_hit:
                    # 既没有工具调用也不是 FINAL:对研究没有推进。先记结构化诊断,再决定停不停
                    content = reply.content or ""
                    chash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:12]
                    last_content_hashes.append(chash)
                    _event(self.con, "model.reply", rid, {
                        "tool_calls": 0, "content_len": len(content),
                        "reasoning_len": len(reply.reasoning or ""),
                        "content_hash": chash,
                        "finish_reason": reply.finish_reason or "",
                        "usage": reply.usage or {},
                        "preview": content[:120],
                        # 分类:空 / 推理-only / 截断 / 纯文字
                        "class": ("reasoning_only" if not content and reply.reasoning else
                                  "empty" if not content and not reply.reasoning else
                                  "truncated" if (reply.finish_reason or "").lower() == "length"
                                  else "text_only")})
                    empty_streak += 1
                    repeated = (len(last_content_hashes) >= self.stall_repeats and
                                len(set(last_content_hashes[-self.stall_repeats:])) == 1)
                    if empty_streak >= self.stall_repeats or repeated:
                        reason = "repeated_content" if repeated else "no_tool_no_final"
                        _event(self.con, "run.stalled", rid,
                               {"reason": reason, "streak": empty_streak,
                                "finish_reason": reply.finish_reason or "",
                                "content_hash": chash})
                        state = "paused"
                        with contextlib.suppress(Exception):
                            _persist()
                        with contextlib.suppress(Exception):
                            self._checkpoint(rid, step, history)   # 诊断状态必须落库
                        break
                elif reply.tool_calls:
                    empty_streak = 0
                if _final_hit:
                    empty_streak = 0
                    final = reply.content.strip()
                    stopped_by_final = True
                    if reply.tool_calls:
                        self._record_cancelled_tool_results(
                            rid, reply.tool_calls,
                            "FINAL reached; tool calls were not executed", history)
                    step += 1
                    # 研究轮次不能只靠模型说 FINAL 认定:
                    # 必须本轮真的推进了一条假设(决定性尝试结束 / 假设收口 / 假设被放弃)
                    advanced = self._round_advanced(target, since)
                    if advanced:
                        if not self._update_if_running(rid, "round",
                                                       (rounds + 1, _now())):
                            state = "paused"
                            break
                        rounds += 1
                        _event(self.con, "round.ended", self.domain.target(target).id,
                               {"run_id": rid, "rounds": rounds})
                    else:
                        _event(self.con, "round.marker", rid,
                               {"note": "FINAL 但本轮没有推进任何假设 ⇒ 不计轮"})
                    self._checkpoint(rid, step, history)
                    break
                if reply.tool_calls:
                    tool_timeout = (self.timeout if not self.limits.max_tool_seconds
                                    else min(self.timeout, self.limits.max_tool_seconds))
                    ctx = ToolContext(domain=self.domain, root=self.root, run_id=rid,
                                      target=target, timeout=tool_timeout,
                                      artifacts_dir=os.path.join(self.root, "state", "artifacts", rid),
                                      policy=self.policy, research=self.research,
                                      set_session_target=self.on_session_target,
                                      compact_request=lambda keep, next_step: self._request_compaction(
                                          rid, keep, next_step),
                                      context_usage=lambda: self._context_usage_for_tool(rid))
                    stalled = False
                    calls_this_turn = list(reply.tool_calls)
                    conv_failed = False
                    lost_owner = False
                    for ci, call in enumerate(calls_this_turn):
                        if not self.is_running_owner(rid):
                            # 接管/取消后剩余工具(含 state_* 写操作)一律不执行;
                            # 协议要求每个 tool_call_id 都有结果 ⇒ 逐个补 cancelled。
                            # 仍是 owner 但状态非 running = 外部取消/预算暂停,不是 owner_lost。
                            same_owner = self._owns(rid)
                            for c2 in calls_this_turn[ci:]:
                                err2 = ("run_stopped: 运行已暂停/取消" if same_owner
                                        else "owner_lost: 运行已被其他进程接管")
                                history.append({"role": "tool", "tool_call_id": c2.get("id"),
                                                "name": c2.get("name"),
                                                "content": json.dumps(
                                                    {"status": "cancelled", "error": err2},
                                                    ensure_ascii=False)})
                                try:
                                    self._conversation_write(
                                        rid, "tool", json.dumps(
                                            {"status": "cancelled", "error": err2},
                                            ensure_ascii=False),
                                        status="cancelled", tool_call_id=c2.get("id"),
                                        client_id=f"tool:{rid}:{c2.get('id')}",
                                        meta={"tool": c2.get("name"), "status": "cancelled"})
                                except ConversationPersistError as e:
                                    self._emit_stream({"type": "conversation_error", "run": rid,
                                                       "phase": "tool-owner-lost",
                                                       "error": str(e)[:300]})
                                    conv_failed = True
                                    break
                            if not same_owner:
                                lost_owner = True
                            break
                        sig = call["name"] + "|" + json.dumps(call.get("args") or {}, sort_keys=True,
                                                              ensure_ascii=False)
                        seen[sig] = seen.get(sig, 0) + 1
                        if seen[sig] >= self.stall_repeats:
                            stalled = True
                        run_tools += 1
                        out = self._exec_tool_call(ctx, call, rid)
                        if call.get("name") == "target_create" and out.get("target"):
                            # target_create moves this run's target_id; use the
                            # new target name for the rest of the turn.
                            target = self._run_target_name(rid, target)
                        # Build the model-visible projection exactly once.  Calling
                        # _bounded_tool_result twice can create two temp paths for
                        # large structured/observation values.
                        tool_content = _bounded_tool_result(out)
                        history.append({"role": "tool", "tool_call_id": call.get("id"),
                                        "name": call["name"],
                                        # 有界且**始终合法**的 JSON:硬截断会把大字段切在中间,
                                        # 模型收到坏 JSON、并丢掉后面的 tool_call_id/产物路径。
                                        # parts 是图片/文本顺序的内部载体，不把 base64 放进 JSON。
                                        "content": tool_content,
                                        "parts": out.get("parts") or []})
                        try:
                            self._record_tool_conversation(rid, call, out,
                                                           content=tool_content)
                        except ConversationPersistError as e:
                            with contextlib.suppress(Exception):
                                _event(self.con, "conversation.error", rid,
                                       {"error": str(e)[:300], "phase": "tool",
                                        "tool": call.get("name")})
                            self._set_paused(rid, f"conversation_error: {str(e)[:300]}")
                            state = "paused"
                            conv_failed = True
                            break
                        _event(self.con, "tool.called", out.get("tool_call_id"),
                               {"tool": call["name"], "status": out.get("status")})
                        if out.get("needs_user_input"):
                            self.domain.request_user_input(rid, out["needs_user_input"])
                    if conv_failed:
                        self._set_paused(rid, "conversation_error: 排队/取消工具记录写入失败")
                        _persist()
                        self._checkpoint(rid, step, history)
                        break
                    if lost_owner:
                        state = "paused"
                        _event(self.con, "run.owner_lost", rid, {"phase": "tool_batch"})
                        break
                    if stalled:
                        # 反复执行同一操作且没有新状态 ⇒ 执行层停滞(不是研究结论)
                        _event(self.con, "run.stalled", rid, {"repeats": self.stall_repeats})
                        state = "paused"
                        break
                    # target_create atomically inserts a target and moves this
                    # run's target_id.  The rest of this turn must use the new
                    # name for closure/mirror checks and round events.
                    target = self._run_target_name(rid, target)
                    # Pi fuel-gauge self-compaction: compact_now is deferred to
                    # this idle-safe boundary, after every tool result in the
                    # batch is persisted.  Then resume with one user message.
                    if self._compact_pending is not None and not (
                            conv_failed or lost_owner or stalled):
                        request = self._compact_pending
                        self._compact_pending = None
                        instructions = (
                            "Preserve precisely: the current task and its next step; paths "
                            "of files read, changed, or created; test/check status; decisions; "
                            "open problems; and the next action. "
                            f"The agent's priorities: {request['keep']} "
                            f"The agent's next step: {request['next_step']}"
                        )
                        try:
                            info = self.compact_now(rid, instructions=instructions)
                            self._compact_auto_resumes += 1
                            window = int(self.limits.context_tokens or 0)
                            after = int(info.get("after_tokens") or 0)
                            if window and after:
                                self._compact_percent_after = after / window * 100.0
                            resume_text = (
                                "[Automatic resume after self-compaction] The summary above "
                                "holds the state of this work. Continue with: "
                                f"{request['next_step']}. Re-read only the files you need, "
                                "by line range where possible; verify current state instead "
                                "of treating the summary as proof."
                            )
                            self._conversation_write(
                                rid, "user", resume_text,
                                meta={"source": "compact_resume"},
                                client_id=f"compact-resume:{rid}:{self._compact_auto_resumes}")
                            history = self._load_history(rid, self._model_id(), self._model_scope())
                            _event(self.con, "run.compact_resumed", rid,
                                   {"auto_resumes": self._compact_auto_resumes,
                                    "next_step": request["next_step"][:300]})
                        except Exception as e:  # noqa: BLE001
                            _event(self.con, "run.compact_resume_failed", rid,
                                   {"error": str(e)[:300]})
                step += 1
                _persist()
                self._checkpoint(rid, step, history)
                if self._pending_pause:
                    reason, detail = self._pending_pause
                    self._pending_pause = None
                    self._limit_hit(rid, reason, detail)
                    state = "paused"
                    break
                self._heartbeat(rid)

        except BaseException as e:
            with contextlib.suppress(Exception):
                _persist()          # 异常/中断也要把本轮计数落库(否则恢复后预算凭空多出)
            me = os.getpid()
            st = _proc_starttime(me)
            with _db.Tx(self.con) as c:
                # 只有仍是 running 的 owner 才能把异常写成 paused;
                # 已被接管时绝不能覆盖新 owner 的状态。
                cur = c.execute(
                    "UPDATE runs SET state='paused', updated_at=?"
                    " WHERE id=? AND state='running'"
                    " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
                    (_now(), rid, me, st))
                if cur.rowcount:
                    _event(c, "run.paused", rid, {"error": f"{type(e).__name__}: {e}"})
            self._checkpoint(rid, step, history)
            raise

        if not stopped_by_final:
            state = "paused"          # 预算耗尽 = 暂停,不是完成
        else:
            state = "finished"
        try:
            with contextlib.suppress(Exception):
                _persist()          # 收尾时落一次计数(FINAL/暂停/异常路径都覆盖)
            return self._finish_state(rid, target, state, step, rounds, final, history)
        finally:
            self._watchdog_stop()

    def _run_target_name(self, rid: str, fallback: str) -> str:
        """Resolve the current target name from the run (renames keep the id)."""
        row = self.con.execute(
            "SELECT t.name FROM runs r JOIN targets t ON t.id=r.target_id WHERE r.id=?",
            (rid,)).fetchone()
        return row["name"] if row else fallback

    def _finish_state(self, rid: str, target: str, state: str, step: int, rounds: int,
                      final: str, history: list[dict]) -> RunResult:
        # 注意:MCP 桥**不在这里关** —— `advance()` 一轮结束后还要继续下一轮,
        # 关掉会让第 2 轮起所有 MCP 工具失效(实测过)。真正关闭在 `Runner.close()`。
        if not self._owns(rid):
            return RunResult(rid, "paused", step, rounds, final, False, [],
                             "owner_lost", history)
        blockers = self.domain.closure_blockers(target)
        if self.research.mirror_due(target):
            blockers.append(f"三镜刷新超期({self.research.rounds_since_mirror(target)} 轮未做,"
                            f"下限 {self.research.mirror_every})")
        handback = self._handback(rid, state, blockers)
        # 取消发生在 model 调用/锁竞争窗口时,_set_paused 可能失败;
        # 暂停态必须保留明确的 user_stop 原因,不能落成 None。
        if (state == "paused" and handback is None and self.stop_event is not None
                and self.stop_event.is_set()):
            handback = "user_stop: 用户取消"
        ready = not blockers
        me = os.getpid()
        st = _proc_starttime(me)
        with _db.Tx(self.con) as c:
            # CAS:仍 running 且仍是 owner 才写。取消/看护可能已把它暂停(我们仍满足 _owns),
            # 迟到的 finished 不能覆盖 paused/handback —— 检查与写入必须是同一条 UPDATE。
            cur = c.execute("UPDATE runs SET state=?, updated_at=?,"
                            " handback_reason=COALESCE(?, handback_reason)"
                            " WHERE id=? AND state='running'"
                            " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
                            (state, _now(), handback, rid, me, st))
            if not cur.rowcount:
                row = c.execute("SELECT state,handback_reason FROM runs WHERE id=?",
                                (rid,)).fetchone()
                return RunResult(rid, row["state"] if row else "paused", step, rounds,
                                 final, False, blockers,
                                 row["handback_reason"] if row else None, history)
            _event(c, "run.finished", rid, {"state": state, "steps": step, "ready": ready,
                                            "handback": handback, "blockers": blockers[:5]})
        return RunResult(rid, state, step, rounds, final, ready, blockers, handback, history)

    def _last_main_usage(self, rid: str) -> dict:
        """Last provider-reported usage from a non-summarize call.

        A fresh resumed Runner has no in-memory usage; replay recent model.call
        events so both the context meter and compaction use the same Pi-like
        provider number instead of the local chars/2.0 estimate.
        """
        if self._last_usage.get("prompt_tokens"):
            return dict(self._last_usage)
        # Pi computes context from the last valid assistant usage, not from a
        # fixed-size event page.  Select the latest main call in SQL so any
        # number of summarize calls cannot hide it; no arbitrary 32-event cap.
        row = self.con.execute(
            "SELECT payload FROM events WHERE kind='model.call' AND ref=?"
            " AND json_valid(payload)"
            " AND COALESCE(json_extract(payload, '$.kind'), '') != 'summarize'"
            " ORDER BY id DESC LIMIT 1", (rid,)).fetchone()
        if row is None:
            return {}
        try:
            payload = json.loads(row["payload"] or "{}")
        except (TypeError, ValueError):
            return {}
        prompt = payload.get("prompt_tokens")
        if isinstance(prompt, int) and prompt > 0:
            usage = {"prompt_tokens": prompt,
                     "cached_tokens": payload.get("cached_tokens")}
            self._last_usage = usage
            return dict(usage)
        # Latest main call has no provider usage: unknown is more honest than
        # falling back to an older main call's number.
        self._last_usage = {}
        return {}

    def _last_main_prompt_tokens(self, rid: str) -> int | None:
        """Last provider-reported prompt usage from a non-summarize call."""
        usage = self._last_main_usage(rid)
        prompt = usage.get("prompt_tokens")
        try:
            return int(prompt) if prompt else None
        except (TypeError, ValueError):
            return None

    def _round_advanced(self, target: str, since: str) -> bool:
        """本轮是否真的推进了研究:决定性尝试结束 / 假设收口 / 假设被放弃。"""
        t = self.domain.target(target)
        n1 = self.con.execute(
            "SELECT COUNT(*) n FROM attempts a JOIN hypotheses h ON h.id=a.hypothesis_id"
            " WHERE h.target_id=? AND a.result IN ('supported','refuted','inconclusive')"
            "   AND a.finished_at IS NOT NULL AND a.finished_at >= ?", (t.id, since)).fetchone()["n"]
        n2 = self.con.execute(
            "SELECT COUNT(*) n FROM hypotheses WHERE target_id=? AND state IN ('concluded','dropped')"
            "   AND updated_at >= ?", (t.id, since)).fetchone()["n"]
        return (n1 + n2) > 0

    def _handback(self, rid: str, state: str, blockers: list[str]) -> str | None:
        """交回控制权的原因。注意区分:
        - `awaiting_user_close`:程序确认"收尾条件已满足",**等用户确认**(不擅自宣布收尾)
        - `closed`:用户已显式确认收尾(main 的确认原则)
        """
        row = self.con.execute(
            "SELECT r.state, r.handback_reason, t.name AS target FROM runs r JOIN targets t"
            " ON t.id=r.target_id WHERE r.id=?", (rid,)).fetchone()
        reason = row["handback_reason"] if row else None
        # watchdog_paused 描述"上一次被谁停下";run 已 finished/resumed 时是过期值
        if reason and reason.startswith("watchdog_paused") and row["state"] != "paused":
            reason = None
        # awaiting_user_close/closed 是"finished 时的结论",不是粘性原因:
        # 一个后来被暂停/继续的 run 不应继续对外报"等用户确认收尾"
        if reason in ("awaiting_user_close", "closed") and row["state"] != "finished":
            reason = None
        if reason:
            return row["handback_reason"]          # needs_user_input 等
        if state != "finished":
            return None          # paused = 只是暂存点,不存在"等用户确认收尾"
        if not blockers:
            if self.domain.is_close_confirmed(row["target"]):
                return "closed"
            return "awaiting_user_close"
        return None

    # ---------------- 资源生命周期(MCP / 看护) ----------------
    def _ensure_mcp(self, rid: str | None = None, *, background: bool = False) -> None:
        """跨轮/恢复时确保 MCP 真活着:被关过(或构造时没开)就重开并记事件。

        `rid` 给出时,把 MCP 子进程的 pgid+starttime 登记进该 run 的 .pgids,
        这样硬崩溃后 claim/recover/看护能按 starttime 核验并清掉孤儿 MCP。
        """
        if not self._mcp_enabled:
            return
        from .mcp_runtime import bridge_from_config

        allowed, why = self._mcp_permitted()
        if not allowed:
            if self.mcp_skipped_reason != why:
                self.mcp_skipped_reason = why
                with contextlib.suppress(Exception):
                    _event(self.con, "mcp.skipped", None, {"reason": why})
            if self.mcp_bridge is not None and not getattr(self.mcp_bridge, "closed", False):
                self.mcp_bridge.close()
            return

        bridge = self.mcp_bridge
        pgids = (os.path.join(self.root, "state", "artifacts", rid, ".pgids")
                 if rid else None)
        if bridge is not None and not getattr(bridge, "closed", False):
            # eager 启动的 bridge 可能在首个 run 之前就已启动；这里补登记
            # 进程组，硬崩溃后 watchdog 才能按 .pgids 回收。
            if pgids:
                bridge.attach_pgids(pgids)
            return
        reopened = bridge is not None
        if bridge is None:
            self.mcp_bridge = bridge_from_config(self.registry, self.root, self._mcp_config_paths,
                                                 timeout=self._mcp_timeout,
                                                 call_timeout=self._mcp_call_timeout,
                                                 pgids_path=pgids)
            bridge = self.mcp_bridge
        elif pgids and not bridge.pgids_path:
            bridge.pgids_path = pgids
        bridge.start(background=background)
        with contextlib.suppress(Exception):
            _event(self.con, "mcp.reopened" if reopened else "mcp.started", None,
                   {"servers": bridge.health().get("servers", {})})

    def _mcp_permitted(self) -> tuple[bool, str]:
        """MCP 能不能启用/启动:**授权**说了算(不是工具名启发式)。"""
        if not self._mcp_enabled:
            return False, "未启用(--no-mcp 或 mcp=False)"
        if isinstance(self.policy, DirectPolicy):
            return True, ""
        az = self.policy.authorization
        if az is None:
            return False, ("没有研究授权 ⇒ 不启动 MCP 服务器"
                           "(用 `fw-core authz grant <target> --roots <dir>` 授权后才启动)")
        if not az.active():
            return False, f"研究授权已失效/被撤销({az.reason or 'expired'})"
        if not getattr(az, "allow_mcp", False):
            return False, "研究授权未允许 MCP(allow_mcp=false)"
        return True, ""

    def mcp_health(self) -> dict | None:
        return self.mcp_bridge.health() if self.mcp_bridge is not None else None

    def close(self) -> None:
        """幂等清理:停看护线程 + 关 MCP(杀进程组)。
        正常结束、取消、异常退出都必须走这里 —— 否则残留子进程与后台线程。
        """
        with contextlib.suppress(Exception):
            self._watchdog_stop()
        if self.mcp_bridge is not None:
            with contextlib.suppress(Exception):
                self.mcp_bridge.close()
        # 特色适配器(如 IDA)的收尾挂在 **Runner 生命周期**上,而不是只在单测里单独调用。
        # 幂等;是否真的停共享资源由适配器自己按配置决定(IDA 默认不停)。
        with contextlib.suppress(Exception):
            from .toolreg import close_feature_adapters

            close_feature_adapters(self.registry)

    # ---------------- 自动继续 ----------------
    def run(self, rid: str, target: str, goal: str | None = None, history: list[dict] | None = None,
            budget: int | None = None) -> RunResult:
        try:
            return self.turn(rid, target, goal, list(history or []), budget)
        finally:
            self.close()

    def advance(self, target: str, goal: str | None = None, *, max_rounds: int | None = None,
                run_id: str | None = None, history: list[dict] | None = None,
                user_client_id: str | None = None) -> RunResult:
        """持续推进,直到:新 finding / 收尾前置全清 / 需要用户输入 / 用完轮次预算。"""
        rid = run_id or self.start(target, goal)
        if run_id:
            self._claim_run(rid)                  # 接管同一 run(并发 resume 防护)
            self._load_authorization(target)      # 新进程接着跑:授权/MCP 必须恢复
            self._ensure_mcp(rid)
        # 续跑已有 run 必须从检查点恢复历史:否则模型只看到新 goal,
        # 之前所有工具结果/结论都"消失"(resume 一直是这么做的,advance 漏了)
        hist = list(history) if history is not None else (
            self._load_history(rid, self._model_id(), self._model_scope()) if run_id else [])
        if run_id and not hist:
            row = self.con.execute("SELECT step,rounds FROM runs WHERE id=?", (rid,)).fetchone()
            if row and row["step"] > 0:
                _event(self.con, "run.history_missing", rid,
                       {"step": row["step"], "reason": "no session history; refusing user-only history"})
                self._set_paused(
                    rid, "history_missing: session history unavailable, refusing empty-history resume")
                return RunResult(rid, "paused", row["step"], row["rounds"], "", False, [],
                                 "history_missing", [])
        if run_id and goal:
            # Same append-only/user-message semantics as resume(), but before
            # the auto-advance loop starts so the first turn sees the command.
            try:
                self._conversation_write(rid, "user", goal, client_id=user_client_id,
                                         meta={"source": "advance"})
            except ConversationPersistError as e:
                self._set_paused(rid, f"conversation_error: {e}")
                raise
        try:
            return self._advance_loop(rid, target, goal, hist, max_rounds)
        finally:
            self.close()

    def _finding_count(self, target: str) -> int:
        t = self.domain.target(target)
        row = self.con.execute(
            "SELECT COUNT(*) n FROM conclusions WHERE target_id=? AND verdict='vulnerable'",
            (t.id,)).fetchone()
        try:
            return int(row["n"] or 0)
        except (TypeError, ValueError):
            return 0

    def _stop_on_finding(self, res: RunResult, before: int, target: str) -> RunResult:
        """A new vulnerable conclusion is a first-class stop reason.

        The old advance loop only stopped on closure/handback; it could continue
        past a finding while unrelated blockers were still open.  Record the
        finding on the result so the UI can distinguish this from a normal
        round finish without adding another handback enum.
        """
        if self._finding_count(target) > before:
            res.finding = True
        return res

    def _advance_loop(self, rid: str, target: str, goal: str | None,
                      hist: list[dict], max_rounds: int | None) -> RunResult:
        finding_before = self._finding_count(target)
        res = self.turn(rid, target, goal, hist)
        res = self._stop_on_finding(res, finding_before, target)
        if res.finding:
            return res
        guard = 0
        idle = 0
        t_start = time.time()
        while max_rounds is None or guard < max_rounds - 1:
            guard += 1
            # 硬保护:与模型行为无关 —— 总时长上限 / 轮次硬上限(只暂停,不写结论)
            if self.limits.time_exhausted(time.time() - t_start):
                self._limit_hit(rid, "max_seconds", {"elapsed_s": round(time.time() - t_start, 1)})
                res.state = "paused"
                return res
            if guard > 200:
                self._limit_hit(rid, "max_rounds_guard", {"rounds": guard})
                res.state = "paused"
                return res
            if res.handback in ("closed", "awaiting_user_close", "needs_user_input", "user_stop"):
                return res
            if res.state == "paused":
                return res                       # 预算是"检查点",不是研究结论
            # 模型说 FINAL 但还有未收口项 ⇒ 带阻塞清单开下一轮(自动继续).
            # The state card was already injected at the start of this advance;
            # auto-continue carries only the blockers for this action.
            rounds_before = res.rounds
            blockers = _blockers_of(res)
            lines = [f"- {b}" for b in blockers[:5]]
            if len(blockers) > 5:
                lines.append(f"- 另有 {len(blockers) - 5} 条，见 research_derived")
            res2 = self.turn(
                rid, target, "继续推进未收口项:\n" + "\n".join(lines),
                res.history, inject_state=False,
            )
            res2 = self._stop_on_finding(res2, finding_before, target)
            if res2.finding:
                return res2
            # 空闲轮:既没推进研究(轮次没涨)也没产生新的执行动作 ⇒ 连续 2 次就停(不是结论)
            idle = idle + 1 if res2.rounds == rounds_before else 0
            res = res2
            if idle >= 2:
                # 空闲轮是"执行层停滞",单独记事件;只在仍 running 时暂停(不覆盖已 paused 的状态)
                _event(self.con, "run.stalled_no_progress", rid, {"idle_rounds": idle})
                self._update_if_running(rid, "stall", (_now(),))
                res.state = "paused"
                return res
        return res

    def resume(self, rid: str, goal: str | None = None, budget: int | None = None,
               user_client_id: str | None = None) -> RunResult:
        row = self.con.execute(
            "SELECT r.*, t.name AS target_name FROM runs r JOIN targets t ON t.id=r.target_id WHERE r.id=?", (rid,)
        ).fetchone()
        if not row:
            raise KeyError(f"run 不存在: {rid}")
        self.domain.require_active_target(row["target_name"])
        history = self._load_history(rid, self._model_id(), self._model_scope())
        self._load_authorization(row["target_name"])   # 新进程:授权/工具配置要重新装配
        self._claim_run(rid)          # 原子接管(活主 ⇒ 拒绝;CAS 防并发双跑)
        if row["step"] > 0 and not history:
            _event(self.con, "run.history_missing", rid,
                   {"step": row["step"], "reason": "no session history; refusing user-only history"})
            self._set_paused(
                rid, "history_missing: session history unavailable, refusing empty-history resume")
            return RunResult(rid, "paused", row["step"], row["rounds"], "", False, [],
                             "history_missing", [])
        if goal:
            # 用户追加指令属于对话历史;client_id 让同一提交重试幂等
            try:
                self._conversation_write(rid, "user", goal, client_id=user_client_id,
                                         meta={"source": "resume"})
            except ConversationPersistError as e:
                self._set_paused(rid, f"conversation_error: {e}")
                raise
        self._ensure_mcp(rid)
        with _db.Tx(self.con) as c:
            _event(c, "run.resumed", rid, {"step": row["step"],
                                           "mcp": bool(self.mcp_bridge and not self.mcp_bridge.closed)})
        try:
            return self.turn(rid, row["target_name"], goal or row["goal"], history, budget)
        finally:
            self.close()

    # ---------------- 失联判定(不误伤活跃运行) ----------------
    def recover_stale(self, *, older_than_s: float = STALE_DEFAULT_S,
                      include_remote: bool = False, remote_factor: float = 6.0) -> list[str]:
        """只收敛**确实失联**的 run:
        - 心跳超过阈值(默认 300s)未更新;或
        - 同主机且记录的 pid 已不存在。
        活跃运行(心跳新鲜且进程活着)一律不动。
        """
        import time

        now = time.time()
        fixed: list[str] = []
        for r in self.con.execute("SELECT id,pid,pid_starttime,host,heartbeat_at FROM runs"
                                  " WHERE state='running'").fetchall():
            hb = r["heartbeat_at"]
            try:
                age = now - _epoch(hb) if hb else float("inf")
            except (TypeError, ValueError):
                age = float("inf")
            same_host = (r["host"] or "") == _host()
            alive: bool | None = None
            if same_host:
                if r["pid"]:
                    alive = _pid_alive(r["pid"])
                    if alive and r["pid_starttime"]:
                        # 进程活着但启动时间对不上 ⇒ pid 被复用,原 run 其实早没了
                        cur = _proc_starttime(r["pid"])
                        if cur is not None and cur != r["pid_starttime"]:
                            alive = False
                    # 进程活着 ⇒ 绝不判失联(可能在跑 fuzz/等模型);进程死了 ⇒ 立即算失联
                    stale = not alive
                else:
                    alive = None
                    stale = age > older_than_s     # 没有 pid 记录(旧库)⇒ 只能靠心跳
            else:
                # 跨主机查不到 pid ⇒ 默认不动;显式开启时才用更长的阈值兜底
                stale = include_remote and age > older_than_s * remote_factor
            if stale and _inflight_tool_grace(self.con, r["id"], now):
                continue        # 长工具在飞 ≠ 失联(与看护同一条判据)
            if not stale:
                continue
            why = ("pid 已消失" if same_host and alive is not True else
                   f"跨主机心跳 {round(age, 1)}s 未更新")
            from .watchdog import Watchdog as _WD
            snap = _WD(self.con, self.root)._pgids_entries(r["id"])   # 扫描时快照
            with _db.Tx(self.con) as c:
                cur = c.execute(
                    "UPDATE runs SET state='paused', handback_reason=?, updated_at=?"
                    " WHERE id=? AND state='running' AND IFNULL(pid,-1)=?"
                    "   AND IFNULL(pid_starttime,-1)=? AND IFNULL(host,'')=?",
                    (f"recovered_stale: {why}", _now(), r["id"],
                     -1 if r["pid"] is None else r["pid"],
                     -1 if r["pid_starttime"] is None else r["pid_starttime"],
                     r["host"] or ""))
                if not cur.rowcount:
                    continue          # 扫描后已被 resume 接管 ⇒ 不能停它
                # 只有**同主机**的孤立工具才杀:pgid 是主机本地概念,跨主机按同名 pgid 杀
                # 可能误伤本机无关进程(.pgids 是别的主机写的)
                # 只有"同主机 + 记录到 pid 且已死"才杀:pid 为空的旧行没有 owner 证据,
                # .pgids 可能是旧格式/被复用 ⇒ 不杀更安全
                if same_host and r["pid"] and alive is False:
                    with contextlib.suppress(Exception):
                        from .watchdog import Watchdog
                        Watchdog(self.con, self.root)._kill_pgids(r["id"], snap)
                c.execute("UPDATE tool_calls SET status='cancelled',"
                          " note=COALESCE(note,'') || ' [recovered stale]'"
                          " WHERE run_id=? AND status='running'", (r["id"],))
                _event(c, "run.recovered_stale", r["id"],
                       {"age_s": round(age, 1), "pid_alive": alive, "host": r["host"],
                        "pid_starttime": r["pid_starttime"]})
            fixed.append(r["id"])
        return fixed


def _record_tool(ctx, name: str, args: dict, status: str, note: str, call_key: str | None) -> None:
    from .tools import _record as _rec

    _rec(ctx, name, args, status, note=note, call_key=call_key)


def _coerce_reply(r) -> Reply:
    """允许 adapter 直接返回 dict(自定义/旧实现),统一收敛成 Reply。"""
    if isinstance(r, Reply):
        return r
    if isinstance(r, dict):
        calls = []
        for c in r.get("tool_calls") or []:
            fn = c.get("function") or {}
            args = c.get("args")
            if args is None:
                raw = fn.get("arguments")
                if isinstance(raw, str):
                    try:
                        args = json.loads(raw or "{}")
                    except json.JSONDecodeError:
                        args = {"__invalid_json__": raw}
                else:
                    args = raw or {}
            calls.append({"id": c.get("id") or f"call_{len(calls)}",
                          "name": c.get("name") or fn.get("name") or "",
                          "args": args, "args_raw": fn.get("arguments")})
        return Reply(content=r.get("content") or "", tool_calls=calls,
                     reasoning=r.get("reasoning_content") or r.get("reasoning") or "",
                     finish_reason=r.get("finish_reason") or "",
                     usage=r.get("usage") or {})
    raise TypeError(f"adapter 返回了不支持的类型: {type(r)}")


def _event(con, kind: str, ref: str | None, payload: dict | None) -> None:
    con.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                (_now(), kind, ref, json.dumps(payload or {}, ensure_ascii=False)))


def _blockers_of(res) -> list[str]:
    """取本轮阻塞清单(供下一轮的提示文本用)。"""
    return list(getattr(res, "blockers", []) or [])


def _context_stats(system: str, history: list[dict]) -> dict:
    """请求体积分解(字符,不是 token):只用于定位"谁在撑大上下文"。"""
    tool = assistant = other = 0
    for m in history:
        c = m.get("content")
        n = len(c) if isinstance(c, str) else 0
        if m.get("role") == "tool":
            tool += n
        elif m.get("role") == "assistant":
            assistant += n + len(m.get("reasoning_content") or "")
        else:
            other += n
    return {"system": len(system), "tool": tool, "assistant": assistant,
            "other": other, "total": tool + assistant + other}


_SUMMARY_SYSTEM = """You are a context summarization assistant. Your task is to read a conversation between a user and an AI assistant, then produce a structured summary following the exact format specified.

Do NOT continue the conversation. Do NOT respond to any questions in the conversation. ONLY output the structured summary."""

_SUMMARY_PROMPT = """The messages above are a conversation to summarize. Create a structured context checkpoint summary that another LLM will use to continue the work.

Use this EXACT format:

## Goal
[What is the user trying to accomplish? Can be multiple items if the session covers different tasks.]

## Constraints & Preferences
- [Any constraints, preferences, or requirements mentioned by user]
- [Or "(none)" if none were mentioned]

## Progress
### Done
- [x] [Completed tasks/changes]

### In Progress
- [ ] [Current work]

### Blocked
- [Issues preventing progress, if any]

## Key Decisions
- **[Decision]**: [Brief rationale]

## Next Steps
1. [Ordered list of what should happen next]

## Critical Context
- [Any data, examples, or references needed to continue]
- [Or "(none)" if not applicable]

Keep each section concise. Preserve exact file paths, function names, and error messages."""

_UPDATE_SUMMARY_PROMPT = """The messages above are NEW conversation messages to incorporate into the existing summary provided in <previous-summary> tags.

Update the existing structured summary with new information. RULES:
- PRESERVE all existing information from the previous summary
- ADD new progress, decisions, and context from the new messages
- UPDATE the Progress section: move items from "In Progress" to "Done" when completed
- UPDATE "Next Steps" based on what was accomplished
- PRESERVE exact file paths, function names, and error messages
- If something is no longer relevant, you may remove it

Use this EXACT format:

## Goal
[Preserve existing goals, add new ones if the task expanded]

## Constraints & Preferences
- [Preserve existing, add new ones discovered]

## Progress
### Done
- [x] [Include previously done items AND newly completed items]

### In Progress
- [ ] [Current work - update based on progress]

### Blocked
- [Current blockers - remove if resolved]

## Key Decisions
- **[Decision]**: [Brief rationale] (preserve all previous, add new)

## Next Steps
1. [Update based on current state]

## Critical Context
- [Preserve important context, add new if needed]

Keep each section concise. Preserve exact file paths, function names, and error messages."""


def _summary_request(text: str) -> list[dict]:
    """Pi's standalone summary context: system prompt + one user message."""
    return [{"role": "system", "content": _SUMMARY_SYSTEM},
            {"role": "user", "content": text}]


def _valid_summary(text: str | None) -> str:
    """Reject a summary that is actually a continued turn/tool call."""
    s = (text or "").strip()
    if not s:
        raise SummarizationIncomplete("summary is empty")
    low = s.lower()
    if "dsml" in low or "<|tool" in low or "<｜tool" in low:
        raise SummarizationIncomplete("summary contains tool-call markup")
    return s


def _summary_reply(reply: Reply) -> str:
    """Match Pi's summarization failure checks before persisting anything."""
    if reply.tool_calls:
        raise SummarizationIncomplete("summarization attempted to call a tool")
    if reply.finish_reason in ("length", "error"):
        raise SummarizationIncomplete(
            f"summarization failed: finish_reason={reply.finish_reason}")
    return _valid_summary(reply.content)


def _serialize_conversation(messages: list[dict]) -> str:
    """Pi ``serializeConversation``: omit thinking and cap tool results at 2000."""
    parts: list[str] = []
    for m in messages:
        role = m.get("role")
        c = m.get("content")
        if role == "assistant" and isinstance(c, str) and c.startswith(SUMMARY_HEADER):
            # Pi passes the previous summary separately, not as a normal turn.
            continue
        c = c if isinstance(c, str) else ""
        if role == "user":
            if c:
                parts.append(f"[User]: {c}")
        elif role == "assistant":
            if c:
                parts.append(f"[Assistant]: {c}")
            calls = []
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function") or {}
                name = fn.get("name") or tc.get("name") or "tool"
                args = fn.get("arguments") or tc.get("args") or ""
                if isinstance(args, str):
                    try:
                        parsed = json.loads(args)
                    except (TypeError, ValueError):
                        parsed = None
                    if isinstance(parsed, dict):
                        args = ", ".join(
                            f"{k}={json.dumps(v, ensure_ascii=False)}"
                            for k, v in parsed.items())
                elif isinstance(args, dict):
                    args = ", ".join(
                        f"{k}={json.dumps(v, ensure_ascii=False)}"
                        for k, v in args.items())
                calls.append(f"{name}({args})")
            if calls:
                parts.append(f"[Assistant tool calls]: {'; '.join(calls)}")
        elif role == "tool":
            if len(c) > 2000:
                omitted = len(c) - 2000
                c = f"{c[:2000]}\n\n[... {omitted} more characters truncated]"
            if c:
                parts.append(f"[Tool result]: {c}")
    return "\n\n".join(parts)


def _render_for_summary(messages: list[dict], previous_summary: str = "",
                         limit: int = 2_000_000) -> str:
    """Standalone Pi summary prompt for the history being compacted."""
    conversation = _serialize_conversation(messages)
    previous = (f"<previous-summary>\n{previous_summary}\n</previous-summary>\n\n"
                if previous_summary else "")
    base = _UPDATE_SUMMARY_PROMPT if previous_summary else _SUMMARY_PROMPT
    prompt = f"<conversation>\n{conversation}\n</conversation>\n\n{previous}{base}"
    return prompt[:limit]


_TURN_PREFIX_PROMPT = """The messages above are earlier context from an ongoing conversation. Later messages are stored separately and do not need to be reconstructed.

Create a concise checkpoint of the user's request and the progress shown above. This checkpoint will be placed before the later messages so the conversation can continue with the necessary context.

## Original Request
[What did the user ask for?]

## Progress So Far
- [Key decisions and work completed in these messages]

## Context Needed to Continue
- [Information from these messages needed to understand the later work]

Only summarize information explicitly present above. Do not infer or recreate later messages."""


def _render_turn_prefix(messages: list[dict], instructions: str = "",
                         limit: int = 2_000_000) -> str:
    """Pi's separate prompt for the early part of a split user-message span."""
    prompt = (f"# Conversation\n{_serialize_conversation(messages)}\n\n"
              f"# Instructions\n{_TURN_PREFIX_PROMPT}")
    if instructions.strip():
        prompt += "\n\nAdditional focus: " + instructions.strip()[:4000]
    return prompt[:limit]

def _bounded_tool_result(out: dict, limit: int | None = None) -> str:
    """工具结果给模型看的版本。

    默认不再用 16k/8k 之类的任意上限：工具输出本身已经按 Pi 的
    `2000 行 / 50 KiB 尾部` 截断，并带 `full_output_path`。这里只做
    JSON 合法性与元数据保留；`limit` 仅保留给调用方显式做硬预算。
    """
    out = dict(out)
    # parts 是模型适配层用的内部部件（图片路径/哈希）；JSON 里只保留 images 摘要。
    out.pop("parts", None)
    if out.get("tool_call_id"):
        # 数据库 TC-… 与协议 tool_call_id(模型给的 call_…)不是一回事:
        # 内容里只保留 db_tool_call_id,协议 id 留在消息层,避免模型拿错
        out["db_tool_call_id"] = out.pop("tool_call_id")
    # 原始 stdout/stderr 是解析用前缀，不是模型输出；有 tail 时不再重复给模型。
    for key in ("stdout", "stderr"):
        value = out.get(key)
        if not isinstance(value, str):
            continue
        if out.get(f"{key}_tail"):
            out.pop(key, None)
        elif len(value.encode("utf-8")) > DEFAULT_MAX_BYTES:
            out[f"{key}_omitted_bytes"] = len(value.encode("utf-8"))
            out.pop(key, None)
    # 模型可见的结构化对象也必须有界：Pi 把完整 structuredContent 留给脚本，
    # 模型看的是转换后的 content。fwcore 没有 codemode，因此把完整 JSON 落临时
    # 文件、模型只保留摘要；否则 20 KiB 文本上限会被结构化对象绕过。
    for key in ("structured", "observation"):
        if out.get(key) is None:
            continue
        try:
            bounded_value, full_path = limit_structured_for_model(
                out[key], max_bytes=DEFAULT_MAX_BYTES,
                prefix=f"fwcore-{key}")
        except OSError as e:
            bounded_value, full_path = {
                "_truncated": True,
                "error": f"完整 {key} 保存失败: {type(e).__name__}: {e}",
            }, None
        out[key] = bounded_value
        if full_path:
            out[f"{key}_full_output_path"] = full_path
    # 所有模型可见文本字段统一走 Pi 尾部边界（适配器也不能绕过）。
    for key in ("stdout_tail", "stderr_tail", "text", "output_tail"):
        value = out.get(key)
        if not isinstance(value, str) or len(value.encode("utf-8")) <= DEFAULT_MAX_BYTES:
            continue
        bounded = truncate_tail(value, max_lines=DEFAULT_MAX_LINES,
                                max_bytes=DEFAULT_MAX_BYTES)
        out[key] = bounded["content"]
        out[f"{key}_truncated"] = True
        out[f"{key}_truncated_by"] = bounded["truncated_by"]
    full = json.dumps(out, ensure_ascii=False, default=str)
    if limit is None or len(full) <= limit:
        return full
    keep = ("status", "db_tool_call_id", "result_path", "stdout_path",
            "stderr_path", "result_sha256", "sha256", "exit_code", "error", "note",
            "path", "bytes", "truncated", "duration_ms", "replayed", "next",
            "needs_user_input", "provider", "capability", "full_output_path",
            "structured_full_output_path", "observation_full_output_path",
            "stdout_full_output_path", "stderr_full_output_path",
            "stdout_truncated", "stderr_truncated",
            "stdout_truncated_by", "stderr_truncated_by")
    base = {k: out[k] for k in keep if k in out}
    big = {k: v for k, v in out.items()
           if isinstance(v, str) and len(v) > 200 and k not in base}
    base["_truncated"] = True
    base["_omitted"] = {k: len(v) for k, v in big.items()}
    budget = limit - len(json.dumps(base, ensure_ascii=False, default=str)) - 100
    excerpt = {}
    for k, v in big.items():
        if budget <= 0:
            break
        take = min(len(v), max(0, budget))
        excerpt[k] = v[:take]
        budget -= take
    if excerpt:
        base["excerpt"] = excerpt
    s = json.dumps(base, ensure_ascii=False, default=str)
    if len(s) <= limit:
        return s
    base.pop("excerpt", None)
    return json.dumps(base, ensure_ascii=False, default=str)

def _sanitize_history(history: list[dict], lookup=None) -> list[dict]:
    """旧版硬截断留下的工具回复可能是非法 JSON;加载时包成合法结构,原文节选保留。

    `lookup(protocol_id) -> {"id","stdout_path","result_path"}` 用**真实** call_key 记录
    补上 DB id 与产物路径(可追溯),不是编造;查不到就只给说明。
    """
    for m in history:
        if m.get("role") != "tool":
            continue
        c = m.get("content")
        if not isinstance(c, str):
            continue
        try:
            data = json.loads(c)
        except (ValueError, TypeError):
            pass
        else:
            # 旧检查点可能仍带着升级前的超大 structured/text；恢复时重新过一遍
            # 模型可见边界，避免只修新产生的消息、旧历史继续撑爆上下文。
            if (isinstance(data, dict)
                    and len(c.encode("utf-8")) > DEFAULT_MAX_BYTES):
                m["content"] = _bounded_tool_result(data)
            continue
        rec = {}
        if lookup:
            try:
                rec = lookup(m.get("tool_call_id")) or {}
            except Exception:  # noqa: BLE001
                rec = {}
        payload = {"status": "unknown", "history_corrupt": True,
                   "note": "历史工具回复是旧版硬截断产物(非法 JSON);原始内容需用工具重新读取",
                   "excerpt": c[:1500]}
        if rec.get("id"):
            payload["db_tool_call_id"] = rec["id"]
        if rec.get("stdout_path"):
            payload["stdout_path"] = rec["stdout_path"]
        if rec.get("result_path"):
            payload["result_path"] = rec["result_path"]
        m["content"] = json.dumps(payload, ensure_ascii=False)
    return history


CHECKPOINT_TOOL_TAIL_CHARS = 2000


def _shrink_history(history: list[dict], keep_full: int = 20, artifact_of=None) -> list[dict]:
    """把历史里较老的 tool 输出截断(保留结构),避免检查点持有全量原文。

    这是**磁盘保护**(不是 live tool result 裁剪):保留 Pi compaction 的 2000
    字符边界;截断时保留 tool_call_id 与原产物路径,恢复后仍能回到全文继续验证。
    `artifact_of(tool_call_id)` 返回可追溯路径。
    """
    tool_idx = [i for i, m in enumerate(history) if m.get("role") == "tool"]
    # keep_full=0 表示"全压"(--0 会变成 [:0] 什么都不压,是个真 bug)
    cutoff = set(tool_idx[:-keep_full] if keep_full > 0 else tool_idx)
    out = []
    for i, m in enumerate(history):
        if i in cutoff:
            c = m.get("content")
            if isinstance(c, str) and len(c) > CHECKPOINT_TOOL_TAIL_CHARS:
                note = f"…(检查点截断,原 {len(c)} 字符"
                if artifact_of:
                    p = artifact_of(m.get("tool_call_id"))
                    if p:
                        note += f",全文: {p}"
                out.append({**m,
                            "content": c[:CHECKPOINT_TOOL_TAIL_CHARS] + note + ")"})
                continue
        out.append(m)
    return out


def _db_bytes(con) -> int:
    try:
        p = con.execute("PRAGMA database_list").fetchone()[2]
        total = 0
        for suffix in ("", "-wal", "-shm"):
            fp = p + suffix
            if os.path.exists(fp):
                total += os.path.getsize(fp)
        return total
    except Exception:  # noqa: BLE001
        return 0


def _host() -> str:
    return socket.gethostname()


def _proc_starttime(pid: int) -> int | None:
    """读 /proc/<pid>/stat 第 22 字段(启动时刻),用于识别 pid 复用。"""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            tail = f.read().rsplit(")", 1)[-1].split()
        return int(tail[19])
    except (OSError, IndexError, ValueError):
        return None


def _inflight_tool_grace(con, rid: str, now: float, grace_s: float = 900.0) -> bool:
    """该 run 是否有"仍在合理时限内"的在飞工具调用(有 ⇒ 不算失联)。"""
    row = con.execute("SELECT created_at FROM tool_calls WHERE run_id=? AND status='running'"
                      " ORDER BY created_at DESC LIMIT 1", (rid,)).fetchone()
    if not row:
        return False
    started = _epoch(row["created_at"])
    return bool(started and (now - started) < grace_s)


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        pid_i = int(pid)
    except (TypeError, ValueError):
        return False
    try:
        os.kill(pid_i, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _epoch(ts: str) -> float:
    import datetime as _d

    try:
        return _d.datetime.fromisoformat(ts).timestamp()
    except Exception:  # noqa: BLE001
        return 0.0


def _safe_json(s: str | None):
    try:
        return json.loads(s) if s else None
    except (TypeError, ValueError):
        return None


def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.UTC).isoformat(timespec="microseconds")  # 与 domain 一致
