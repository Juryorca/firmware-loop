"""运行时:模型↔工具循环 + 心跳/失联判定 + 幂等工具调用 + 自动继续。

语义分层(这轮修正的重点)
- **研究轮次(round)**:一条假设被推到判定性结果为一轮;`runs.rounds` 与 `round.ended` 事件记录。
- **执行检查点(checkpoint)**:每次模型/工具交互后落盘,含完整消息历史。
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
from .compact import compact_history, estimate_tokens, should_compact
from .compact import find_cut as _find_cut
from .contextinfo import build_status
from .domain import ConversationPersistError, Domain, new_id
from .limits import Limits
from .models import Adapter, ModelHTTPError, Reply, ScriptedAdapter, assistant_message  # noqa: F401
from .output import DEFAULT_MAX_BYTES
from .policy import Policy
from .streaming import StreamCancelled, StreamControl, StreamError, as_event
from .research import Research
from .toolkit import register_repo_tools
from .tools import Registry, ToolContext, default_registry

# FINAL 可能出现在多段文字里的任意**行首**(模型常写"本轮已收口…\nFINAL: …")
_FINAL_RE = __import__("re").compile(r"(?m)^\s*FINAL:")

# 兼容旧名(测试与脚本可能仍在用)
ScriptedModel = ScriptedAdapter

HANDOVER = ("closed", "needs_user_input", "user_stop")
STALE_DEFAULT_S = 300.0


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

    # 兼容 dict 访问(旧测试/CLI 用 res["state"] 等)
    def as_dict(self) -> dict:
        return {"run_id": self.run_id, "state": self.state, "steps": self.steps, "rounds": self.rounds,
                "final": self.final, "ready": self.ready, "blockers": self.blockers,
                "handback": self.handback, "history": self.history}

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
                 stop_event=None):
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
        self.stream_control: StreamControl | None = None
        self._last_usage: dict = {}
        self._compact_cooldown_until = -1
        self._last_compact: dict | None = None
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
        t = self.domain.target(target)
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
            except Exception as e:                      # 钩子自己坏了也不能毁掉研究
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

    def _update_if_running(self, rid: str, set_sql: str, params: tuple) -> bool:
        """CAS:只有"自己仍是 owner 且 run 仍 running"才写;检查与写入是同一条 UPDATE。

        为什么不能"先 _owns 再写":两步之间存在窗口 —— 取消/看护/新 owner 可能刚好把
        run 暂停或接管,迟到的写会覆盖 paused/handback/计数(已实测踩过)。
        """
        me = os.getpid()
        st = _proc_starttime(me)
        cur = None
        with _db.Tx(self.con) as c:
            cur = c.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # nosec B608
                "UPDATE runs SET " + set_sql +
                " WHERE id=? AND state='running'"
                " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
                (*params, rid, me, st))
        return cur is not None and cur.rowcount > 0

    def _update_if_owner(self, rid: str, set_sql: str, params: tuple, *,
                         states: tuple[str, ...] = ("running", "paused")) -> bool:
        """原子写:只有"仍是 owner 且 state 在允许集合"才更新。

        `_checkpoint/_persist` 的检查与写入必须是同一条 UPDATE,否则
        检查后、事务前仍可被别的进程接管(TOCTOU)。
        """
        me = os.getpid()
        st = _proc_starttime(me)
        marks = ",".join("?" for _ in states)
        cur = None
        with _db.Tx(self.con) as c:
            cur = c.execute(  # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query  # nosec B608
                "UPDATE runs SET " + set_sql +
                f" WHERE id=? AND state IN ({marks})"
                " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
                (*params, rid, *states, me, st))
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

    def _load_history(self, rid: str) -> list[dict]:
        """从最新检查点恢复历史(含旧版坏 JSON 修复);损坏就返回空并记事件。"""
        ck = self.con.execute(
            "SELECT messages FROM checkpoints WHERE run_id=? ORDER BY step DESC LIMIT 1",
            (rid,)).fetchone()
        if not ck:
            return []
        try:
            def _lookup(pid):
                row = self.con.execute(
                    "SELECT id, stdout_path, result_path FROM tool_calls"
                    " WHERE id=? OR call_key=?", (pid, pid)).fetchone()
                return dict(row) if row else None

            return _sanitize_history(json.loads(ck["messages"]), _lookup)
        except (json.JSONDecodeError, TypeError) as e:
            with _db.Tx(self.con) as c:
                _event(c, "run.checkpoint_corrupt", rid, {"error": str(e)[:200]})
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

    def _checkpoint(self, rid: str, step: int, history: list[dict]) -> bool:
        """存检查点。

        防爆(2026-11-04 62GB 事故):
        - **裁剪**:只保留最近 20 条工具输出原文,更早的截断成摘要 —— 恢复仍可用,体积可控;
        - **保留上限**:只留最近 `limits.checkpoints_keep` 条;
        - **库体积硬顶**:超过 `limits.max_db_bytes` 继续删最老的检查点,仍超 ⇒ 暂停 run(不写结论)。
        """
        payload = json.dumps(_shrink_history(history, artifact_of=self._artifact_of),
                             ensure_ascii=False)
        me = os.getpid()
        st = _proc_starttime(me)
        with _db.Tx(self.con) as c:
            # 检查与写入同一条 UPDATE:扫描后接管也不能让旧 owner 写 checkpoint
            cur = c.execute(
                "UPDATE runs SET step=?, updated_at=? WHERE id=?"
                " AND state IN ('running','paused')"
                " AND IFNULL(pid,-1)=? AND IFNULL(pid_starttime,-1)=?",
                (step, _now(), rid, me, st))
            if not cur.rowcount:
                return False
            c.execute("INSERT INTO checkpoints(id,run_id,step,messages,created_at) VALUES(?,?,?,?,?)",
                      (new_id("CK"), rid, step, payload, _now()))
            try:
                keep = max(1, int(self.limits.checkpoints_keep))
            except (TypeError, ValueError):
                keep = 120
            c.execute(
                "DELETE FROM checkpoints WHERE run_id=? AND id NOT IN "
                "(SELECT id FROM checkpoints WHERE run_id=? ORDER BY step DESC LIMIT ?)",
                (rid, rid, keep))
        self._enforce_db_cap(rid)
        return True

    def _enforce_db_cap(self, rid: str) -> None:
        """库体积硬顶:超了就删最老的检查点;仍超 ⇒ 暂停(保留可恢复性,不改变研究事实)。"""
        size = _db_bytes(self.con)
        if size <= self.limits.max_db_bytes:
            return
        with _db.Tx(self.con) as c:
            c.execute("DELETE FROM checkpoints WHERE id IN "
                      "(SELECT id FROM checkpoints ORDER BY created_at ASC LIMIT 200)")
        # WAL 截断必须在事务之外;并发写者持有锁时跳过即可
        with contextlib.suppress(_db.sqlite3.OperationalError):
            self.con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size = _db_bytes(self.con)
        if size > self.limits.max_db_bytes:
            self._pending_pause = ("max_db_bytes", {"db_bytes": size, "limit": self.limits.max_db_bytes})

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
                rid, "state='paused', updated_at=?, budget_note=?",
                (_now(), json.dumps(note, ensure_ascii=False))):
            return
        _event(self.con, "run.limit_hit", rid, {"reason": reason, **(detail or {})})

    # ---------------- 流式模型调用 ----------------
    def _reserve_call(self, rid: str, n: int) -> bool:
        """发起请求前先把计数落库;只有仍是 running 的 owner 才能预留。"""
        return self._update_if_running(
            rid, "model_calls=MAX(model_calls,?), updated_at=?", (n, _now()))

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
                    rid, "state='paused', handback_reason=?, updated_at=?",
                    (reason[:400], _now()))
            except _db.LockedError:
                time.sleep(0.05 * (attempt + 1))
        return False

    def _conversation_write(self, *args, **kwargs):
        """对话写入:短重试只针对锁竞争;最终失败必须显式抛错,不能吞掉。"""
        last: Exception | None = None
        for attempt in range(3):
            try:
                return self.domain.add_conversation(*args, **kwargs)
            except Exception as e:
                last = e
                if isinstance(e, _db.LockedError) or "locked" in str(e).lower():
                    time.sleep(0.05 * (attempt + 1))
                    continue
                break
        raise ConversationPersistError(f"对话写入失败: {last}") from last

    def _persist_counters(self, rid: str, calls: int, tools: int, elapsed: float) -> bool:
        """计数落库也必须带 owner 条件(检查与写入同一条 UPDATE)。"""
        return self._update_if_owner(
            rid,
            "model_calls=MAX(model_calls,?), tool_calls=MAX(tool_calls,?),"
            " elapsed_s=MAX(elapsed_s,?)",
            (calls, tools, elapsed),
            states=("running", "paused"))

    def _persist_assistant(self, rid: str, reply: Reply, *, status: str,
                           call_seq: int, error: str = "") -> None:
        """对话记录只存**面向用户**的内容;reasoning 只记长度,不混进正文。"""
        if not (reply.content or reply.reasoning or reply.tool_calls):
            return
        meta = {
            "model": str(getattr(self.model, "model", "") or getattr(self.model, "name", "")),
            "finish_reason": reply.finish_reason or "",
            "usage": reply.usage or {},
            "tool_calls": [c.get("id") for c in reply.tool_calls],
            "reasoning_len": len(reply.reasoning or ""),
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
                    raise ValueError("tool arguments 不是对象")
            except (json.JSONDecodeError, ValueError):
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
            self._emit_stream({"type": "assistant_done", "run": rid, "call": call_seq,
                               "text": reply.content, "reasoning": reply.reasoning,
                               "finish_reason": reply.finish_reason,
                               "usage": reply.usage or {}, "status": status,
                               "tool_calls": len(reply.tool_calls)})
            self._persist_assistant(rid, reply, status=status, call_seq=call_seq)
            return reply
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
            self._emit_stream({"type": "assistant_done", "run": rid, "call": call_seq,
                               "text": reply.content, "reasoning": reply.reasoning,
                               "finish_reason": reply.finish_reason,
                               "usage": reply.usage or {}, "status": status,
                               "tool_calls": len(reply.tool_calls)})
            self._persist_assistant(rid, reply, status=status, call_seq=call_seq)
            return reply
        except StreamCancelled:
            reply = self._reply_from_stream(content, reasoning, calls, finish, usage)
            self._emit_stream({"type": "assistant_done", "run": rid, "call": call_seq,
                               "text": reply.content, "reasoning": reply.reasoning,
                               "finish_reason": reply.finish_reason, "usage": reply.usage,
                               "status": "cancelled", "tool_calls": len(reply.tool_calls)})
            self._persist_assistant(rid, reply, status="cancelled", call_seq=call_seq)
            raise
        except Exception as e:
            reply = self._reply_from_stream(content, reasoning, calls, finish, usage)
            self._emit_stream({"type": "assistant_error", "run": rid, "call": call_seq,
                               "text": reply.content, "reasoning": reply.reasoning,
                               "error": str(e)[:500]})
            self._persist_assistant(rid, reply, status="error", call_seq=call_seq,
                                    error=str(e))
            if isinstance(e, StreamError):
                raise
            if isinstance(e, ModelHTTPError):
                raise StreamError(str(e)) from e
            raise
        finally:
            self.stream_control = None

    def _record_tool_conversation(self, rid: str, call: dict, out: dict) -> None:
        status = str(out.get("status") or "ok")
        cstatus = {"ok": "ok", "error": "error", "timeout": "error",
                   "cancelled": "cancelled"}.get(status, "error")
        payload = {k: out.get(k) for k in (
                       "status", "error", "note", "exit_code",
                       "stdout_tail", "stderr_tail", "text",
                       "stdout_truncated", "stderr_truncated",
                       "stdout_truncated_by", "stderr_truncated_by",
                       "stdout_total_lines", "stderr_total_lines",
                       "stdout_total_bytes", "stderr_total_bytes",
                       "stdout_full_output_path", "stderr_full_output_path",
                       "full_output_path")
                   if out.get(k) not in (None, "")}
        for key in ("stdout_tail", "stderr_tail", "text"):
            value = payload.get(key)
            if isinstance(value, str):
                # Pi 的 live tool output 是 2000 行/50 KiB；持久化也沿用这个
                # 上限，不再使用独立的 8k 拍脑袋值。
                payload[key] = value[:DEFAULT_MAX_BYTES]
        if out.get("tool_call_id"):
            payload["tool_call_id"] = out["tool_call_id"]
        content = json.dumps(payload, ensure_ascii=False)
        call_id = str(call.get("id") or call.get("name") or "?")
        try:
            self._conversation_write(
                rid, "tool", content, status=cstatus,
                tool_call_id=call.get("id"),
                client_id=f"tool:{rid}:{call_id}",
                meta={"tool": call.get("name"), "status": status})
        except ConversationPersistError as e:
            self._emit_stream({"type": "conversation_error", "run": rid,
                               "phase": "tool", "tool": call.get("name"),
                               "error": str(e)[:300]})
            raise

    # ---------------- 一轮 = 一个模型-工具交互序列 ----------------
    def turn(self, rid: str, target: str, goal: str | None, history: list[dict],
             budget: int | None = None) -> RunResult:
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
        # run 级预算:从数据库读累计值(暂停/恢复/多轮都不重置)。
        # 定义在 try 之外 ⇒ 异常路径也能安全落库(避免 _persist 未绑定)
        row0 = self.con.execute("SELECT model_calls, tool_calls, elapsed_s FROM runs WHERE id=?",
                                (rid,)).fetchone()
        run_calls = int(row0["model_calls"] or 0) if row0 else 0
        run_tools = int(row0["tool_calls"] or 0) if row0 else 0
        run_elapsed = float(row0["elapsed_s"] or 0.0) if row0 else 0.0
        t0 = time.time()

        def _persist() -> None:
            # 检查与写入同一条 UPDATE;并发接管后旧 owner 不能改计数
            self._persist_counters(rid, run_calls, run_tools,
                                   run_elapsed + (time.time() - t0))

        try:
            seen: dict[str, int] = {}
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
                if (run_calls >= self._compact_cooldown_until
                        and should_compact(history, self.limits.context_tokens,
                                           self.limits.output_reserve_tokens)
                        and _find_cut(history, self.limits.keep_recent_tokens) > 0):
                    before = estimate_tokens(history)

                    def _summarize(chunk):
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
                        text = _render_for_summary(chunk)
                        reply = _coerce_reply(self.model.chat(
                            [{"role": "user", "content": text}], []))
                        _event(self.con, "model.call", rid, {
                            "kind": "summarize", "n_messages": len(chunk),
                            "usage_known": bool(reply.usage), "usage": reply.usage or {},
                            "prompt_tokens": (reply.usage or {}).get("prompt_tokens"),
                            "completion_tokens": (reply.usage or {}).get("completion_tokens"),
                            "cached_tokens": ((reply.usage or {}).get("prompt_tokens_details")
                                              or {}).get("cached_tokens"),
                        })
                        return reply.content or ""

                    try:
                        new_history, _info = compact_history(
                            history, _summarize, self.limits.keep_recent_tokens,
                            chunk_tokens=self.limits.summarize_chunk_tokens,
                            max_calls=self.limits.summarize_max_calls)
                        if not _info.get("compacted"):        # 切不出东西 ⇒ 不动历史
                            raise RuntimeError("没有可压缩的完整协议组(keep_recent 过大)")
                    except Exception as e:
                        # 失败:历史原样保留 + 明确事件 + 冷却(不无限重试、不重复付费)
                        _event(self.con, "run.compact_failed", rid,
                               {"tokens": before, "error": str(e)[:300]})
                        self._compact_cooldown_until = run_calls + self.limits.compact_retry_cooldown_calls
                    else:
                        history = new_history
                        self._last_compact = {"before_tokens": before,
                                              "after_tokens": estimate_tokens(history), **_info}
                        _event(self.con, "run.compacted", rid, self._last_compact)
                # 前缀缓存:system 必须**静态**(规则),动态状态放历史之后的 user 消息;
                # 否则 derived/进度一变,整段前缀作废(实测命中率 98% → 1%)
                _sys = self.research.rules_prompt(target)
                _conv = self.domain.conversation(rid, roles=("user",))
                if goal and _conv and (_conv[-1].get("content") or "").strip() == goal.strip():
                    _conv = _conv[:-1]          # 当前目标单独放在「本轮目标」,不重复
                _state = self.research.state_prompt(target, goal, rid, conversation=_conv)
                _status = build_status(
                    model=str(getattr(self.model, "model", "") or getattr(self.model, "name", "")),
                    window=self.limits.context_tokens or None,
                    messages=history,
                    output_reserve=self.limits.output_reserve_tokens,
                    context_tokens=self.limits.context_tokens,
                    last_prompt_tokens=self._last_usage.get("prompt_tokens"),
                    last_cached_tokens=self._last_usage.get("cached_tokens"),
                    system=_sys, state=_state,
                    schema=json.dumps(self.registry.describe(), ensure_ascii=False))
                _state = _state + "\n\n" + _status.render()
                _note = self._prepare_state.get("context_note")
                if _note:
                    _state += "\n\n## 轮前准备(程序注入)\n" + _note
                msgs = [{"role": "system", "content": _sys}] + history \
                    + [{"role": "user", "content": _state}]
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
                # 预留成功后、真正发请求前再确认一次所有权:接管后旧进程不得再发起调用
                if not self.is_running_owner(rid) or (self.stop_event is not None
                                                      and self.stop_event.is_set()):
                    state = "paused"
                    _persist()
                    break
                _ctx = _context_stats(_sys + _state, history)
                try:
                    reply = self._model_call(rid, msgs, self.registry.describe(), run_calls)
                except StreamCancelled:
                    _event(self.con, "model.cancelled", rid, {"calls": run_calls})
                    state = "paused"
                    _persist()
                    break
                except StreamError as e:
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
                    r = getattr(e, "reply", None)
                    if r is not None:
                        if r.content or r.tool_calls or r.reasoning:
                            history.append(r.assistant_message())
                        else:
                            history.append({"role": "assistant", "content": ""})
                        for c in r.tool_calls:
                            history.append({"role": "tool", "tool_call_id": c.get("id"),
                                            "name": c.get("name"),
                                            "content": json.dumps(
                                                {"status": "cancelled",
                                                 "error": "conversation_persist_failed: tool 未执行"},
                                                ensure_ascii=False)})
                    self._set_paused(rid, f"conversation_error: {str(e)[:300]}")
                    state = "paused"
                    _persist()
                    self._checkpoint(rid, step, history)
                    break
                if self.stop_event is not None and self.stop_event.is_set():
                    # 非流式 chat 期间到达的取消:返回后立刻停,不执行工具
                    self._set_paused(rid, "user_stop: 用户取消")
                    state = "paused"
                    _persist()
                    break
                _finish_l = (reply.finish_reason or "").lower()
                _incomplete = _finish_l in ("length", "content_filter")
                if _incomplete and (_finish_l == "content_filter" or reply.tool_calls
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
                else:
                    history.append({"role": "assistant", "content": ""})
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
                    step += 1
                    # 研究轮次不能只靠模型说 FINAL 认定:
                    # 必须本轮真的推进了一条假设(决定性尝试结束 / 假设收口 / 假设被放弃)
                    advanced = self._round_advanced(target, since)
                    if advanced:
                        if not self._update_if_running(rid, "rounds=?, updated_at=?",
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
                    tool_timeout = min(self.timeout, self.limits.max_tool_seconds)
                    ctx = ToolContext(domain=self.domain, root=self.root, run_id=rid, timeout=tool_timeout,
                                      artifacts_dir=os.path.join(self.root, "state", "artifacts", rid),
                                      policy=self.policy, research=self.research)
                    stalled = False
                    cap = max(1, int(self.limits.max_tool_calls_per_turn))
                    calls_this_turn = list(reply.tool_calls)
                    conv_failed = False
                    if len(calls_this_turn) > cap:
                        # 防止一次模型回复刷出上百次调用(演练里出现过 121 次):
                        # 超出的**显式**告知未执行,而不是静默丢弃
                        skipped = calls_this_turn[cap:]
                        calls_this_turn = calls_this_turn[:cap]
                        for c in skipped:
                            err = (f"未执行:单轮工具调用上限 {cap}"
                                   "(把命令合并成更少的调用)")
                            history.append({"role": "tool", "tool_call_id": c.get("id"),
                                            "name": c.get("name"),
                                            "content": json.dumps(
                                                {"status": "cancelled", "error": err},
                                                ensure_ascii=False)})
                            try:
                                self._conversation_write(
                                    rid, "tool", json.dumps(
                                        {"status": "cancelled", "error": err},
                                        ensure_ascii=False),
                                    status="cancelled", tool_call_id=c.get("id"),
                                    client_id=f"tool:{rid}:{c.get('id')}",
                                    meta={"tool": c.get("name"), "status": "cancelled"})
                            except ConversationPersistError as e:
                                self._emit_stream({"type": "conversation_error", "run": rid,
                                                   "phase": "tool-cap", "error": str(e)[:300]})
                                self._set_paused(rid, f"conversation_error: {str(e)[:300]}")
                                state = "paused"
                                conv_failed = True
                                break
                        if conv_failed:
                            _persist()
                            self._checkpoint(rid, step, history)
                            break
                        _event(self.con, "run.tool_cap", rid,
                               {"requested": len(reply.tool_calls), "executed": cap})
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
                        history.append({"role": "tool", "tool_call_id": call.get("id"),
                                        "name": call["name"],
                                        # 有界且**始终合法**的 JSON:硬截断会把大字段切在中间,
                                        # 模型收到坏 JSON、并丢掉后面的 tool_call_id/产物路径
                                        "content": _bounded_tool_result(out)})
                        try:
                            self._record_tool_conversation(rid, call, out)
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
    def _ensure_mcp(self, rid: str | None = None) -> None:
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
        if bridge is not None and not getattr(bridge, "closed", False):
            return
        reopened = bridge is not None
        pgids = (os.path.join(self.root, "state", "artifacts", rid, ".pgids")
                 if rid else None)
        if bridge is None:
            self.mcp_bridge = bridge_from_config(self.registry, self.root, self._mcp_config_paths,
                                                 timeout=self._mcp_timeout,
                                                 call_timeout=self._mcp_call_timeout,
                                                 pgids_path=pgids)
            bridge = self.mcp_bridge
        elif pgids and not bridge.pgids_path:
            bridge.pgids_path = pgids
        bridge.start()
        with contextlib.suppress(Exception):
            _event(self.con, "mcp.reopened" if reopened else "mcp.started", None,
                   {"servers": bridge.health().get("servers", {})})

    def _mcp_permitted(self) -> tuple[bool, str]:
        """MCP 能不能启用/启动:**授权**说了算(不是工具名启发式)。"""
        if not self._mcp_enabled:
            return False, "未启用(--no-mcp 或 mcp=False)"
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
                run_id: str | None = None, history: list[dict] | None = None) -> RunResult:
        """持续推进,直到:收尾前置全清 / 需要用户输入 / 用完轮次预算。"""
        rid = run_id or self.start(target, goal)
        if run_id:
            self._claim_run(rid)                  # 接管同一 run(并发 resume 防护)
            self._load_authorization(target)      # 新进程接着跑:授权/MCP 必须恢复
            self._ensure_mcp(rid)
        # 续跑已有 run 必须从检查点恢复历史:否则模型只看到新 goal,
        # 之前所有工具结果/结论都"消失"(resume 一直是这么做的,advance 漏了)
        hist = list(history) if history is not None else (
            self._load_history(rid) if run_id else [])
        try:
            return self._advance_loop(rid, target, goal, hist, max_rounds)
        finally:
            self.close()

    def _advance_loop(self, rid: str, target: str, goal: str | None,
                      hist: list[dict], max_rounds: int | None) -> RunResult:
        res = self.turn(rid, target, goal, hist)
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
            # 模型说 FINAL 但还有未收口项 ⇒ 带阻塞清单开下一轮(自动继续)
            rounds_before = res.rounds
            res2 = self.turn(rid, target,
                             "继续推进未收口项:\n" + "\n".join(f"- {b}" for b in _blockers_of(res)),
                             res.history)
            # 空闲轮:既没推进研究(轮次没涨)也没产生新的执行动作 ⇒ 连续 2 次就停(不是结论)
            idle = idle + 1 if res2.rounds == rounds_before else 0
            res = res2
            if idle >= 2:
                # 空闲轮是"执行层停滞",单独记事件;只在仍 running 时暂停(不覆盖已 paused 的状态)
                _event(self.con, "run.stalled_no_progress", rid, {"idle_rounds": idle})
                self._update_if_running(rid, "state='paused', updated_at=?", (_now(),))
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
        history = self._load_history(rid)
        self._load_authorization(row["target_name"])   # 新进程:授权/工具配置要重新装配
        self._claim_run(rid)          # 原子接管(活主 ⇒ 拒绝;CAS 防并发双跑)
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
            age = now - _epoch(hb) if hb else float("inf")
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


def _render_for_summary(messages: list[dict], limit: int = 2_000_000) -> str:
    """把要压缩的旧消息渲染成摘要请求(保留事实/地址/路径与 tool_call_id,不编造)。"""
    lines = ["把下面这段固件研究对话压成**结构化摘要**,保留:目标、已确认的技术事实(地址/字段/结论)、"
             "未解决问题、关键产物路径与 db_tool_call_id;不要添加原文没有的结论。只输出摘要。"]
    for m in messages:
        role = m.get("role")
        c = m.get("content") or ""
        if role == "assistant" and m.get("tool_calls"):
            names = [((tc.get("function") or {}).get("name") or tc.get("name")) for tc in m["tool_calls"]]
            lines.append(f"[assistant] 调用工具: {names}")
            if c:
                lines.append(f"[assistant] {c}")
        elif role == "tool":
            lines.append(f"[tool {m.get('tool_call_id')}] {c}")
        else:
            lines.append(f"[{role}] {c}")
    return "\n".join(lines)[:limit]


def _bounded_tool_result(out: dict, limit: int | None = None) -> str:
    """工具结果给模型看的版本。

    默认不再用 16k/8k 之类的任意上限：工具输出本身已经按 Pi 的
    `2000 行 / 50 KiB 尾部` 截断，并带 `full_output_path`。这里只做
    JSON 合法性与元数据保留；`limit` 仅保留给调用方显式做硬预算。
    """
    out = dict(out)
    if out.get("tool_call_id"):
        # 数据库 TC-… 与协议 tool_call_id(模型给的 call_…)不是一回事:
        # 内容里只保留 db_tool_call_id,协议 id 留在消息层,避免模型拿错
        out["db_tool_call_id"] = out.pop("tool_call_id")
    # 原始 stdout/stderr 是解析用前缀，不是模型输出；超过 Pi 上限时只留元数据。
    for key in ("stdout", "stderr"):
        value = out.get(key)
        if isinstance(value, str) and len(value.encode("utf-8")) > DEFAULT_MAX_BYTES:
            out[f"{key}_omitted_bytes"] = len(value.encode("utf-8"))
            out.pop(key, None)
    full = json.dumps(out, ensure_ascii=False, default=str)
    if limit is None or len(full) <= limit:
        return full
    keep = ("status", "db_tool_call_id", "result_path", "stdout_path",
            "stderr_path", "result_sha256", "sha256", "exit_code", "error", "note",
            "path", "bytes", "truncated", "duration_ms", "replayed", "next",
            "needs_user_input", "provider", "capability", "full_output_path",
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
            json.loads(c)
            continue
        except (ValueError, TypeError):
            pass
        rec = {}
        if lookup:
            try:
                rec = lookup(m.get("tool_call_id")) or {}
            except Exception:
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


def _shrink_history(history: list[dict], keep_full: int = 20, artifact_of=None) -> list[dict]:
    """把历史里较老的 tool 输出截断(保留结构),避免检查点持有全量原文。

    这是**磁盘保护**(不是请求裁剪):截断时保留 tool_call_id 与原产物路径,
    恢复后仍能回到全文继续验证。artifact_of(tool_call_id) 返回可追溯路径。
    """
    tool_idx = [i for i, m in enumerate(history) if m.get("role") == "tool"]
    # keep_full=0 表示"全压"(--0 会变成 [:0] 什么都不压,是个真 bug)
    cutoff = set(tool_idx[:-keep_full] if keep_full > 0 else tool_idx)
    out = []
    for i, m in enumerate(history):
        if i in cutoff:
            c = m.get("content")
            if isinstance(c, str) and len(c) > 200:
                note = f"…(检查点截断,原 {len(c)} 字符"
                if artifact_of:
                    p = artifact_of(m.get("tool_call_id"))
                    if p:
                        note += f",全文: {p}"
                out.append({**m, "content": c[:200] + note + ")"})
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
    except Exception:
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
    except Exception:
        return 0.0


def _safe_json(s: str | None):
    try:
        return json.loads(s) if s else None
    except (TypeError, ValueError):
        return None


def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.UTC).isoformat(timespec="microseconds")  # 与 domain 一致
