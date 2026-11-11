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
import os
import queue
import sqlite3
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

from fwcore import db as _db
from fwcore.models import ScriptedAdapter
from fwcore.policy import DirectPolicy
from fwcore.runtime import Runner, RunResult
from fwcore.tools import DEFAULT_TARGET_NAME

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
    target_status: str = "active"


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


def _is_continue_command(text: str) -> bool:
    """Recognize natural continuation commands, not only exact one-word input.

    Users routinely type "继续 先做 X" or "继续推进，顺便检查 Y".  These are
    still continuation commands; treating them as ordinary resume turns makes
    the model announce "next round" but the runtime only runs one turn.
    """
    s = (text or "").strip()
    if not s:
        return False
    return (s in ("继续", "继续推进", "continue")
            or s.startswith("继续")
            or s.casefold().startswith("continue"))


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
        self._pending: list[tuple[str, str, str | None, str | None]] = []
        self._returned: list[str] = []
        self._lock = threading.Lock()
        self._bootstrap_lock = threading.Lock()
        self._events_lock = threading.Lock()
        self._busy = False
        self._closed = False
        self._last_error: str | None = None
        self._thread: threading.Thread | None = None
        # 不在 drain 时丢事件：UI 的 250ms tick 负责消费；并发列表+clear 会丢 turn_done。
        self._events: deque[UiEvent] = deque()
        self._tool_cache: dict[str, tuple] = {}
        self._usage_cache: tuple | None = None
        self._started = False
        self._runner_ref: Runner | None = None
        self._cancel_event = threading.Event()
        self._runner_model_name: str | None = None
        self._model_cursor: int | None = None

    # ---------------- 生命周期 ----------------
    def _default_target(self) -> str | None:
        # Prefer the shared, firmware-neutral bootstrap target so a new TUI
        # session joins it instead of inventing another temporary target.
        row = self.services.con.execute(
            "SELECT name FROM targets WHERE status='active' AND name=? LIMIT 1",
            (DEFAULT_TARGET_NAME,)).fetchone()
        if row:
            return row["name"]
        row = self.services.con.execute(
            "SELECT name FROM targets WHERE status='active'"
            " ORDER BY created_at DESC LIMIT 1").fetchone()
        return row["name"] if row else None

    def ensure_bootstrap_target(self) -> str | None:
        """Create or reuse the shared, firmware-neutral default target.

        The default target is not tied to a firmware.  A run starts there; the
        agent can later call target_create to make a real target and move only
        this run's target_id.  The default stays available for other sessions.
        """
        with self._bootstrap_lock:
            with self._lock:
                if self.target:
                    return self.target
                if self._busy:
                    self.emit("error", "当前一轮仍在执行，暂时不能创建 bootstrap target。")
                    return None
            # Another session may already have created the shared default.
            row = self.services.con.execute(
                "SELECT name FROM targets WHERE status='active' AND name=? LIMIT 1",
                (DEFAULT_TARGET_NAME,)).fetchone()
            if row is not None:
                name = row["name"]
                with self._lock:
                    self.target = name
                    self.run_id = None
                self.emit("session.target", name, target=name, run=None,
                          bootstrap=True)
                return name
            last: Exception | None = None
            names = [DEFAULT_TARGET_NAME]
            names += [f"{DEFAULT_TARGET_NAME}-{uuid.uuid4().hex[:8]}" for _ in range(3)]
            for name in names:
                try:
                    self.services.domain.create_target(name)
                except Exception as e:  # noqa: BLE001 - collision or storage error
                    last = e
                    row = self.services.con.execute(
                        "SELECT name FROM targets WHERE status='active' AND name=? LIMIT 1",
                        (name,)).fetchone()
                    if row is not None:
                        with self._lock:
                            self.target = name
                            self.run_id = None
                        self.emit("session.target", name, target=name, run=None,
                                  bootstrap=True)
                        return name
                    continue
                with self._lock:
                    if self.target:
                        return self.target
                    self.target = name
                    self.run_id = None
                self.emit("session.target", name, target=name, run=None,
                          bootstrap=True)
                return name
            self.emit("error", f"bootstrap target 创建失败: {last or 'unknown error'}")
            return None

    def _on_target_set(self, name: str) -> None:
        """Callback from the target_create tool (worker thread)."""
        with self._lock:
            self.target = name
        self._tool_cache.clear()
        self._model_cursor = None
        self.emit("session.target", name, target=name, run=self.run_id,
                  bootstrap=False)

    def _target_status(self, target: str | None = None) -> str:
        target = target or self.target
        if not target:
            return "active"
        row = self.services.con.execute(
            "SELECT status FROM targets WHERE name=?", (target,)).fetchone()
        return (row["status"] if row else "active") or "active"

    def start(self) -> None:
        if self._started:
            return
        self._thread = threading.Thread(target=self._loop, name="fwapp-session", daemon=True)
        self._thread.start()
        self._started = True

    def close(self) -> None:
        # 先走现有 Runner.cancel / 安全点机制，再停止 worker；不创建第二套取消系统。
        if self.busy:
            self.cancel("应用退出")
        with self._lock:
            queued, self._pending = list(self._pending), []
        for text, cid, _target, queued_run in queued:
            run = queued_run or self.run_id
            if run:
                try:
                    self.services.domain.add_conversation(
                        run, "user", text, status="cancelled", client_id=cid,
                        meta={"source": "tui-queue", "cancelled": True})
                except Exception as e:  # noqa: BLE001
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
            return [item[0] for item in self._pending]

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def take_returned(self) -> list[str]:
        with self._lock:
            out, self._returned = self._returned, []
        return out

    # ---------------- UI 事件 ----------------
    def emit(self, kind: str, text: str = "", **data) -> None:
        ev = UiEvent(kind=kind, text=text, data=data)
        with self._events_lock:
            self._events.append(ev)

    def drain_events(self) -> list[UiEvent]:
        with self._events_lock:
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
            target = self.target
            run_id = self.run_id
        if target and self._target_status(target) != "active":
            self.emit("error", "目标已收尾或归档，不能开始新研究；先执行 unarchive 恢复。")
            return
        if not busy:
            # 新的一轮用户输入生效:清除上一轮的取消标记
            self._cancel_event.clear()
        if busy:
            # 排队也持久化(状态 queued);并把目标/会话与消息绑定，防止 busy 短暂间隙后串台。
            if run_id:
                try:
                    self.services.domain.add_conversation(
                        run_id, "user", text, status="queued",
                        client_id=client_id, meta={"source": "tui-queue"})
                except Exception as e:  # noqa: BLE001
                    self.emit("error", f"排队消息写入失败(仍会尝试执行): {e}")
            with self._lock:
                self._pending.append((text, client_id, target, run_id))
            self.emit("queued", text)
            return
        self._q.put(("turn", text, client_id, target, run_id))

    def new_session(self, text: str | None = None) -> None:
        with self._lock:
            if self._busy:
                busy = True
            else:
                busy = False
                self.run_id = None
        if busy:
            self.emit("error", "当前一轮仍在执行，请先按 Esc 停止，再新建会话。")
            return
        self._tool_cache.clear()
        self._model_cursor = None
        self.emit("session.new", "")
        if text:
            self.submit(text)

    def resume_run(self, run_id: str, *, run_now: bool = False) -> None:
        if self.busy:
            self.emit("error", "当前一轮仍在执行，暂时不能切换研究会话。")
            return
        row = self.services.con.execute(
            "SELECT r.id,t.name FROM runs r JOIN targets t ON t.id=r.target_id WHERE r.id=?",
            (run_id,)).fetchone()
        if not row:
            self.emit("error", f"研究会话不存在：{run_id}")
            return
        if self._target_status(row["name"]) != "active":
            self.emit("error", "目标已收尾或归档，不能继续研究会话；先执行 unarchive 恢复。")
            return
        with self._lock:
            if self._busy:
                self.emit("error", "当前一轮仍在执行，暂时不能切换研究会话。")
                return
            self.target = row["name"]
            self.run_id = run_id
            target = self.target
        self._tool_cache.clear()
        self._model_cursor = None
        self.emit("session.resumed", run_id, run=run_id, target=target)
        if run_now:
            self._q.put(("continue", None, None, target, run_id))

    def continue_run(self) -> bool:
        with self._lock:
            run_id = self.run_id
            target = self.target
            busy = self._busy
        if not run_id:
            self.emit("error", "当前没有可继续的研究会话。")
            return False
        if target and self._target_status(target) != "active":
            self.emit("error", "目标已收尾或归档，不能继续研究；先执行 unarchive 恢复。")
            return False
        if busy:
            self.emit("error", "当前一轮仍在执行，暂时不能继续。")
            return False
        self._q.put(("continue", None, None, target, run_id))
        return True

    def compact(self, instructions: str = "") -> bool:
        with self._lock:
            run_id = self.run_id
            target = self.target
            busy = self._busy
        if not run_id:
            self.emit("error", "当前没有可压缩的研究会话。")
            return False
        if target and self._target_status(target) != "active":
            self.emit("error", "目标已收尾或归档，不能压缩会话。")
            return False
        if busy:
            self.emit("error", "当前一轮仍在执行，暂时不能压缩。")
            return False
        self._q.put(("compact", instructions, None, target, run_id))
        return True

    def set_target(self, name: str) -> None:
        if self.busy:
            self.emit("error", "当前一轮仍在执行，暂时不能切换目标。")
            return
        if not self.services.con.execute("SELECT 1 FROM targets WHERE name=?", (name,)).fetchone():
            self.emit("error", f"目标不存在：{name}")
            return
        with self._lock:
            if self._busy:
                self.emit("error", "当前一轮仍在执行，暂时不能切换目标。")
                return
            self.target = name
            self.run_id = latest_run(self.services.con, name)
        self._tool_cache.clear()
        self._model_cursor = None
        self.emit("session.target", name, target=name, run=self.run_id)

    def set_model(self, name: str, info=None) -> bool:
        from fwcore.models import split_context_annotation

        from .config import ModelConfigError, adapter_for

        raw = (name or "").strip()
        if not raw:
            return False
        if info is None:
            candidates = [info for info in self.cfg.model_catalog
                          if info.api_id == raw or info.id == raw or info.label == raw]
            info = candidates[0] if candidates else None
        api_id, suffix_context = split_context_annotation(raw)
        if info is not None:
            api_id = info.api_id or api_id
        if raw in ("scripted", "fake"):
            api_id = "scripted"
        try:
            adapter_for(self.cfg, api_id)
        except ModelConfigError as e:
            self.emit("error", str(e))
            return False
        self.model_name = api_id
        if api_id not in ("scripted", "fake"):
            self.cfg.model = api_id
        if api_id in ("scripted", "fake"):
            self.cfg.limits.context_tokens = 0
        elif info is not None:
            if info.context_window:
                self.cfg.limits.context_tokens = info.context_window
            self.cfg.model_catalog_error = ""
        elif suffix_context:
            self.cfg.limits.context_tokens = suffix_context
        if self._runner_ref is not None and api_id not in ("scripted", "fake"):
            self._runner_ref.limits.context_tokens = self.cfg.limits.context_tokens
        self.emit("session.model", api_id, model=api_id,
                  label=getattr(info, "label", api_id) if info else api_id)
        return True

    def cancel(self, reason: str = "用户取消") -> bool:
        rid = self.run_id
        # 取消标记先置位:即使 run 还没创建(worker 正在 start),也能在
        # 创建后立即取消,而不是把取消丢掉。
        self._cancel_event.set()
        if not rid:
            with self._lock:
                queued = list(self._pending)
                self._pending.clear()
                self._returned.extend(item[0] for item in queued)
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
            except Exception as e:              # LockedError 等:控制已发,再试落库  # noqa: BLE001
                last_error = e
                time.sleep(0.05)
        if not (closed or paused):
            # 事件已置位:worker 会在安全点暂停;DB 状态由 turn 落库
            self.emit("warning", f"取消已生效,DB 暂停稍后落库: {last_error}")
        with self._lock:
            queued = list(self._pending)
            self._pending.clear()
            self._returned.extend(item[0] for item in queued)
        for text, cid, _target, queued_run in queued:
            try:
                self.services.domain.add_conversation(
                    queued_run or rid, "user", text, status="cancelled", client_id=cid,
                    meta={"source": "tui-queue", "cancelled": True})
            except Exception as e:  # noqa: BLE001
                self.emit("error", f"取消排队消息状态写入失败: {e}")
        self.emit("cancelled", reason, run=rid)
        return True

    def dequeue(self) -> list[str]:
        """把排队消息还给编辑器(Pi 的 Alt+Up 语义);DB 中标记为 cancelled。"""
        with self._lock:
            items, self._pending = list(self._pending), []
        for text, cid, _target, queued_run in items:
            run = queued_run or self.run_id
            if run:
                try:
                    self.services.domain.add_conversation(
                        run, "user", text, status="cancelled", client_id=cid,
                        meta={"source": "tui-queue", "cancelled": True})
                except Exception as e:  # noqa: BLE001
                    self.emit("error", f"取回排队消息状态写入失败: {e}")
        return [item[0] for item in items]

    # ---------------- worker ----------------
    def _loop(self) -> None:
        con = None
        runner = None
        try:
            con = _db.connect(self.cfg.db_path)
            # Pi 在会话启动时装配 MCP；不要等用户发第一条消息才让 /mcp
            # 看到服务器。后台 worker 里启动，不阻塞 TUI 输入。
            try:
                runner = self._make_runner(con)
                self._runner_ref = runner
                self._runner_model_name = self.model_name
                runner._ensure_mcp(None, background=True)
            except Exception as e:  # noqa: BLE001 - app layer must surface setup errors
                self._last_error = str(e)
            while not self._closed:
                item = self._q.get()
                if item is None:
                    break
                if self._closed:
                    break
                kind = item[0]
                arg = item[1] if len(item) > 1 else None
                client_id = item[2] if len(item) > 2 else None
                target = item[3] if len(item) > 3 else self.target
                run_id = item[4] if len(item) > 4 else self.run_id
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
                        self._do_turn(runner, str(arg), client_id, target, run_id)
                    elif kind == "continue":
                        self._do_continue(runner, target, run_id)
                    elif kind == "compact":
                        self._do_compact(runner, str(arg or ""), target, run_id)
                    elif kind == "restart":
                        runner.close()
                        runner = self._make_runner(con)
                        self._runner_ref = runner
                        self._runner_model_name = self.model_name
                except Exception as e:                 # 应用层兜底:显示,不吞  # noqa: BLE001
                    self._last_error = str(e)
                    self.emit("error", str(e), cmd=kind)
                finally:
                    with self._lock:
                        self._busy = False
                        nxt = self._pending.pop(0) if self._pending else None
                        if nxt and nxt[3] is None and nxt[2] == self.target:
                            # 首轮尚未创建 run_id 时排队的消息属于同一会话。
                            nxt = (nxt[0], nxt[1], nxt[2], self.run_id)
                    if nxt and not self._closed:
                        self._q.put(("turn", nxt[0], nxt[1], nxt[2], nxt[3]))
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
        # 本地交互会话直接全批准（Pi 式），不保留 authz/allow_all 开关。
        return Runner(
            con, self.cfg.root, self._adapter_for(self.model_name),
            limits=self.cfg.limits, watchdog=self._watchdog, mcp=self._mcp,
            watchdog_poll_s=5.0, mcp_call_timeout=120.0,
            on_stream=self._on_stream, stop_event=self._cancel_event,
            policy=DirectPolicy(), on_session_target=self._on_target_set,
        )

    def mcp_status(self) -> dict:
        """给 /mcp 用的只读状态；配置发现与 Runner 使用同一 Pi 兼容来源。"""
        from fwcore.mcp_config import discover_mcp_config, load_mcp_config

        sources = discover_mcp_config(self.cfg.root)
        cfg = load_mcp_config(sources, base_dir=self.cfg.root)
        names = sorted(cfg.servers)
        display = ", ".join(
            os.path.relpath(s.path, self.cfg.root) if os.path.isabs(s.path) else s.path
            for s in sources
        )
        runner = self._runner_ref
        return {
            "enabled": self._mcp,
            "config": display,
            "config_sources": [s.path for s in sources],
            "configured": names,
            "config_error": "\n".join(cfg.errors) or None,
            "health": runner.mcp_health() if runner is not None else None,
            "skipped": runner.mcp_skipped_reason if runner is not None else None,
        }

    def _on_stream(self, ev: dict) -> None:
        kind = str(ev.get("type") or "stream")
        text = str(ev.get("text") or "")
        data = {k: v for k, v in ev.items() if k not in ("type", "text")}
        self.emit(kind, text, **data)

    def _do_turn(self, runner: Runner, text: str, client_id: str | None = None,
                 target: str | None = None, run_id: str | None = None) -> None:
        target = target if target is not None else self.target
        rid = run_id
        if not target:
            self.emit("error", "还没有选择目标：请先选择目标，或运行 `fw-core init <目标>`。")
            return
        if self._target_status(target) != "active":
            self.emit("error", "目标已收尾或归档，不能开始研究；先执行 unarchive 恢复。")
            return
        if not rid:
            rid = runner.start(target, text)      # start 内持久化首条用户消息
            with self._lock:
                if self.target == target and self.run_id is None:
                    self.run_id = rid
            if self._cancel_event.is_set():
                # 取消发生在 start 之前/期间:创建后立即暂停,绝不发起模型调用
                runner.cancel(rid, "用户取消")
                res = RunResult(rid, "paused", 0, 0, "", False, [],
                                "user_stop: 用户取消", [])
                self._publish_result(res)
                return
            res = runner.run(rid, target, goal=text)
        elif _is_continue_command(text):
            self._cancel_event.clear()
            res = runner.advance(target, goal=text, run_id=rid, user_client_id=client_id)
        else:
            res = runner.resume(rid, goal=text, user_client_id=client_id)
        self._publish_result(res)

    def _do_continue(self, runner: Runner, target: str | None = None,
                     run_id: str | None = None) -> None:
        rid = run_id or self.run_id
        target = target or self.target
        if not rid:
            self.emit("error", "当前没有可继续的研究会话。")
            return
        if not target:
            self.emit("error", "当前没有目标，不能继续研究。")
            return
        self._cancel_event.clear()
        # `/continue` is the auto-continue entry: Runner.advance stops on a
        # new finding, a handback, budget/pause, or two idle rounds.
        res = runner.advance(target, run_id=rid)
        self._publish_result(res)

    def _do_compact(self, runner: Runner, instructions: str = "",
                    target: str | None = None, run_id: str | None = None) -> None:
        rid = run_id or self.run_id
        if not rid:
            self.emit("error", "当前没有可压缩的研究会话。")
            return
        try:
            info = runner.compact_now(rid, instructions=instructions or "")
        except Exception as e:  # noqa: BLE001
            self.emit("error", f"上下文压缩失败：{e}")
            return
        self.emit("compact_done", "", **info)

    def _publish_result(self, res) -> None:
        data = {"run": res.run_id, "state": res.state, "handback": res.handback,
                "blockers": list(res.blockers or []), "final": (res.final or "")[:4000],
                "finding": bool(getattr(res, "finding", False))}
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
            target_status=self._target_status(target),
        )

    def _tokens(self) -> dict:
        if not self.run_id:
            return {"prompt": 0, "completion": 0, "cached": 0, "total": 0,
                    "calls": 0, "last_prompt": 0, "last_cached": 0}
        last = self.services.con.execute(
            "SELECT MAX(id) m FROM events WHERE kind='model.call' AND ref=?",
            (self.run_id,)).fetchone()["m"]
        if self._usage_cache and self._usage_cache[0] == (self.run_id, last):
            return self._usage_cache[1]
        out = {"prompt": 0, "completion": 0, "cached": 0, "total": 0,
               "calls": 0, "last_prompt": 0, "last_cached": 0}
        for r in self.services.con.execute(
                "SELECT payload FROM events WHERE kind='model.call' AND ref=? ORDER BY id",
                (self.run_id,)).fetchall():
            p = _json(r["payload"], {})
            u = p.get("usage") or {}
            prompt = int(u.get("prompt_tokens") or p.get("prompt_tokens") or 0)
            cached = int(p.get("cached_tokens") or 0)
            out["prompt"] += prompt
            out["completion"] += int(u.get("completion_tokens") or p.get("completion_tokens") or 0)
            out["cached"] += cached
            out["calls"] += 1
            # Pi 的 footer 显示最近一次 main 请求的 cache hit rate;
            # summarize 调用不混进这个指标。最新 main 没有 usage 时必须是 0,
            # 不能沿用更早 main 的数字。
            if p.get("kind") != "summarize":
                out["last_prompt"] = prompt
                out["last_cached"] = cached
        out["total"] = out["prompt"] + out["completion"]
        self._usage_cache = ((self.run_id, last), out)
        return out

    def _context(self, tokens: dict) -> dict:
        window = int(getattr(self.cfg.limits, "context_tokens", 0) or 0)
        # Match Pi's footer: only the last *main* provider request is a real
        # context measurement.  No local chars/2.0 fallback.
        prompt = int(tokens.get("last_prompt") or 0)
        return {"window": window, "last_prompt": prompt,
                "percent": round(prompt / window * 100, 1) if window and prompt else None}

    # ---------------- 转录 ----------------
    def transcript(self, limit: int = 400) -> list[TranscriptItem]:
        """用户可见对话:conversation 为准,缺失的 assistant/tool 从权威记录补齐。

        权威关系:tool_calls = 工具是否真的执行;conversation = 模型/用户可见消息;
        checkpoint = 仅 step 指针。模型历史由 conversation+compactions 重建。
        """
        if not self.run_id:
            return []
        con = self.services.con
        tool_rows = [dict(r) for r in con.execute(
            "SELECT id,call_key,tool,args,status,note,exit_code,duration_ms,stdout_path,"
            "result_path,created_at FROM tool_calls WHERE run_id=? ORDER BY created_at,id",
            (self.run_id,)).fetchall()]
        # 同一工具调用可能按 call_key 或数据库 id 被引用；对话记录里的
        # tool_call_id 对“未授权/未执行”调用可能只有模型 call id，而 tool_calls
        # 行的 call_key 为空。只按 (call_key or id) 查会漏配，最后又把真实
        # tool_calls 补到末尾，造成“工具全跑最后”。
        tools: dict[str, dict] = {}
        for row in tool_rows:
            if row.get("call_key"):
                tools[str(row["call_key"])] = row
            tools[str(row["id"])] = row
        used_tool_ids: set[str] = set()
        entries = self.services.domain.conversation(self.run_id, limit=limit)
        if not entries:
            return []

        # Everything visible comes from the append-only session log.  Reasoning
        # is stored in assistant meta; do not reach into the retired full-history
        # checkpoint table.
        reasoning_by_key: dict[tuple[str, tuple[str, ...]], str] = {}
        for e in entries:
            if e.get("role") != "assistant":
                continue
            meta = _json(e.get("meta"), {})
            reasoning = (meta.get("reasoning") or "").strip()
            if reasoning:
                key = ((e.get("content") or "").strip(),
                       tuple(meta.get("tool_calls") or []))
                reasoning_by_key[key] = reasoning

        def _same_tool_status(a, b) -> bool:
            return a == b or (a == "error" and b in ("error", "timeout")) or (
                b == "error" and a in ("error", "timeout"))

        def _tool_row_for_entry(e, meta: dict) -> dict | None:
            tid = e.get("tool_call_id") or ""
            row = tools.get(tid)
            if row is not None and row["id"] not in used_tool_ids:
                return row
            # 旧记录/未执行工具可能没有 call_key；用 tool/status/时间秒做一次
            # 有界的同批匹配，避免把真实行再追加到末尾。
            wanted_tool = meta.get("tool")
            wanted_status = e.get("status") or "ok"
            wanted_sec = (e.get("created_at") or "")[:19]
            for cand in tool_rows:
                if cand["id"] in used_tool_ids:
                    continue
                if wanted_tool and cand.get("tool") != wanted_tool:
                    continue
                if not _same_tool_status(wanted_status, cand.get("status") or "ok"):
                    continue
                cand_sec = (cand.get("created_at") or "")[:19]
                if wanted_sec and cand_sec and wanted_sec != cand_sec:
                    continue
                return cand
            return None

        conv: list[dict] = []
        for e in entries:
            role = e.get("role")
            content = e.get("content") or ""
            meta = _json(e.get("meta"), {})
            tool_ids = tuple(meta.get("tool_calls") or [])
            item = None
            if role == "user" and content.strip():
                suffix = ""
                if e.get("status") == "queued":
                    suffix = "  （已排队）"
                elif e.get("status") == "cancelled":
                    suffix = "  （已取消）"
                item = TranscriptItem("user", content + suffix)
            elif role == "assistant":
                reasoning = reasoning_by_key.get((content.strip(), tool_ids), "")
                if content.strip() or reasoning:
                    item = TranscriptItem("assistant", content, {"reasoning": reasoning})
            elif role == "tool":
                row = _tool_row_for_entry(e, meta)
                if row is not None:
                    used_tool_ids.add(row["id"])
                else:
                    row = {"id": e.get("id"), "tool": meta.get("tool") or "tool",
                           "args": "{}", "status": e.get("status") or "ok",
                           "note": content, "created_at": e.get("created_at")}
                item = TranscriptItem("tool", "", {"call": row})
            conv.append({"item": item, "role": role, "content": content,
                         "tool_id": e.get("tool_call_id") or "",
                         "tool_ids": tool_ids,
                         "status": e.get("status")})

        execs = []
        for e in entries:
            if e.get("role") != "assistant":
                continue
            content = (e.get("content") or "").strip()
            meta = _json(e.get("meta"), {})
            call_ids = tuple(meta.get("tool_calls") or [])
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
                        anchor = max(anchor, last_anchor)
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
                row = tools.get(cid) or tools.get(str(cid))
                if row is not None and row["id"] not in used_tool_ids:
                    add_after(last_anchor, TranscriptItem("tool", "", {"call": row}))
                    used_tool_ids.add(row["id"])
                    used_tools.add(cid)

        items: list[TranscriptItem] = list(after.get(-1, []))
        for i, c in enumerate(conv):
            if c["item"] is not None:
                items.append(c["item"])
            items.extend(after.get(i, []))
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
