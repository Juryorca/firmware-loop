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

import json
import os
import socket
import time
from dataclasses import dataclass
from typing import Any

from . import db as _db
from .domain import Domain, new_id
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
                 stall_repeats: int = 3):
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
        with _db.Tx(self.con) as c:
            c.execute("INSERT INTO checkpoints(id,run_id,step,messages,created_at) VALUES(?,?,?,?,?)",
                      (new_id("CK"), rid, step, json.dumps(history, ensure_ascii=False), _now()))
            c.execute("UPDATE runs SET step=?, updated_at=? WHERE id=?", (step, _now(), rid))

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
            while budget is None or budget > 0:
                if budget is not None:
                    budget -= 1
                if self.max_seconds and (time.time() - t0) > self.max_seconds:
                    state = "paused"
                    _event(self.con, "run.safety_pause", rid, {"reason": "max_seconds"})
                    break
                msgs = [{"role": "system", "content": self.research.system_prompt(target, goal)}] + history
                reply = _coerce_reply(self.model.chat(msgs, self.registry.describe()))
                history.append(reply.assistant_message() if (reply.content or reply.tool_calls)
                               else {"role": "assistant", "content": ""})
                if reply.content.strip().startswith("FINAL:"):
                    final = reply.content.strip()
                    stopped_by_final = True
                    step += 1
                    rounds += 1
                    self._checkpoint(rid, step, history)
                    _event(self.con, "round.ended", self.domain.target(target).id,
                           {"run_id": rid, "rounds": rounds})
                    with _db.Tx(self.con) as c:
                        c.execute("UPDATE runs SET rounds=? WHERE id=?", (rounds, rid))
                    break
                if reply.tool_calls:
                    ctx = ToolContext(domain=self.domain, root=self.root, run_id=rid, timeout=self.timeout,
                                      artifacts_dir=os.path.join(self.root, "state", "artifacts", rid),
                                      policy=self.policy, research=self.research)
                    stalled = False
                    for call in reply.tool_calls:
                        sig = call["name"] + "|" + json.dumps(call.get("args") or {}, sort_keys=True,
                                                              ensure_ascii=False)
                        seen[sig] = seen.get(sig, 0) + 1
                        if seen[sig] >= self.stall_repeats:
                            stalled = True
                        out = self.registry.call(ctx, call["name"], call.get("args") or {},
                                                 call_key=call.get("id"), run_id=rid)
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

    def _handback(self, rid: str, state: str, blockers: list[str]) -> str | None:
        row = self.con.execute("SELECT handback_reason FROM runs WHERE id=?", (rid,)).fetchone()
        if row and row["handback_reason"]:
            return row["handback_reason"]          # 模型主动交回(needs_user_input)
        if not blockers:
            return "closed"                        # 唯一"真正可结束"的原因
        return None                                # 还有活 ⇒ 不该交回,交给 advance() 继续

    # ---------------- 自动继续 ----------------
    def run(self, rid: str, target: str, goal: str | None = None, history: list[dict] | None = None,
            budget: int | None = None) -> RunResult:
        return self.turn(rid, target, goal, list(history or []), budget)

    def advance(self, target: str, goal: str | None = None, *, max_rounds: int = 4,
                run_id: str | None = None, history: list[dict] | None = None) -> RunResult:
        """持续推进,直到:收尾前置全清 / 需要用户输入 / 用完轮次预算。"""
        rid = run_id or self.start(target, goal)
        hist = list(history or [])
        res = self.turn(rid, target, goal, hist)
        for _ in range(max_rounds - 1):
            if res.handback in ("closed", "needs_user_input"):
                return res
            if res.state == "paused":
                return res                       # 预算是"检查点",不是研究结论
            # 模型说 FINAL 但还有未收口项 ⇒ 带阻塞清单开下一轮(自动继续)
            res = self.turn(rid, target, "继续推进未收口项:\n" + "\n".join(f"- {b}" for b in res.blockers),
                            res.history)
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
        history = json.loads(ck["messages"]) if ck else []
        with _db.Tx(self.con) as c:
            c.execute("UPDATE runs SET state='running', updated_at=? WHERE id=?", (_now(), rid))
            _event(c, "run.resumed", rid, {"step": row["step"]})
        return self.turn(rid, row["target_name"], goal or row["goal"], history, budget)

    # ---------------- 失联判定(不误伤活跃运行) ----------------
    def recover_stale(self, *, older_than_s: float = STALE_DEFAULT_S) -> list[str]:
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
            alive = _pid_alive(r["pid"]) if same_host else True   # 别的机器上无法判断 ⇒ 保守:不处理
            stale = age > older_than_s or (same_host and not alive)
            if not stale:
                continue
            with _db.Tx(self.con) as c:
                c.execute("UPDATE runs SET state='paused', updated_at=? WHERE id=?", (_now(), r["id"]))
                _event(c, "run.recovered_stale", r["id"],
                       {"age_s": round(age, 1), "pid_alive": alive, "host": r["host"]})
            fixed.append(r["id"])
        return fixed


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


def _host() -> str:
    return socket.gethostname()


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
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

    return _d.datetime.now(_d.timezone.utc).isoformat(timespec="seconds")
