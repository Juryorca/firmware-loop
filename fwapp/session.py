"""Agent Session:把现有 Runner 装进一个可被 UI 驱动的会话。

对应 Pi 的 `AgentSession` + `AgentSessionRuntime` 的**应用侧**职责,
但**不实现 Agent Loop** —— 每一轮都调用 fwcore.Runner 的
turn / resume / start,研究状态、预算、取消、看护全部留在 Core。

线程模型:
- 一个后台 worker 线程独占自己的 SQLite 连接与 Runner(模型调用是阻塞的);
- UI 线程用 `services.con` 只读快照,并在取消时直接走 Runner.cancel;
- 所有写仍由 Core 的 CAS / 租约 / 预算控制,应用层不写研究事实。
"""

from __future__ import annotations

import contextlib
import json
import queue
import sqlite3
import threading
import uuid
import time
from collections import deque
from dataclasses import dataclass, field

from fwcore import db as _db
from fwcore.compact import estimate_tokens
from fwcore.models import ScriptedAdapter
from fwcore.runtime import RunResult, Runner

from .config import AppConfig, adapter_for
from .services import Services, latest_run


@dataclass
class UiEvent:
    kind: str
    text: str = ""
    data: dict = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass
class Snapshot:
    target: str | None
    run_id: str | None
    state: str
    handback: str | None
    goal: str | None
    model: str
    rounds: int
    step: int
    calls: int
    tools: int
    elapsed: float
    limits: dict
    budget: dict
    tokens: dict
    context: dict
    hypotheses: dict
    surfaces: dict
    evidence: int
    observations: int
    blockers: list[str]
    busy: bool
    pending: list[str]
    last_error: str | None
    updated_at: str | None


@dataclass
class TranscriptItem:
    kind: str          # user / assistant / tool / notice
    text: str = ""
    meta: dict = field(default_factory=dict)


def _json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


