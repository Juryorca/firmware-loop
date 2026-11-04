"""工具层:描述式注册 + 真实执行状态。

原则(见任务书):工具**能力开放** —— 由描述与 schema 让模型发现,不用固定技术分类;
本层只管"正确执行 + 记录真实状态 + 证据落盘",不替模型决定研究策略。

执行状态语义(机器协议,不是研究世界):
  pending → running → ok | error | timeout | cancelled
只有 `ok` 才能被当作"命令成功完成";`timeout`/`error` **不能**推出研究结论。
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import sqlite3
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable

from . import db as _db
from .domain import Domain, new_id


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[["ToolContext", dict], dict]
    risk: str = "low"  # low | high(high 需要人类确认,见 Runner)


@dataclass
class ToolContext:
    domain: Domain
    root: str
    run_id: str | None = None
    timeout: float = 60.0
    artifacts_dir: str = ""
    confirm: Callable[[str], bool] | None = None


@dataclass
class Registry:
    specs: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self.specs:
            raise ValueError(f"工具重名: {spec.name}")
        self.specs[spec.name] = spec

    def describe(self) -> list[dict]:
        """给模型的工具清单(名称 / 说明 / JSON Schema / 风险级)。"""
        return [
            {"name": s.name, "description": s.description, "input_schema": s.input_schema, "risk": s.risk}
            for s in sorted(self.specs.values(), key=lambda x: x.name)
        ]

    def call(self, ctx: ToolContext, name: str, args: dict) -> dict:
        spec = self.specs.get(name)
        if not spec:
            return {"status": "error", "error": f"未知工具: {name}"}
        if spec.risk == "high" and ctx.confirm and not ctx.confirm(f"{name} {json.dumps(args, ensure_ascii=False)}"):
            return {"status": "cancelled", "error": "用户未确认高风险操作"}
        cid = new_id("TC")
        t0 = time.time()
        with _db.Tx(ctx.domain.con) as c:
            c.execute(
                "INSERT INTO tool_calls(id,run_id,tool,args,status,created_at) VALUES(?,?,?,?,'running',?)",
                (cid, ctx.run_id, name, json.dumps(args, ensure_ascii=False), _now()),
            )
        try:
            out = spec.handler(ctx, args) or {}
            status = out.get("status", "ok")
            if status not in ("ok", "error", "timeout", "cancelled"):
                status = "ok"
        except Exception as e:  # 工具抛异常 = 工具失败,不是研究结论
            out = {"status": "error", "error": f"{type(e).__name__}: {e}"}
            status = "error"
        dur = int((time.time() - t0) * 1000)
        with _db.Tx(ctx.domain.con) as c:
            c.execute(
                "UPDATE tool_calls SET status=?, exit_code=?, duration_ms=?, stdout_path=?, stderr_path=? WHERE id=?",
                (status, out.get("exit_code"), dur, out.get("stdout_path"), out.get("stderr_path"), cid),
            )
        out["status"] = status
        out["tool_call_id"] = cid
        out["duration_ms"] = dur
        return out


def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.timezone.utc).isoformat(timespec="seconds")


# ---------------- 内置工具 ----------------

def _abs(ctx: ToolContext, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(ctx.root, p)


def shell_exec(ctx: ToolContext, args: dict) -> dict:
    """跑一条命令(有超时);stdout/stderr 落盘并算 sha256。"""
    cmd = args.get("command")
    if not cmd:
        return {"status": "error", "error": "command 必填"}
    workdir = _abs(ctx, args.get("cwd", "."))
    timeout = float(args.get("timeout", ctx.timeout))
    os.makedirs(ctx.artifacts_dir, exist_ok=True)
    oid = new_id("OUT")
    op = os.path.join(ctx.artifacts_dir, f"{oid}.stdout")
    ep = os.path.join(ctx.artifacts_dir, f"{oid}.stderr")
    started = _now()
    with open(op, "wb") as fo, open(ep, "wb") as fe:
        try:
            proc = subprocess.run(
                shlex.split(cmd) if isinstance(cmd, str) else cmd,
                cwd=workdir, stdout=fo, stderr=fe, timeout=timeout,
            )
            code, status = proc.returncode, "ok" if proc.returncode == 0 else "error"
        except subprocess.TimeoutExpired:
            code, status = None, "timeout"
        except FileNotFoundError as e:
            code, status = None, "error"
            with open(ep, "ab") as fe2:
                fe2.write(f"命令不存在: {e}".encode())
    return {
        "status": status, "exit_code": code, "started_at": started,
        "stdout_path": op, "stderr_path": ep,
        "stdout_sha256": _sha256(op), "stderr_sha256": _sha256(ep),
    }


def fs_read(ctx: ToolContext, args: dict) -> dict:
    p = _abs(ctx, args["path"])
    if not os.path.isfile(p):
        return {"status": "error", "error": f"文件不存在: {args['path']}"}
    limit = int(args.get("limit", 64_000))
    with open(p, "rb") as f:
        data = f.read(limit + 1)
    truncated = len(data) > limit
    return {
        "status": "ok", "path": args["path"], "bytes": os.path.getsize(p),
        "truncated": truncated, "sha256": _sha256(p),
        "text": data[:limit].decode("utf-8", errors="replace"),
    }


def fs_hash(ctx: ToolContext, args: dict) -> dict:
    p = _abs(ctx, args["path"])
    if not os.path.isfile(p):
        return {"status": "error", "error": f"文件不存在: {args['path']}"}
    return {"status": "ok", "sha256": _sha256(p), "bytes": os.path.getsize(p)}


def state_add_hypothesis(ctx: ToolContext, args: dict) -> dict:
    hid = ctx.domain.add_hypothesis(
        args["target"], args["title"], body=args.get("body"),
        source=args.get("source", "agent"), priority=int(args.get("priority", 100)),
    )
    return {"status": "ok", "hypothesis_id": hid}


def state_add_observation(ctx: ToolContext, args: dict) -> dict:
    oid = ctx.domain.add_observation(args["target"], args["text"], attempt_id=args.get("attempt_id"))
    return {"status": "ok", "observation_id": oid}


def state_add_evidence(ctx: ToolContext, args: dict) -> dict:
    eid = ctx.domain.add_evidence(
        args["target"], args["kind"], path=args.get("path"), capture=args.get("capture"),
        attempt_id=args.get("attempt_id"), hypothesis_id=args.get("hypothesis_id"),
        version=args.get("version"),
    )
    return {"status": "ok", "evidence_id": eid}


def state_start_attempt(ctx: ToolContext, args: dict) -> dict:
    aid = ctx.domain.start_attempt(args["hypothesis_id"], args["method"], args.get("params"))
    return {"status": "ok", "attempt_id": aid}


def state_finish_attempt(ctx: ToolContext, args: dict) -> dict:
    ctx.domain.finish_attempt(args["attempt_id"], args["result"], args.get("note", ""))
    return {"status": "ok", "attempt_id": args["attempt_id"], "result": args["result"]}


def state_conclude(ctx: ToolContext, args: dict) -> dict:
    cid = ctx.domain.conclude(args["hypothesis_id"], args["verdict"], args["scope_note"])
    return {"status": "ok", "conclusion_id": cid}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def default_registry() -> Registry:
    r = Registry()
    r.register(ToolSpec(
        "shell.exec", "在工作区里执行一条命令(有超时;输出落盘 + sha256)",
        {"type": "object", "properties": {
            "command": {"type": "string"}, "cwd": {"type": "string"},
            "timeout": {"type": "number"}}, "required": ["command"]},
        shell_exec, risk="high"))
    r.register(ToolSpec(
        "fs.read", "读文件(默认最多 64KB,返回 sha256 与是否截断)",
        {"type": "object", "properties": {
            "path": {"type": "string"}, "limit": {"type": "number"}}, "required": ["path"]},
        fs_read))
    r.register(ToolSpec(
        "fs.hash", "算文件 sha256",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        fs_hash))
    r.register(ToolSpec(
        "state.add_hypothesis", "登记一条研究假设(来源自由文本)",
        {"type": "object", "properties": {
            "target": {"type": "string"}, "title": {"type": "string"},
            "body": {"type": "string"}, "source": {"type": "string"},
            "priority": {"type": "number"}}, "required": ["target", "title"]},
        state_add_hypothesis))
    r.register(ToolSpec(
        "state.add_observation", "登记一条观察(可绑定某次尝试)",
        {"type": "object", "properties": {
            "target": {"type": "string"}, "text": {"type": "string"},
            "attempt_id": {"type": "string"}}, "required": ["target", "text"]},
        state_add_observation))
    r.register(ToolSpec(
        "state.add_evidence", "登记证据(必须能定位:path 或 capture),自动绑定版本",
        {"type": "object", "properties": {
            "target": {"type": "string"}, "kind": {"type": "string"},
            "path": {"type": "string"}, "capture": {"type": "string"},
            "attempt_id": {"type": "string"}, "hypothesis_id": {"type": "string"},
            "version": {"type": "string"}}, "required": ["target", "kind"]},
        state_add_evidence))
    r.register(ToolSpec(
        "state.start_attempt", "开始一次尝试(方法自由文本)",
        {"type": "object", "properties": {
            "hypothesis_id": {"type": "string"}, "method": {"type": "string"},
            "params": {"type": "object"}}, "required": ["hypothesis_id", "method"]},
        state_start_attempt))
    r.register(ToolSpec(
        "state.finish_attempt",
        "结束一次尝试。result ∈ supported|refuted|inconclusive|environment_failure|no_signal|cancelled"
        "(后三种不会关闭假设)",
        {"type": "object", "properties": {
            "attempt_id": {"type": "string"}, "result": {"type": "string"},
            "note": {"type": "string"}}, "required": ["attempt_id", "result"]},
        state_finish_attempt))
    r.register(ToolSpec(
        "state.conclude", "给假设下结论(必须写适用范围 scope_note)",
        {"type": "object", "properties": {
            "hypothesis_id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["vulnerable", "no_finding", "inconclusive"]},
            "scope_note": {"type": "string"}},
         "required": ["hypothesis_id", "verdict", "scope_note"]},
        state_conclude))
    return r
