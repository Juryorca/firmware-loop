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

from . import db as _db
from .domain import Domain, new_id
from .limits import Limits
from .models import Adapter, Reply, ScriptedAdapter, assistant_message  # noqa: F401 (对外兼容)
from .policy import Policy
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
                 mcp_timeout: float = 20.0, mcp_call_timeout: float = 120.0):
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
        # 对齐 Pi 的 prepareNextTurn:每轮模型调用前可做"准备"(压缩上下文/取外部状态),
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

    def start(self, target: str, goal: str | None = None) -> str:
        t = self.domain.target(target)
        self._load_authorization(target)
        # 授权已确定 ⇒ 这时才允许启动 MCP(启动有副作用:子进程/外网/登录态)
        self._ensure_mcp()
        rid = new_id("R")
        now = _now()
        with _db.Tx(self.con) as c:
            c.execute("INSERT INTO runs(id,target_id,state,step,rounds,goal,pid,pid_starttime,host,"
                      "model,heartbeat_at,started_at,updated_at)"
                      " VALUES(?,?,'running',0,0,?,?,?,?,?,?,?,?)",
                      (rid, t.id, goal, os.getpid(), _proc_starttime(os.getpid()), _host(),
                       getattr(self.model, "name", None), now, now, now))
            _event(c, "run.started", rid, {"target": target, "goal": goal})
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
        """结构化进度快照(给外部监控/Pi 适配器用;只读)。"""
        row = self.con.execute(
            "SELECT r.id,r.state,r.step,r.rounds,r.handback_reason,r.goal,r.heartbeat_at,"
            "       r.model_calls,r.tool_calls,r.elapsed_s,"
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
                "recent_events": events}

    def cancel(self, rid: str, reason: str = "用户取消") -> None:
        """请求取消一次运行(等价 Pi 的 AbortSignal)。

        语义:在**安全点**停下(不打断工具内部),停 = paused + handback=user_stop,
        并杀掉该 run 的工具进程组;不写任何研究结论。
        """
        with _db.Tx(self.con) as c:
            c.execute("UPDATE runs SET state='paused', handback_reason=?, updated_at=? WHERE id=?",
                      (f"user_stop: {reason}", _now(), rid))
            _event(c, "run.cancelled", rid, {"reason": reason})
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

    def _heartbeat(self, rid: str) -> None:
        with _db.Tx(self.con) as c:
            c.execute("UPDATE runs SET heartbeat_at=?, pid=?, host=?, updated_at=? WHERE id=?",
                      (_now(), os.getpid(), _host(), _now(), rid))

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

    def _checkpoint(self, rid: str, step: int, history: list[dict]) -> None:
        """存检查点。

        防爆(2026-11-04 62GB 事故):
        - **裁剪**:只保留最近 20 条工具输出原文,更早的截断成摘要 —— 恢复仍可用,体积可控;
        - **保留上限**:只留最近 `limits.checkpoints_keep` 条;
        - **库体积硬顶**:超过 `limits.max_db_bytes` 继续删最老的检查点,仍超 ⇒ 暂停 run(不写结论)。
        """
        payload = json.dumps(_shrink_history(history, artifact_of=self._artifact_of),
                             ensure_ascii=False)
        with _db.Tx(self.con) as c:
            c.execute("INSERT INTO checkpoints(id,run_id,step,messages,created_at) VALUES(?,?,?,?,?)",
                      (new_id("CK"), rid, step, payload, _now()))
            c.execute("UPDATE runs SET step=?, updated_at=? WHERE id=?", (step, _now(), rid))
            try:
                keep = max(1, int(self.limits.checkpoints_keep))
            except (TypeError, ValueError):
                keep = 120
            c.execute(
                "DELETE FROM checkpoints WHERE run_id=? AND id NOT IN "
                "(SELECT id FROM checkpoints WHERE run_id=? ORDER BY step DESC LIMIT ?)",
                (rid, rid, keep))
        self._enforce_db_cap(rid)

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
        with _db.Tx(self.con) as c:
            c.execute("UPDATE runs SET state='paused', updated_at=? WHERE id=?", (_now(), rid))
            _event(c, "run.limit_hit", rid, {"reason": reason, **(detail or {})})

    # ---------------- 一轮 = 一个模型-工具交互序列 ----------------
    def turn(self, rid: str, target: str, goal: str | None, history: list[dict],
             budget: int | None = None) -> RunResult:
        budget = self.max_steps if budget is None else budget      # None = 不限步数
        step = self.con.execute("SELECT step,rounds FROM runs WHERE id=?", (rid,)).fetchone()["step"]
        rounds = self.con.execute("SELECT rounds FROM runs WHERE id=?", (rid,)).fetchone()["rounds"]
        final = ""
        stopped_by_final = False
        state = "running"
        with contextlib.suppress(Exception):
            row = self.con.execute("SELECT handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
            if not (row and (row["handback_reason"] or "").startswith("user_stop")):
                with _db.Tx(self.con) as c:
                    c.execute("UPDATE runs SET state='running', updated_at=? WHERE id=?", (_now(), rid))
        self._watchdog_start()
        self._ensure_mcp()          # 跨轮/恢复:MCP 必须真的可用(被关过就重开)
        self._heartbeat(rid)
        # run 级预算:从数据库读累计值(暂停/恢复/多轮都不重置)。
        # 定义在 try 之外 ⇒ 异常路径也能安全落库(避免 _persist 未绑定)
        row0 = self.con.execute("SELECT model_calls, tool_calls, elapsed_s FROM runs WHERE id=?",
                                (rid,)).fetchone()
        run_calls = int(row0["model_calls"] or 0) if row0 else 0
        run_tools = int(row0["tool_calls"] or 0) if row0 else 0
        run_elapsed = float(row0["elapsed_s"] or 0.0) if row0 else 0.0
        t0 = time.time()

        def _persist() -> None:
            with _db.Tx(self.con) as c:
                c.execute("UPDATE runs SET model_calls=?, tool_calls=?, elapsed_s=? WHERE id=?",
                          (run_calls, run_tools, run_elapsed + (time.time() - t0), rid))

        try:
            seen: dict[str, int] = {}
            empty_streak = 0
            last_content_hashes: list[str] = []
            since = _now()
            while budget is None or budget > 0:
                stopped = self._external_stop(rid)
                if stopped:
                    state = "paused"
                    _event(self.con, "run.stopped_external", rid, {"reason": stopped})
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
                # 前缀缓存:system 必须**静态**(规则),动态状态放历史之后的 user 消息;
                # 否则 derived/进度一变,整段前缀作废(实测命中率 98% → 1%)
                _sys = self.research.rules_prompt(target)
                _state = self.research.state_prompt(target, goal, rid)
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
                _ctx = _context_stats(_sys + _state, history)
                reply = _coerce_reply(self.model.chat(msgs, self.registry.describe()))
                # **每一次**真实调用都记账(含 tool_calls / FINAL / 推理-only / 截断);
                # 服务端没给 usage ⇒ usage_known=false,不猜 token 数
                _u = dict(reply.usage or {})
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
                        rounds += 1
                        _event(self.con, "round.ended", self.domain.target(target).id,
                               {"run_id": rid, "rounds": rounds})
                        with _db.Tx(self.con) as c:
                            c.execute("UPDATE runs SET rounds=? WHERE id=?", (rounds, rid))
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
                    if len(calls_this_turn) > cap:
                        # 防止一次模型回复刷出上百次调用(演练里出现过 121 次):
                        # 超出的**显式**告知未执行,而不是静默丢弃
                        skipped = calls_this_turn[cap:]
                        calls_this_turn = calls_this_turn[:cap]
                        for c in skipped:
                            history.append({"role": "tool", "tool_call_id": c.get("id"),
                                            "name": c.get("name"),
                                            "content": json.dumps(
                                                {"status": "cancelled",
                                                 "error": f"未执行:单轮工具调用上限 {cap}"
                                                          "(把命令合并成更少的调用)"},
                                                ensure_ascii=False)})
                        _event(self.con, "run.tool_cap", rid,
                               {"requested": len(reply.tool_calls), "executed": cap})
                    for call in calls_this_turn:
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
                        _event(self.con, "tool.called", out.get("tool_call_id"),
                               {"tool": call["name"], "status": out.get("status")})
                        if out.get("needs_user_input"):
                            self.domain.request_user_input(rid, out["needs_user_input"])
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
            with _db.Tx(self.con) as c:
                c.execute("UPDATE runs SET state='paused', updated_at=? WHERE id=?", (_now(), rid))
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
        blockers = self.domain.closure_blockers(target)
        if self.research.mirror_due(target):
            blockers.append(f"三镜刷新超期({self.research.rounds_since_mirror(target)} 轮未做,"
                            f"下限 {self.research.mirror_every})")
        handback = self._handback(rid, state, blockers)
        ready = not blockers
        with _db.Tx(self.con) as c:
            c.execute("UPDATE runs SET state=?, updated_at=?, handback_reason=COALESCE(?, handback_reason)"
                      " WHERE id=?", (state, _now(), handback, rid))
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
            "SELECT r.handback_reason, t.name AS target FROM runs r JOIN targets t ON t.id=r.target_id WHERE r.id=?",
            (rid,)).fetchone()
        if row and row["handback_reason"]:
            return row["handback_reason"]          # needs_user_input 等
        if not blockers:
            if self.domain.is_close_confirmed(row["target"]):
                return "closed"
            return "awaiting_user_close"
        return None

    # ---------------- 资源生命周期(MCP / 看护) ----------------
    def _ensure_mcp(self) -> None:
        """跨轮/恢复时确保 MCP 真活着:被关过(或构造时没开)就重开并记事件。"""
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
        if bridge is None:
            self.mcp_bridge = bridge_from_config(self.registry, self.root, self._mcp_config_paths,
                                                 timeout=self._mcp_timeout,
                                                 call_timeout=self._mcp_call_timeout)
            bridge = self.mcp_bridge
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
            self._load_authorization(target)      # 新进程接着跑:授权/MCP 必须恢复
            self._ensure_mcp()
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
                self._limit_hit(rid, "stalled_no_progress", {"idle_rounds": idle})
                res.state = "paused"
                return res
        return res

    def resume(self, rid: str, goal: str | None = None, budget: int | None = None) -> RunResult:
        row = self.con.execute(
            "SELECT r.*, t.name AS target_name FROM runs r JOIN targets t ON t.id=r.target_id WHERE r.id=?", (rid,)
        ).fetchone()
        if not row:
            raise KeyError(f"run 不存在: {rid}")
        history = self._load_history(rid)
        self._load_authorization(row["target_name"])   # 新进程:授权/工具配置要重新装配
        self._ensure_mcp()
        with _db.Tx(self.con) as c:
            c.execute("UPDATE runs SET state='running', updated_at=? WHERE id=?", (_now(), rid))
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
            with _db.Tx(self.con) as c:
                c.execute("UPDATE runs SET state='paused', handback_reason=?, updated_at=? WHERE id=?",
                          (f"recovered_stale: {why}", _now(), r["id"]))
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