class AgentSession:
    """一个 target × model 的交互会话;同一时间只跑一个 turn。"""

    def __init__(self, services: Services, *, target: str | None = None,
                 run_id: str | None = None, model: str | None = None,
                 adapter_factory=None, mcp: bool = True, watchdog: bool = True):
        self.services = services
        self.cfg: AppConfig = services.cfg
        self.target = target or self._default_target()
        self.run_id = run_id
        if self.target and not self.run_id:
            self.run_id = latest_run(services.con, self.target)
        self.model_name = model or self.cfg.model_name
        self._adapter_factory = adapter_factory
        self._mcp = mcp
        self._watchdog = watchdog
        self._q: queue.Queue = queue.Queue()
        self._pending: list[tuple[str, str]] = []
        self._returned: list[str] = []
        self._lock = threading.Lock()
        self._busy = False
        self._closed = False
        self._last_error: str | None = None
        self._thread: threading.Thread | None = None
        self._events: deque[UiEvent] = deque(maxlen=400)
        self._tool_cache: dict[str, tuple] = {}
        self._usage_cache: tuple | None = None
        self._started = False
        self._runner_ref: Runner | None = None
        self._cancel_event = threading.Event()
        self._runner_model_name: str | None = None
        self._model_cursor: int | None = None

    # ---------------- 生命周期 ----------------
    def _default_target(self) -> str | None:
        row = self.services.con.execute(
            "SELECT name FROM targets ORDER BY created_at DESC LIMIT 1").fetchone()
        return row["name"] if row else None

    def start(self) -> None:
        if self._started:
            return
        self._thread = threading.Thread(target=self._loop, name="fwapp-session", daemon=True)
        self._thread.start()
        self._started = True

    def close(self) -> None:
        with self._lock:
            queued, self._pending = list(self._pending), []
        for text, cid in queued:
            if self.run_id:
                try:
                    self.services.domain.add_conversation(
                        self.run_id, "user", text, status="cancelled", client_id=cid,
                        meta={"source": "tui-queue", "cancelled": True})
                except Exception as e:
                    self.emit("error", f"关闭时排队消息状态写入失败: {e}")
        self._closed = True
        self._q.put(None)
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.services.close()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._busy

    @property
    def pending(self) -> list[str]:
        with self._lock:
            return [text for text, _cid in self._pending]

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def take_returned(self) -> list[str]:
        with self._lock:
            out, self._returned = self._returned, []
        return out

    # ---------------- UI 事件 ----------------
    def emit(self, kind: str, text: str = "", **data) -> None:
        self._events.append(UiEvent(kind=kind, text=text, data=data))

    def drain_events(self) -> list[UiEvent]:
        out = list(self._events)
        self._events.clear()
        return out

    # ---------------- 提交与控制 ----------------
    def submit(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        client_id = f"tui-{uuid.uuid4().hex}"     # 同一提交重试幂等
        with self._lock:
            busy = self._busy
        if not busy:
            # 新的一轮用户输入生效:清除上一轮的取消标记
            self._cancel_event.clear()
        if busy:
            # 排队也持久化(状态 queued);执行时同一 client_id 推进为 ok,不产生第二条
            if self.run_id:
                try:
                    self.services.domain.add_conversation(
                        self.run_id, "user", text, status="queued",
                        client_id=client_id, meta={"source": "tui-queue"})
                except Exception as e:
                    self.emit("error", f"排队消息写入失败(仍会尝试执行): {e}")
            with self._lock:
                self._pending.append((text, client_id))
            self.emit("queued", text)
            return
        self._q.put(("turn", text, client_id))

    def new_session(self, text: str | None = None) -> None:
        if self.busy:
            self.emit("error", "正在执行;先 Esc 停止再 /new")
            return
        self.run_id = None
        self._tool_cache.clear()
        self._model_cursor = None
        self.emit("session.new", "")
        if text:
            self.submit(text)

    def resume_run(self, run_id: str, *, run_now: bool = False) -> None:
        if self.busy:
            self.emit("error", "正在执行;不能切换 run")
            return
        row = self.services.con.execute(
            "SELECT r.id,t.name FROM runs r JOIN targets t ON t.id=r.target_id WHERE r.id=?",
            (run_id,)).fetchone()
        if not row:
            self.emit("error", f"run 不存在: {run_id}")
            return
        self.target = row["name"]
        self.run_id = run_id
        self._tool_cache.clear()
        self._model_cursor = None
        self.emit("session.resumed", run_id, run=run_id, target=self.target)
        if run_now:
            self._q.put(("continue", None))

    def continue_run(self) -> None:
        if not self.run_id:
            self.emit("error", "没有可继续的 run")
            return
        if self.busy:
            self.emit("queued", "(continue)")
            return
        self._q.put(("continue", None))

    def set_target(self, name: str) -> None:
        if self.busy:
            self.emit("error", "正在执行;不能切换目标")
            return
        if not self.services.con.execute("SELECT 1 FROM targets WHERE name=?", (name,)).fetchone():
            self.emit("error", f"目标不存在: {name}")
            return
        self.target = name
        self.run_id = latest_run(self.services.con, name)
        self._tool_cache.clear()
        self._model_cursor = None
        self.emit("session.target", name, target=name, run=self.run_id)

    def set_model(self, name: str) -> None:
        self.model_name = name
        self.emit("session.model", name, model=name)

    def cancel(self, reason: str = "用户取消") -> bool:
        rid = self.run_id
        # 取消标记先置位:即使 run 还没创建(worker 正在 start),也能在
        # 创建后立即取消,而不是把取消丢掉。
        self._cancel_event.set()
        if not rid:
            with self._lock:
                queued = list(self._pending)
                self._pending.clear()
                self._returned.extend(text for text, _cid in queued)
            self.emit("cancelled", reason, run=None)
            return True
        runner = self._runner_ref
        closed = False
        ctrl = getattr(runner, "stream_control", None) if runner is not None else None
        if ctrl is not None:
            with contextlib.suppress(Exception):
                ctrl.cancel()
                closed = True
        paused = False
        last_error: Exception | None = None
        for _ in range(40):                     # 最多 ~2s:worker 可能在写大 checkpoint
            try:
                ctl = Runner(self.services.con, self.cfg.root, ScriptedAdapter(script=[]),
                             mcp=False, watchdog=False)
                ctl.cancel(rid, reason)
                paused = True
                break
            except Exception as e:              # LockedError 等:控制已发,再试落库
                last_error = e
                time.sleep(0.05)
        if not (closed or paused):
            # 事件已置位:worker 会在安全点暂停;DB 状态由 turn 落库
            self.emit("warning", f"取消已生效,DB 暂停稍后落库: {last_error}")
        with self._lock:
            queued = list(self._pending)
            self._pending.clear()
            self._returned.extend(text for text, _cid in queued)
        for text, cid in queued:
            try:
                self.services.domain.add_conversation(
                    rid, "user", text, status="cancelled", client_id=cid,
                    meta={"source": "tui-queue", "cancelled": True})
            except Exception as e:
                self.emit("error", f"取消排队消息状态写入失败: {e}")
        self.emit("cancelled", reason, run=rid)
        return True

    def dequeue(self) -> list[str]:
        """把排队消息还给编辑器(Pi 的 Alt+Up 语义);DB 中标记为 cancelled。"""
        with self._lock:
            items, self._pending = list(self._pending), []
        for text, cid in items:
            if self.run_id:
                try:
                    self.services.domain.add_conversation(
                        self.run_id, "user", text, status="cancelled", client_id=cid,
                        meta={"source": "tui-queue", "cancelled": True})
                except Exception as e:
                    self.emit("error", f"取回排队消息状态写入失败: {e}")
        return [text for text, _cid in items]

    # ---------------- worker ----------------
    def _loop(self) -> None:
        con = None
        runner = None
        try:
            con = _db.connect(self.cfg.db_path)
            while not self._closed:
                item = self._q.get()
                if item is None:
                    break
                kind = item[0]
                arg = item[1] if len(item) > 1 else None
                client_id = item[2] if len(item) > 2 else None
                with self._lock:
                    self._busy = True
                self.emit("busy", kind)
                try:
                    if runner is None:
                        runner = self._make_runner(con)
                        self._runner_ref = runner
                        self._runner_model_name = self.model_name
                    elif self._runner_model_name != self.model_name:
                        runner.model = self._adapter_for(self.model_name)
                        self._runner_model_name = self.model_name
                    if kind == "turn":
                        self._do_turn(runner, str(arg), client_id)
                    elif kind == "continue":
                        self._do_continue(runner)
                    elif kind == "restart":
                        runner.close()
                        runner = self._make_runner(con)
                        self._runner_ref = runner
                        self._runner_model_name = self.model_name
                except Exception as e:                 # 应用层兜底:显示,不吞
                    self._last_error = str(e)
                    self.emit("error", str(e), cmd=kind)
                finally:
                    with self._lock:
                        self._busy = False
                        nxt = self._pending.pop(0) if self._pending else None
                    if nxt and not self._closed:
                        self._q.put(("turn", nxt[0], nxt[1]))
        finally:
            if runner is not None:
                with contextlib.suppress(Exception):
                    runner.close()
            self._runner_ref = None
            if con is not None:
                with contextlib.suppress(Exception):
                    con.close()

    def _adapter_for(self, name: str):
        if self._adapter_factory is not None:
            return self._adapter_factory(name)
        return adapter_for(self.cfg, name)

    def _make_runner(self, con: sqlite3.Connection) -> Runner:
        return Runner(
            con, self.cfg.root, self._adapter_for(self.model_name),
            limits=self.cfg.limits, watchdog=self._watchdog, mcp=self._mcp,
            watchdog_poll_s=5.0, mcp_call_timeout=120.0,
            on_stream=self._on_stream, stop_event=self._cancel_event,
        )

    def _on_stream(self, ev: dict) -> None:
        kind = str(ev.get("type") or "stream")
        self._events.append(UiEvent(kind=kind, text=str(ev.get("text") or ""), data=dict(ev)))

    def _do_turn(self, runner: Runner, text: str, client_id: str | None = None) -> None:
        if not self.target:
            self.emit("error", "没有目标:先用 `fw-core init <target>` 创建,或在选择器里选目标")
            return
        rid = self.run_id
        if not rid:
            rid = runner.start(self.target, text)      # start 内持久化首条用户消息
            self.run_id = rid
            if self._cancel_event.is_set():
                # 取消发生在 start 之前/期间:创建后立即暂停,绝不发起模型调用
                runner.cancel(rid, "用户取消")
                res = RunResult(rid, "paused", 0, 0, "", False, [],
                                "user_stop: 用户取消", [])
                self._publish_result(res)
                return
            res = runner.run(rid, self.target, goal=text)
        else:
            res = runner.resume(rid, goal=text, user_client_id=client_id)
        self._publish_result(res)

    def _do_continue(self, runner: Runner) -> None:
        if not self.run_id:
            self.emit("error", "没有可继续的 run")
            return
        self._cancel_event.clear()
        res = runner.resume(self.run_id)    # 不伪造新的用户消息
        self._publish_result(res)

    def _publish_result(self, res) -> None:
        data = {"run": res.run_id, "state": res.state, "handback": res.handback,
                "blockers": list(res.blockers or []), "final": (res.final or "")[:4000]}
        self.emit("turn_done", data["final"], **data)

    # ---------------- 只读快照 ----------------
    def snapshot(self) -> Snapshot:
        con = self.services.con
        row = None
        if self.run_id:
            row = con.execute(
                "SELECT r.*, t.name AS target FROM runs r JOIN targets t ON t.id=r.target_id"
                " WHERE r.id=?", (self.run_id,)).fetchone()
        if row is None and self.target:
            row = con.execute(
                "SELECT r.*, t.name AS target FROM runs r JOIN targets t ON t.id=r.target_id"
                " WHERE t.name=? ORDER BY r.started_at DESC, r.rowid DESC LIMIT 1",
                (self.target,)).fetchone()
        target = row["target"] if row else self.target
        derived, blockers = {}, []
        if target:
            with contextlib.suppress(Exception):
                derived = self.services.domain.derived(target)
                blockers = list(derived.get("closure_blockers") or [])
        tokens = self._tokens()
        return Snapshot(
            target=target, run_id=row["id"] if row else self.run_id,
            state=row["state"] if row else "idle",
            handback=row["handback_reason"] if row else None,
            goal=row["goal"] if row else None,
            model=self.model_name,
            rounds=int(row["rounds"] or 0) if row else 0,
            step=int(row["step"] or 0) if row else 0,
            calls=int(row["model_calls"] or 0) if row else 0,
            tools=int(row["tool_calls"] or 0) if row else 0,
            elapsed=float(row["elapsed_s"] or 0.0) if row else 0.0,
            limits=self.cfg.limits.describe(),
            budget=_json(row["budget_note"], {}) if row else {},
            tokens=tokens,
            context=self._context(tokens),
            hypotheses=derived.get("hypotheses", {}),
            surfaces=derived.get("surfaces", {}),
            evidence=int(derived.get("evidence", 0) or 0),
            observations=int(derived.get("observations", 0) or 0),
            blockers=blockers,
            busy=self.busy, pending=self.pending, last_error=self._last_error,
            updated_at=row["updated_at"] if row else None,
        )

    def _tokens(self) -> dict:
        if not self.run_id:
            return {"prompt": 0, "completion": 0, "cached": 0, "total": 0, "calls": 0}
        last = self.services.con.execute(
            "SELECT MAX(id) m FROM events WHERE kind='model.call' AND ref=?",
            (self.run_id,)).fetchone()["m"]
        if self._usage_cache and self._usage_cache[0] == (self.run_id, last):
            return self._usage_cache[1]
        out = {"prompt": 0, "completion": 0, "cached": 0, "total": 0, "calls": 0}
        for r in self.services.con.execute(
                "SELECT payload FROM events WHERE kind='model.call' AND ref=? ORDER BY id",
                (self.run_id,)).fetchall():
            p = _json(r["payload"], {})
            u = p.get("usage") or {}
            out["prompt"] += int(u.get("prompt_tokens") or p.get("prompt_tokens") or 0)
            out["completion"] += int(u.get("completion_tokens") or p.get("completion_tokens") or 0)
            out["cached"] += int(p.get("cached_tokens") or 0)
            out["calls"] += 1
        out["total"] = out["prompt"] + out["completion"]
        self._usage_cache = ((self.run_id, last), out)
        return out

    def _context(self, tokens: dict) -> dict:
        est = 0
        if self.run_id:
            ck = self.services.con.execute(
                "SELECT messages FROM checkpoints WHERE run_id=? ORDER BY step DESC, rowid DESC LIMIT 1",
                (self.run_id,)).fetchone()
            msgs = _json(ck["messages"], []) if ck else []
            with contextlib.suppress(Exception):
                est = estimate_tokens(msgs)
        window = int(getattr(self.cfg.limits, "context_tokens", 0) or 0)
        last = self.services.con.execute(
            "SELECT payload FROM events WHERE kind='model.call' AND ref=? ORDER BY id DESC LIMIT 1",
            (self.run_id,)).fetchone() if self.run_id else None
        prompt = 0
        if last:
            p = _json(last["payload"], {})
            prompt = int(p.get("prompt_tokens") or 0)
        return {"estimated": est, "window": window, "last_prompt": prompt,
                "percent": round((prompt or est) / window * 100, 1) if window else None}

    # ---------------- 转录 ----------------
    def transcript(self, limit: int = 400) -> list[TranscriptItem]:
        """用户可见对话:conversation 为准,缺失的 assistant/tool 从权威记录补齐。

        权威关系:tool_calls = 工具是否真的执行;checkpoint = 模型执行历史;
        conversation = 用户可见消息。三者不互相替代,只做**幂等合并**。
        """
        if not self.run_id:
            return []
        con = self.services.con
        tools = {r["call_key"] or r["id"]: dict(r) for r in con.execute(
            "SELECT id,call_key,tool,args,status,note,exit_code,duration_ms,stdout_path,"
            "result_path,created_at FROM tool_calls WHERE run_id=? ORDER BY created_at,id",
            (self.run_id,)).fetchall()}
        entries = self.services.domain.conversation(self.run_id, limit=limit)
        ck = con.execute(
            "SELECT messages FROM checkpoints WHERE run_id=? ORDER BY step DESC, rowid DESC LIMIT 1",
            (self.run_id,)).fetchone()

        if not entries:
            return self._transcript_from_checkpoint(ck, tools, limit)

        conv: list[dict] = []
        for e in entries:
            role = e.get("role")
            content = e.get("content") or ""
            item = None
            if role == "user" and content.strip():
                suffix = ""
                if e.get("status") == "queued":
                    suffix = "  (queued)"
                elif e.get("status") == "cancelled":
                    suffix = "  (cancelled)"
                item = TranscriptItem("user", content + suffix)
            elif role == "assistant" and content.strip():
                item = TranscriptItem("assistant", content)
            elif role == "tool":
                row = tools.get(e.get("tool_call_id") or "")
                if row is None:
                    meta = _json(e.get("meta"), {})
                    row = {"id": e.get("id"), "tool": meta.get("tool") or "tool",
                           "args": "{}", "status": e.get("status") or "ok",
                           "note": content, "created_at": e.get("created_at")}
                item = TranscriptItem("tool", "", {"call": row})
            meta = _json(e.get("meta"), {})
            conv.append({"item": item, "role": role, "content": content,
                         "tool_id": e.get("tool_call_id") or "",
                         "tool_ids": tuple(meta.get("tool_calls") or []),
                         "status": e.get("status")})

        msgs = _json(ck["messages"], []) if ck else []
        execs = []
        for m in msgs[-limit:]:
            if m.get("role") != "assistant":
                continue
            content = (m.get("content") or "").strip()
            call_ids = tuple(c.get("id") or "" for c in (m.get("tool_calls") or []))
            if content or call_ids:
                execs.append({"content": content, "call_ids": call_ids})

        avail: dict[tuple, list[int]] = {}
        tool_index: dict[str, int] = {}
        for i, c in enumerate(conv):
            if c["role"] == "assistant":
                avail.setdefault((c["content"].strip(), c["tool_ids"]), []).append(i)
            elif c["role"] == "tool" and c["tool_id"]:
                tool_index[c["tool_id"]] = i
        used_assist: set[int] = set()
        used_tools: set[str] = {c["tool_id"] for c in conv
                                if c["role"] == "tool" and c["tool_id"]}
        after: dict[int, list[TranscriptItem]] = {}

        def add_after(idx: int, item: TranscriptItem) -> None:
            after.setdefault(idx, []).append(item)

        last_anchor = -1
        for ex in execs:
            key = (ex["content"], ex["call_ids"])
            matched = next((i for i in avail.get(key, []) if i not in used_assist), None)
            if matched is not None:
                used_assist.add(matched)
                last_anchor = matched
            else:
                # 缺失 assistant:优先插在它声明的工具记录之前,否则插在下一个
                # assistant 之前 / 最后一条用户消息之后。
                cand = min((tool_index[i] for i in ex["call_ids"] if i in tool_index),
                           default=None)
                if cand is not None:
                    anchor = cand - 1
                    if ex["content"]:
                        add_after(anchor, TranscriptItem("assistant", ex["content"]))
                    last_anchor = anchor
                else:
                    nxt = next((i for i, c in enumerate(conv)
                                if i > last_anchor and c["role"] == "assistant"), None)
                    if nxt is not None:
                        anchor = nxt - 1
                    else:
                        anchor = max((i for i, c in enumerate(conv) if c["role"] == "user"),
                                     default=last_anchor)
                        if anchor < last_anchor:
                            anchor = last_anchor
                    if ex["content"]:
                        add_after(anchor, TranscriptItem("assistant", ex["content"]))
                    last_anchor = anchor
            for cid in ex["call_ids"]:
                if not cid:
                    continue
                if cid in tool_index:
                    last_anchor = max(last_anchor, tool_index[cid])
                    used_tools.add(cid)
                    continue
                row = tools.get(cid)
                if row is not None:
                    add_after(last_anchor, TranscriptItem("tool", "", {"call": row}))
                    used_tools.add(cid)

        # 已执行但既不在 conversation、也不在 checkpoint 里的工具:补在末尾
        for key, row in tools.items():
            if key not in used_tools:
                add_after(last_anchor, TranscriptItem("tool", "", {"call": row}))
                used_tools.add(key)

        items: list[TranscriptItem] = list(after.get(-1, []))
        for i, c in enumerate(conv):
            if c["item"] is not None:
                items.append(c["item"])
            items.extend(after.get(i, []))
        return items

    def _transcript_from_checkpoint(self, ck, tools: dict, limit: int) -> list[TranscriptItem]:
        """兼容迁移前/旧 run:从 checkpoint 重建(用户 goal 只显示一次)。"""
        items: list[TranscriptItem] = []
        con = self.services.con
        run = con.execute("SELECT goal FROM runs WHERE id=?", (self.run_id,)).fetchone()
        if run and run["goal"]:
            items.append(TranscriptItem("user", run["goal"]))
        used_tools: set[str] = set()
        if ck:
            msgs = _json(ck["messages"], [])
            for m in msgs[-limit:]:
                if m.get("role") != "assistant":
                    continue
                content = m.get("content") or ""
                reasoning = m.get("reasoning_content") or ""
                if content.strip() or reasoning.strip():
                    items.append(TranscriptItem("assistant", content, {"reasoning": reasoning}))
                for c in m.get("tool_calls") or []:
                    key = c.get("id") or ""
                    row = tools.get(key)
                    if row:
                        used_tools.add(key)
                        items.append(TranscriptItem("tool", "", {"call": row}))
        for key, row in tools.items():
            if key not in used_tools:
                items.append(TranscriptItem("tool", "", {"call": row}))
        return items

    def model_updates(self) -> list[dict]:
        """新的 model.call 事件(UI 用来展示实时模型活动)。"""
        if not self.run_id:
            return []
        con = self.services.con
        if self._model_cursor is None:
            row = con.execute(
                "SELECT MAX(id) m FROM events WHERE kind='model.call' AND ref=?",
                (self.run_id,)).fetchone()
            self._model_cursor = int(row["m"] or 0)
            return []
        rows = con.execute(
            "SELECT id,payload FROM events WHERE kind='model.call' AND ref=? AND id>? ORDER BY id",
            (self.run_id, self._model_cursor)).fetchall()
        if rows:
            self._model_cursor = int(rows[-1]["id"])
        return [{"id": r["id"], "payload": _json(r["payload"], {})} for r in rows]

    def tool_updates(self) -> list[dict]:
        """返回新出现或状态变化的工具调用(UI 增量渲染用)。"""
        if not self.run_id:
            return []
        rows = self.services.con.execute(
            "SELECT id,call_key,tool,args,status,note,exit_code,duration_ms,stdout_path,"
            "result_path,created_at FROM tool_calls WHERE run_id=? ORDER BY created_at,id",
            (self.run_id,)).fetchall()
        changed = []
        for r in rows:
            d = dict(r)
            sig = (d["status"], d["note"], d["exit_code"], d["duration_ms"], d["result_path"])
            if self._tool_cache.get(d["id"]) != sig:
                self._tool_cache[d["id"]] = sig
                changed.append(d)
        return changed

    def recent_events(self, limit: int = 8) -> list[dict]:
        if not self.run_id:
            return []
        rows = self.services.con.execute(
            "SELECT ts,kind,ref,payload FROM events WHERE ref=? ORDER BY id DESC LIMIT ?",
            (self.run_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def research_view(self) -> dict:
        if not self.target:
            return {}
        return self.services.domain.derived(self.target)
