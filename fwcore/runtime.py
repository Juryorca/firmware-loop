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
import json
import os
import socket
import time
from dataclasses import dataclass

from . import db as _db
from .domain import Domain, new_id
from .limits import Limits
from .models import Adapter, Reply, ScriptedAdapter, assistant_message  # noqa: F401 (对外兼容)
from .policy import Policy
from .research import Research
from .toolkit import register_repo_tools
from .tools import Registry, ToolContext, default_registry

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
                 prepare: object = None, prepare_timeout: float = 15.0):
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
        self._prepare_state: dict = {}
        self._pending_pause: tuple[str, dict] | None = None
        self.timeout = timeout
        self.policy = policy or Policy()
        self.research = research or Research(root, self.domain)

    # ---------------- 运行生命周期 ----------------
    def start(self, target: str, goal: str | None = None) -> str:
        t = self.domain.target(target)
        rid = new_id("R")
        now = _now()
        with _db.Tx(self.con) as c:
            c.execute("INSERT INTO runs(id,target_id,state,step,rounds,goal,pid,host,heartbeat_at,started_at,updated_at)"
                      " VALUES(?,?,'running',0,0,?,?,?,?,?,?)",
                      (rid, t.id, goal, os.getpid(), _host(), now, now, now))
            _event(c, "run.started", rid, {"target": target, "goal": goal})
        return rid

    def progress(self, rid: str) -> dict:
        """结构化进度快照(给外部监控/Pi 适配器用;只读)。"""
        row = self.con.execute(
            "SELECT r.id,r.state,r.step,r.rounds,r.handback_reason,r.goal,r.heartbeat_at,"
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
                "handback": row["handback_reason"], "goal": row["goal"],
                "heartbeat_at": row["heartbeat_at"],
                "blockers": self.domain.closure_blockers(target),
                "prepare_timeouts": self.prepare_timeouts,
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

    def _run_prepare(self, target: str, rid: str, step: int) -> dict:
        """跑轮前准备钩子(带超时)。超时/异常都只记事件并继续,不改变研究结论。"""
        if not self.prepare:
            return {}
        from concurrent.futures import ThreadPoolExecutor
        from concurrent.futures import TimeoutError as _T

        payload = {"target": target, "run_id": rid, "step": step,
                   "derived": self.domain.derived(target)}
        try:
            with ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(self.prepare, payload)      # type: ignore[misc]
                out = fut.result(timeout=self.prepare_timeout) or {}
        except _T:
            self.prepare_timeouts += 1
            _event(self.con, "run.prepare_timeout", rid, {"timeout_s": self.prepare_timeout})
            return {}
        except Exception as e:                              # 钩子自己坏了也不能毁掉研究
            _event(self.con, "run.prepare_error", rid, {"error": f"{type(e).__name__}: {e}"})
            return {}
        if isinstance(out, dict) and out.get("context_note"):
            self._prepare_state = {"context_note": str(out["context_note"])[:4000]}
        return out if isinstance(out, dict) else {}

    def _exec_tool_call(self, ctx, call: dict, rid: str) -> dict:
        """执行一次工具调用;参数截断/非法 JSON 时**不执行**,显式失败并留痕。"""
        if call.get("args_invalid"):
            err = "tool arguments 截断或非法 JSON ⇒ 未执行(请重发完整参数)"
            _record_tool(ctx, call.get("name") or "?", call.get("args") or {}, "error", err, call.get("id"))
            return {"status": "error", "error": err, "not_executed": True}
        return self.registry.call(ctx, call.get("name") or "", call.get("args") or {},
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

    def _checkpoint(self, rid: str, step: int, history: list[dict]) -> None:
        """存检查点。

        防爆(2026-11-04 62GB 事故):
        - **裁剪**:只保留最近 20 条工具输出原文,更早的截断成摘要 —— 恢复仍可用,体积可控;
        - **保留上限**:只留最近 `limits.checkpoints_keep` 条;
        - **库体积硬顶**:超过 `limits.max_db_bytes` 继续删最老的检查点,仍超 ⇒ 暂停 run(不写结论)。
        """
        payload = json.dumps(_shrink_history(history), ensure_ascii=False)
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
        self._heartbeat(rid)
        try:
            seen: dict[str, int] = {}
            t0 = time.time()
            since = _now()
            model_calls = 0
            while budget is None or budget > 0:
                stopped = self._external_stop(rid)
                if stopped:
                    state = "paused"
                    _event(self.con, "run.stopped_external", rid, {"reason": stopped})
                    break
                if budget is not None:
                    budget -= 1
                if self.limits.time_exhausted(time.time() - t0):
                    state = "paused"
                    self._limit_hit(rid, "max_seconds", {"elapsed_s": round(time.time() - t0, 1)})
                    break
                prep = self._run_prepare(target, rid, step)
                if prep.get("cancel"):
                    self._limit_hit(rid, "prepare_cancel", {"note": str(prep.get("why") or "")[:120]})
                    state = "paused"
                    break
                _sys = self.research.system_prompt(target, goal)
                _note = self._prepare_state.get("context_note")
                if _note:
                    _sys += "\n\n## 轮前准备(程序注入)\n" + _note
                msgs = [{"role": "system", "content": _sys}] + history
                model_calls += 1
                if self.limits.calls_exhausted(model_calls):
                    state = "paused"
                    self._limit_hit(rid, "max_model_calls", {"calls": model_calls,
                                                             "limit": self.limits.max_model_calls})
                    break
                if self.limits.time_exhausted(time.time() - t0):
                    state = "paused"
                    self._limit_hit(rid, "max_seconds", {"elapsed_s": round(time.time() - t0, 1)})
                    break
                reply = _coerce_reply(self.model.chat(msgs, self.registry.describe()))
                history.append(reply.assistant_message() if (reply.content or reply.tool_calls)
                               else {"role": "assistant", "content": ""})
                if reply.content.strip().startswith("FINAL:"):
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
                    for call in reply.tool_calls:
                        sig = call["name"] + "|" + json.dumps(call.get("args") or {}, sort_keys=True,
                                                              ensure_ascii=False)
                        seen[sig] = seen.get(sig, 0) + 1
                        if seen[sig] >= self.stall_repeats:
                            stalled = True
                        out = self._exec_tool_call(ctx, call, rid)
                        history.append({"role": "tool", "tool_call_id": call.get("id"),
                                        "name": call["name"],
                                        "content": json.dumps(out, ensure_ascii=False)[:8000]})
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
                self._checkpoint(rid, step, history)
                if self._pending_pause:
                    reason, detail = self._pending_pause
                    self._pending_pause = None
                    self._limit_hit(rid, reason, detail)
                    state = "paused"
                    break
                self._heartbeat(rid)

        except BaseException as e:
            with _db.Tx(self.con) as c:
                c.execute("UPDATE runs SET state='paused', updated_at=? WHERE id=?", (_now(), rid))
                _event(c, "run.paused", rid, {"error": f"{type(e).__name__}: {e}"})
            self._checkpoint(rid, step, history)
            raise

        if not stopped_by_final:
            state = "paused"          # 预算耗尽 = 暂停,不是完成
        else:
            state = "finished"
        return self._finish_state(rid, target, state, step, rounds, final, history)

    def _finish_state(self, rid: str, target: str, state: str, step: int, rounds: int,
                      final: str, history: list[dict]) -> RunResult:
        blockers = self.domain.closure_blockers(target) + self.research.dual_output_blockers(target)
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

    # ---------------- 自动继续 ----------------
    def run(self, rid: str, target: str, goal: str | None = None, history: list[dict] | None = None,
            budget: int | None = None) -> RunResult:
        return self.turn(rid, target, goal, list(history or []), budget)

    def advance(self, target: str, goal: str | None = None, *, max_rounds: int | None = None,
                run_id: str | None = None, history: list[dict] | None = None) -> RunResult:
        """持续推进,直到:收尾前置全清 / 需要用户输入 / 用完轮次预算。"""
        rid = run_id or self.start(target, goal)
        hist = list(history or [])
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
                             "继续推进未收口项:\n" + "\n".join(f"- {b}" for b in res2_blockers(res)),
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
        ck = self.con.execute(
            "SELECT messages FROM checkpoints WHERE run_id=? ORDER BY step DESC LIMIT 1", (rid,)
        ).fetchone()
        try:
            history = json.loads(ck["messages"]) if ck else []
        except (json.JSONDecodeError, TypeError) as e:      # 检查点损坏 ⇒ 从空历史继续(记录事件)
            history = []
            with _db.Tx(self.con) as c:
                _event(c, "run.checkpoint_corrupt", rid, {"error": str(e)[:200]})
        with _db.Tx(self.con) as c:
            c.execute("UPDATE runs SET state='running', updated_at=? WHERE id=?", (_now(), rid))
            _event(c, "run.resumed", rid, {"step": row["step"]})
        return self.turn(rid, row["target_name"], goal or row["goal"], history, budget)

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
        for r in self.con.execute("SELECT id,pid,host,heartbeat_at FROM runs WHERE state='running'").fetchall():
            hb = r["heartbeat_at"]
            age = now - _epoch(hb) if hb else float("inf")
            same_host = (r["host"] or "") == _host()
            alive: bool | None = None
            if same_host:
                if r["pid"]:
                    alive = _pid_alive(r["pid"])
                    # 进程活着 ⇒ **绝不**判失联(可能在跑 fuzz/等模型);进程死了 ⇒ 立即算失联
                    stale = not alive
                else:
                    alive = None
                    stale = age > older_than_s     # 没有 pid 记录(旧库)⇒ 只能靠心跳
            else:
                # 跨主机无法查 pid ⇒ 默认不动;显式开启时才用更长的阈值兜底
                stale = include_remote and age > older_than_s * remote_factor
            if not stale:
                continue
            with _db.Tx(self.con) as c:
                c.execute("UPDATE runs SET state='paused', updated_at=? WHERE id=?", (_now(), r["id"]))
                _event(c, "run.recovered_stale", r["id"],
                       {"age_s": round(age, 1), "pid_alive": alive, "host": r["host"]})
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
                     reasoning=r.get("reasoning_content") or r.get("reasoning") or "")
    raise TypeError(f"adapter 返回了不支持的类型: {type(r)}")


def _event(con, kind: str, ref: str | None, payload: dict | None) -> None:
    con.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                (_now(), kind, ref, json.dumps(payload or {}, ensure_ascii=False)))


def res2_blockers(res) -> list[str]:
    """取本轮阻塞清单(供下一轮的提示文本用)。"""
    return list(getattr(res, "blockers", []) or [])


def _shrink_history(history: list[dict], keep_full: int = 20) -> list[dict]:
    """把历史里较老的 tool 输出截断(保留结构),避免检查点持有全量原文。"""
    tool_idx = [i for i, m in enumerate(history) if m.get("role") == "tool"]
    cutoff = set(tool_idx[:-keep_full]) if len(tool_idx) > keep_full else set()
    out = []
    for i, m in enumerate(history):
        if i in cutoff:
            c = m.get("content")
            if isinstance(c, str) and len(c) > 200:
                out.append({**m, "content": c[:200] + f"…(截断,原 {len(c)} 字节)"})
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