def _bounded_tool_result(out: dict, limit: int = 8000) -> str:
    """工具结果给模型看的版本:整体 JSON 合法,关键字段优先保留。

    - `db_tool_call_id`:数据库 TC-…(state_add_evidence 认这个,不是协议 id)
    - 产物路径/哈希/状态/错误/退出码/摘要一律保留;大字段只给节选 + 原长度
    - 完整输出仍在磁盘(`stdout_path`/`result_path`)与数据库
    """
    out = dict(out)
    if out.get("tool_call_id"):
        # 数据库 TC-… 与协议 tool_call_id(模型给的 call_…)不是一回事:
        # 内容里只保留 db_tool_call_id,协议 id 留在消息层,避免模型拿错
        out["db_tool_call_id"] = out.pop("tool_call_id")
    full = json.dumps(out, ensure_ascii=False, default=str)
    if len(full) <= limit:
        return full
    keep = ("status", "db_tool_call_id", "result_path", "stdout_path",
            "stderr_path", "result_sha256", "sha256", "exit_code", "error", "note",
            "path", "bytes", "truncated", "duration_ms", "replayed", "next",
            "needs_user_input", "provider", "capability")
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
    base.pop("excerpt", None)          # 极端情况:只留结构化元数据,保证不越界且合法
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


def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.UTC).isoformat(timespec="seconds")
