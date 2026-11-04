"""运行时:模型 ↔ 工具循环 + 检查点 + 恢复(宿主无关,不依赖 Pi)。

职责(与任务书一致)
- **模型**选择行动(下一步研究什么、调哪个工具、参数);
- **运行时**负责:提供工具清单、执行调用、记录真实状态、组织上下文、持久化、恢复、追踪;
- **领域层**负责事实与不变量(见 domain.py)。

中断语义:进程/上下文中断 ⇒ run 置 `paused` 并写检查点;恢复时不重读长篇 notes,
而是从权威状态 + 最近事件重建上下文。中断**不**改变任何假设结论。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from . import db as _db
from .domain import Domain, new_id
from .tools import Registry, ToolContext, default_registry

SYSTEM = """你是一个固件漏洞研究 agent。你在一个**已经建立权威状态**的框架里工作:

- 研究事实(目标/攻击面/假设/尝试/观察/证据/结论)由工具写入,不要假设它们会被自动推断。
- 一次尝试(result)有六种:supported / refuted / inconclusive / environment_failure / no_signal / cancelled。
  后三种**不会**关闭假设 —— 工具失败、超时、没打到目标,都不是研究结论。
- 下结论必须给 scope_note(这条结论管到哪个入口/函数/版本)。判 vulnerable 必须已挂证据。
- 优先解决"最可能改变判断的关键未知",主动找能推翻当前解释的反例;不要用抽样代表整体。
- 想结束本轮时,输出一行 `FINAL: <一句话小结>`(不调用工具)。
"""


class Model(Protocol):
    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        """返回 {"content": str, "tool_calls": [{"name":..., "args": {...}}]}"""


@dataclass
class ScriptedModel:
    """测试用:按脚本回复;脚本用尽后返回 FINAL。"""

    script: list[dict] = field(default_factory=list)
    i: int = 0

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        if self.i < len(self.script):
            out = self.script[self.i]
            self.i += 1
            return out
        return {"content": "FINAL: script exhausted", "tool_calls": []}


@dataclass
class OpenAIModel:
    """最小 OpenAI 兼容客户端(urllib,无第三方依赖)。"""

    model: str
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    temperature: float = 0.2
    api_key_env: str = "OPENAI_API_KEY"

    def chat(self, messages: list[dict], tools: list[dict]) -> dict:
        key = self.api_key or os.environ.get(self.api_key_env, "")
        if not key:
            raise RuntimeError(f"缺少 API key(环境变量 {self.api_key_env})")
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if tools:
            payload["tools"] = [
                {"type": "function", "function": {
                    "name": t["name"], "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
                }} for t in tools
            ]
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
        msg = data["choices"][0]["message"]
        calls = []
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": fn.get("arguments")}
            calls.append({"name": fn.get("name"), "args": args, "id": tc.get("id")})
        return {"content": msg.get("content") or "", "tool_calls": calls}


class Runner:
    def __init__(self, con, root: str, model: Model, *, registry: Registry | None = None,
                 max_steps: int = 40, timeout: float = 60.0):
        self.con = con
        self.root = root
        self.domain = Domain(con, root)
        self.model = model
        self.registry = registry or default_registry()
        self.max_steps = max_steps
        self.timeout = timeout

    # ---------------- 运行与检查点 ----------------
    def start(self, target: str, goal: str | None = None) -> str:
        t = self.domain.target(target)
        rid = new_id("R")
        now = _now()
        with _db.Tx(self.con) as c:
            c.execute("INSERT INTO runs(id,target_id,state,step,goal,started_at,updated_at)"
                      " VALUES(?,?,'running',0,?,?,?)", (rid, t.id, goal, now, now))
            c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                      (now, "run.started", rid, json.dumps({"target": target, "goal": goal}, ensure_ascii=False)))
        return rid

    def context(self, target: str) -> dict:
        """从权威状态重建上下文(不读 notes.md)。"""
        d = self.domain.derived(target)
        events = [dict(r) for r in self.con.execute(
            "SELECT ts,kind,ref,payload FROM events ORDER BY id DESC LIMIT 20").fetchall()]
        events.reverse()
        return {"derived": d, "recent_events": events}

    def _messages(self, target: str, goal: str | None, history: list[dict]) -> list[dict]:
        ctx = self.context(target)
        head = SYSTEM + "\n\n## 当前状态(程序算出的派生量)\n```json\n" + \
            json.dumps(ctx["derived"], ensure_ascii=False, indent=2) + "\n```\n"
        if goal:
            head += f"\n## 本轮目标\n{goal}\n"
        return [{"role": "system", "content": head}] + history

    def _checkpoint(self, rid: str, step: int, history: list[dict]) -> None:
        with _db.Tx(self.con) as c:
            c.execute("INSERT INTO checkpoints(id,run_id,step,messages,created_at) VALUES(?,?,?,?,?)",
                      (new_id("CK"), rid, step, json.dumps(history, ensure_ascii=False), _now()))
            c.execute("UPDATE runs SET step=?, updated_at=? WHERE id=?", (step, _now(), rid))

    def run(self, rid: str, target: str, goal: str | None = None, history: list[dict] | None = None) -> dict:
        """执行循环。返回 {state, steps, final, history}。

        任何异常(含键盘中断/模型错误)⇒ run 置 paused + 检查点;**不改变假设结论**。
        """
        history = list(history or [])
        step_start = self.con.execute("SELECT step FROM runs WHERE id=?", (rid,)).fetchone()["step"]
        step = step_start
        final = ""
        state = "finished"
        try:
            while step < self.max_steps:
                msgs = self._messages(target, goal, history)
                reply = self.model.chat(msgs, self.registry.describe())
                content = (reply.get("content") or "").strip()
                calls = reply.get("tool_calls") or []
                if content:
                    history.append({"role": "assistant", "content": content})
                if content.startswith("FINAL:"):
                    final = content
                    step += 1
                    self._checkpoint(rid, step, history)
                    break
                if not calls:
                    # 没工具调用也没 FINAL ⇒ 视作本轮表达,继续(避免空转:计一步)
                    step += 1
                    self._checkpoint(rid, step, history)
                    if step >= self.max_steps:
                        state = "paused"
                    continue
                ctx = ToolContext(
                    domain=self.domain, root=self.root, run_id=rid, timeout=self.timeout,
                    artifacts_dir=os.path.join(self.root, "state", "artifacts", rid),
                )
                for call in calls:
                    out = self.registry.call(ctx, call["name"], call.get("args") or {})
                    history.append({"role": "tool", "tool_call_id": call.get("id"),
                                    "name": call["name"], "content": json.dumps(out, ensure_ascii=False)[:8000]})
                    with _db.Tx(self.con) as c:
                        c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                                  (_now(), "tool.called", out.get("tool_call_id"),
                                   json.dumps({"tool": call["name"], "status": out.get("status")}, ensure_ascii=False)))
                step += 1
                self._checkpoint(rid, step, history)
        except BaseException as e:  # 中断 != 研究停止
            state = "paused"
            with _db.Tx(self.con) as c:
                c.execute("UPDATE runs SET state='paused', updated_at=? WHERE id=?", (_now(), rid))
                c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                          (_now(), "run.paused", rid, json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)))
            self._checkpoint(rid, step, history)
            raise
        with _db.Tx(self.con) as c:
            c.execute("UPDATE runs SET state=?, updated_at=? WHERE id=?", (state, _now(), rid))
            c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                      (_now(), "run.finished", rid, json.dumps({"state": state, "steps": step}, ensure_ascii=False)))
        return {"run_id": rid, "state": state, "steps": step, "final": final, "history": history}

    def resume(self, rid: str, goal: str | None = None) -> dict:
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
            c.execute("INSERT INTO events(ts,kind,ref,payload) VALUES(?,?,?,?)",
                      (_now(), "run.resumed", rid, json.dumps({"step": row["step"]}, ensure_ascii=False)))
        return self.run(rid, row["target_name"], goal or row["goal"], history=history)


def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.timezone.utc).isoformat(timespec="seconds")
