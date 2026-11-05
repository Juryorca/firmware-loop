"""工具层:描述式注册 + 真实执行状态。

原则(见任务书):工具**能力开放** —— 由描述与 schema 让模型发现,不用固定技术分类;
本层只管"正确执行 + 记录真实状态 + 证据落盘",不替模型决定研究策略。

执行状态语义(机器协议,不是研究世界):
  pending → running → ok | error | timeout | cancelled
只有 `ok` 才能被当作"命令成功完成";`timeout`/`error` **不能**推出研究结论。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shlex
import subprocess
import time
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # 只用于类型,避免循环导入
    from .research import Research

from . import db as _db
from .domain import Domain, new_id
from .policy import Policy


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[ToolContext, dict], dict]
    risk: str = "low"        # low | high
    # requires: 该类工具需要的授权能力(如 "mcp")。它是**可核的授权门**,不是提示
    requires: str | None = None
    external: bool = False   # 外网/登录态/公开提交:永远需要人类确认(high 需要人类确认,见 Runner)
    # manifest:该工具来自哪个声明(fwcore/toolreg.py);核心工具为 None。仅审计用,运行时不读。
    manifest: object | None = None
    # adapter_instance:该工具的适配器实例(拥有它的 Registry 负责 close;见 toolreg)
    adapter_instance: object | None = None


@dataclass
class ToolContext:
    domain: Domain
    root: str
    run_id: str | None = None
    timeout: float = 60.0
    artifacts_dir: str = ""
    confirm: Callable[[str], bool] | None = None
    policy: Policy = None  # type: ignore[assignment]
    research: Research | None = None    # 工具用它记三镜(见 fwcore/research.py)


@dataclass
class Registry:
    specs: dict[str, ToolSpec] = field(default_factory=dict)
    # 注册报告(manifest 来源):registered / unavailable / skipped。空 = 还没做过 manifest 装载。
    registration: dict = field(default_factory=dict)
    # 已 close 过的特色适配器实例 id(close_feature_adapters 幂等用)
    closed_adapters: set = field(default_factory=set)

    NAME_RE = __import__("re").compile(r"^[A-Za-z0-9_-]{1,64}$")

    def register(self, spec: ToolSpec) -> None:
        # OpenAI 兼容端点要求 function.name 匹配 ^[a-zA-Z0-9_-]+$ ⇒ 注册时挡住
        if not self.NAME_RE.match(spec.name or ""):
            raise ValueError(f"工具名非法(只允许字母数字下划线连字符,≤64): {spec.name!r}")
        if spec.name in self.specs:
            raise ValueError(f"工具重名: {spec.name}")
        self.specs[spec.name] = spec

    def describe(self) -> list[dict]:
        """给模型的工具清单(名称 / 说明 / JSON Schema / 风险级)。"""
        return [
            {"name": s.name, "description": s.description, "input_schema": s.input_schema, "risk": s.risk}
            for s in sorted(self.specs.values(), key=lambda x: x.name)
        ]

    def call(self, ctx: ToolContext, name: str, args: dict, *, call_key: str | None = None,
             run_id: str | None = None) -> dict:
        """执行工具。`call_key`(模型给的调用 id)存在时**幂等**:
        同一 run 内重复调用同一 id 不会重复副作用,直接返回上次结果。"""
        spec = self.specs.get(name)
        if not spec:
            return {"status": "error", "error": f"未知工具: {name}"}
        pol = ctx.policy or Policy(confirm=ctx.confirm)
        allowed, reason = pol.decide(name, spec.risk, args, external=spec.external,
                                     requires=spec.requires)
        if not allowed:
            _record(ctx, name, args, "cancelled", note=reason)
            return {"status": "cancelled", "error": reason}
        bad = pol.check_args(name, spec.input_schema, args)
        if bad:
            _record(ctx, name, args, "error", note=bad)
            return {"status": "error", "error": bad}
        prev = None
        if call_key:
            if ctx.run_id:
                prev = ctx.domain.con.execute(
                    "SELECT * FROM tool_calls WHERE run_id=? AND call_key=?", (ctx.run_id, call_key)
                ).fetchone()
            else:   # 没有 run 归属的临时调用也要幂等,否则同一 key 会重复产生副作用
                prev = ctx.domain.con.execute(
                    "SELECT * FROM tool_calls WHERE run_id IS NULL AND call_key=?", (call_key,)
                ).fetchone()
        if prev and prev["status"] not in ("pending", "running"):
            return {"status": prev["status"], "replayed": True, "tool_call_id": prev["id"],
                    "exit_code": prev["exit_code"], "note": prev["note"],
                    "stdout_path": prev["stdout_path"], "stderr_path": prev["stderr_path"]}
        cid = new_id("TC")
        t0 = time.time()
        try:
            with _db.Tx(ctx.domain.con) as c:
                c.execute(
                    "INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,created_at)"
                    " VALUES(?,?,?,?,?,'running',?)",
                    (cid, call_key, ctx.run_id, name, json.dumps(args, ensure_ascii=False), _now()),
                )
        except Exception as e:      # 唯一索引冲突 = 另一个执行者已在跑同一次调用
            return {"status": "cancelled", "error": f"重复调用被拒(幂等保护): {e}"}
        out: dict[str, Any] = {}
        try:
            out = spec.handler(ctx, args) or {}
            status = out.get("status", "ok")
            if status not in ("ok", "error", "timeout", "cancelled"):
                status = "ok"
        except Exception as e:  # 工具抛异常 = 工具失败,不是研究结论
            out = {"status": "error", "error": f"{type(e).__name__}: {e}"}
            status = "error"
        # 运行被取消时,在飞工具的中断是**取消**而不是工具失败:
        # 语义差别很重要 —— cancelled/timeout 不能当证据,error 会被误读成"工具坏了"
        if status in ("error", "timeout") and ctx.run_id:
            hb = ctx.domain.con.execute("SELECT handback_reason FROM runs WHERE id=?",
                                        (ctx.run_id,)).fetchone()
            if hb and (hb["handback_reason"] or "").startswith("user_stop"):
                status = "cancelled"
                out["error"] = "运行已取消:工具执行被中断"
        dur = int((time.time() - t0) * 1000)
        # 文本类工具(MCP/IDA/Ghidra 等)没有 stdout 文件 ⇒ 把结果本身落盘 + 算哈希,
        # 这样"证据引用某次工具调用"时总有可校验的原始产物,而不是只留在对话里。
        result_path = out.get("result_path")
        result_sha = out.get("result_sha256")
        if not result_path and isinstance(out.get("text"), str) and out["text"]:
            art = ctx.artifacts_dir or os.path.join(ctx.root, "state", "artifacts", ctx.run_id or "local")
            try:
                os.makedirs(art, exist_ok=True)
                result_path = os.path.join(art, f"{cid}.result.txt")
                with open(result_path, "wb") as f:
                    f.write(out["text"].encode("utf-8", errors="replace"))
                result_sha = _sha256(result_path)
            except OSError:
                result_path, result_sha = None, None      # 落盘失败不改工具结论,但如实留空
        observation = out.get("observation")
        with _db.Tx(ctx.domain.con) as c:
            meta = out.get("meta") or {}
            # 失败**原因**要落库:否则事后审计/模型复盘都看不出"是工具坏了、环境不对,还是策略拒绝"
            err_text = None if status == "ok" else str(out.get("error") or out.get("note") or "")[:1000]
            c.execute(
                "UPDATE tool_calls SET status=?, exit_code=?, duration_ms=?, stdout_path=?, stderr_path=?,"
                " provider=?, capability=?, arch=?, env_json=?, result_path=?, result_sha256=?, observation=?,"
                " note=COALESCE(note, ?) WHERE id=?",
                (status, out.get("exit_code"), dur, out.get("stdout_path"), out.get("stderr_path"),
                 meta.get("provider"), meta.get("capability"), meta.get("arch"),
                 json.dumps(meta, ensure_ascii=False) if meta else None,
                 result_path, result_sha,
                 json.dumps(observation, ensure_ascii=False)[:20000] if observation else None,
                 err_text, cid),
            )
        out["status"] = status
        out["tool_call_id"] = cid
        out["duration_ms"] = dur
        return out


def _proc_starttime(pid: int) -> int | None:
    """读 /proc/<pid>/stat 的第 22 个字段(进程启动时间,用来识别 pid 复用)。"""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as f:
            data = f.read()
        tail = data.rsplit(")", 1)[-1].split()
        return int(tail[19])          # stat 里 starttime 是第 22 字段(整体)/ 去掉前两个后第 20 个
    except (OSError, IndexError, ValueError):
        return None


def _kill_group(proc: subprocess.Popen) -> None:
    """杀整个进程组(含子进程),并等待回收。"""
    import signal

    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _record(ctx: ToolContext, tool: str, args: dict, status: str, note: str = "",
            call_key: str | None = None) -> None:
    with _db.Tx(ctx.domain.con) as c:
        c.execute(
            "INSERT INTO tool_calls(id,call_key,run_id,tool,args,status,note,created_at)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (new_id("TC"), call_key, ctx.run_id, tool, json.dumps(args, ensure_ascii=False),
             status, note, _now()),
        )


def _now() -> str:
    import datetime as _d

    return _d.datetime.now(_d.timezone.utc).isoformat(timespec="seconds")


# ---------------- 内置工具 ----------------

def _abs(ctx: ToolContext, p: str) -> str:
    return p if os.path.isabs(p) else os.path.join(ctx.root, p)


def _cancel_requested(ctx) -> bool:
    """运行是否已被取消(用户/外部停止)。长工具靠它做到"取消即停",而不是干等到超时。"""
    if not ctx.run_id:
        return False
    try:
        row = ctx.domain.con.execute("SELECT handback_reason FROM runs WHERE id=?",
                                     (ctx.run_id,)).fetchone()
    except Exception:
        return False
    return bool(row and (row["handback_reason"] or "").startswith("user_stop"))


_SHELL_META = (";", "|", "&&", "||", ">", "<", "$(", "`", "\n", "*", "?", "~")


def _needs_shell(cmd: str) -> bool:
    return any(m in cmd for m in _SHELL_META)


def shell_exec(ctx: ToolContext, args: dict) -> dict:
    """跑一条命令(有超时);stdout/stderr 落盘并算 sha256。

    语义:默认按 argv 直接执行(不经过 shell);若命令里含 shell 语法(`; | && $() > *` 等)
    或显式 `shell=true`,则用 `bash -lc` 执行,并在结果里标注 `shell=true`
    (实测模型天然按 shell 写命令,静默失败会浪费一整轮上下文)。
    """
    cmd = args.get("command")
    if not cmd:
        return {"status": "error", "error": "command 必填"}
    use_shell = bool(args.get("shell")) or _needs_shell(cmd)
    pol = ctx.policy or Policy()
    workdir = pol.resolve_path(ctx.root, args.get("cwd", "."))
    if not os.path.isdir(workdir):
        return {"status": "error", "error": f"cwd 不存在: {args.get('cwd', '.')}"}
    timeout = pol.clamp_timeout(args.get("timeout", ctx.timeout))
    art = ctx.artifacts_dir or os.path.join(ctx.root, "state", "artifacts", ctx.run_id or "local")
    try:
        os.makedirs(art, exist_ok=True)
    except OSError as e:
        return {"status": "error", "error": f"无法创建产物目录: {e}"}
    oid = new_id("OUT")
    op = os.path.join(art, f"{oid}.stdout")
    ep = os.path.join(art, f"{oid}.stderr")
    started = _now()
    try:
        stack = contextlib.ExitStack()
        fo = stack.enter_context(open(op, "wb"))
        fe = stack.enter_context(open(ep, "wb"))
    except OSError as e:
        return {"status": "error", "error": f"无法创建输出文件: {e}"}
    with stack:
        try:
            argv = (["bash", "-lc", cmd] if use_shell
                    else (shlex.split(cmd) if isinstance(cmd, str) else cmd))
            proc = subprocess.Popen(
                argv, cwd=workdir, stdout=fo, stderr=fe,
                start_new_session=True,     # 独立进程组 ⇒ 超时可整组杀掉
            )
            try:                        # 记录 pgid + 启动时间:看护杀组前要核对,防 pid 复用误杀
                pg = os.getpgid(proc.pid)
                with open(os.path.join(art, ".pgids"), "a", encoding="utf-8") as pf:
                    pf.write(f"{pg} {_proc_starttime(proc.pid) or 0}\n")
            except OSError:
                pass
        except FileNotFoundError as e:
            with open(ep, "ab") as fe2:
                fe2.write(f"命令不存在: {e}".encode())
            return {"status": "error", "exit_code": None, "stdout_path": op, "stderr_path": ep,
                    "stdout_sha256": _sha256(op), "stderr_sha256": _sha256(ep)}
        deadline = time.time() + float(timeout)
        try:
            while True:
                slice_s = max(0.02, min(0.2, deadline - time.time()))
                try:
                    proc.wait(timeout=slice_s)
                    code = proc.returncode
                    status = "ok" if code == 0 else (
                        "cancelled" if _cancel_requested(ctx) else "error")
                    break
                except subprocess.TimeoutExpired:
                    if _cancel_requested(ctx):
                        _kill_group(proc)              # 取消:整组杀掉,不留在飞进程
                        with contextlib.suppress(Exception):
                            proc.wait(timeout=2)
                        code, status = None, "cancelled"
                        break
                    if time.time() >= deadline:
                        _kill_group(proc)              # 超时:同理会收拾整组
                        code, status = None, "timeout"
                        break
        except BaseException:               # 中断(KeyboardInterrupt 等)也要收拾干净
            _kill_group(proc)
            raise
    return {
        "status": status, "exit_code": code, "started_at": started, "shell": use_shell,
        "stdout_path": op, "stderr_path": ep,
        "stdout_sha256": _sha256(op), "stderr_sha256": _sha256(ep),
    }


def fs_read(ctx: ToolContext, args: dict) -> dict:
    pol = ctx.policy or Policy()
    try:
        p = pol.resolve_path(ctx.root, args["path"])
    except Exception as e:
        return {"status": "error", "error": f"路径不合法: {e}"}
    if not os.path.isfile(p):
        return {"status": "error", "error": f"文件不存在: {args['path']}"}
    limit = int(args.get("limit", 64_000))
    try:
        with open(p, "rb") as f:
            data = f.read(limit + 1)
    except OSError as e:
        return {"status": "error", "error": f"读文件失败: {e}"}
    truncated = len(data) > limit
    return {
        "status": "ok", "path": args["path"], "bytes": os.path.getsize(p),
        "truncated": truncated, "sha256": _sha256(p),
        "text": data[:limit].decode("utf-8", errors="replace"),
    }


def fs_hash(ctx: ToolContext, args: dict) -> dict:
    pol = ctx.policy or Policy()
    try:
        p = pol.resolve_path(ctx.root, args["path"])
    except Exception as e:
        return {"status": "error", "error": f"路径不合法: {e}"}
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


def _auto_link_tool_call(ctx: ToolContext, path: str | None) -> tuple[str | None, str | None]:
    """按产物路径回溯到产生它的工具调用(只认本 run 内成功的调用)。

    为什么需要:模型经常只给 path,不记得给 tool_call_id ⇒ 证据与执行断链,复核也无从谈起。
    路径匹配是**可复核**的确定性关联(不是猜):把命中的调用 id 记进证据,并在返回里说明。
    """
    if not path:
        return None, None
    try:
        rows = ctx.domain.con.execute(
            "SELECT id, provider, args, result_path, stdout_path FROM tool_calls"
            " WHERE run_id=? AND status='ok' ORDER BY created_at DESC LIMIT 50",
            (ctx.run_id,)).fetchall()
    except Exception:
        return None, None
    target = os.path.abspath(path if os.path.isabs(path) else os.path.join(ctx.root, path))
    rel = os.path.relpath(target, ctx.root)
    for r in rows:
        for cand in (r["result_path"], r["stdout_path"]):
            if cand and os.path.abspath(cand) == target:
                return r["id"], "auto-linked-by-path"
    # 次强证据:该路径出现在某次成功调用的参数里(例如 `echo x > proof.txt` / `cp a b`)
    # —— 仍然是确定性的文本匹配,但比"产物路径完全相等"弱,所以在 link 里标出来
    for r in rows:
        args = r["args"] or ""
        if rel in args or os.path.basename(target) in args:
            return r["id"], "auto-linked-by-args(弱关联:路径出现在命令里)"
    return None, None


def state_add_evidence(ctx: ToolContext, args: dict) -> dict:
    path = args.get("path")
    tid = args.get("tool_call_id")
    link_note = None
    if not tid:
        tid, link_note = _auto_link_tool_call(ctx, path)
    eid = ctx.domain.add_evidence(
        args["target"], args["kind"], path=path, capture=args.get("capture"),
        attempt_id=args.get("attempt_id"), hypothesis_id=args.get("hypothesis_id"),
        version=args.get("version"),
        # 绑定到具体工具调用 ⇒ 自动继承 provider/原始产物,并标为"未复核"
        tool_call_id=tid, source=args.get("source"),
    )
    out = {"status": "ok", "evidence_id": eid, "linked_tool_call_id": tid,
           "verified": False,
           "next": "复核后才算证据:state_verify_evidence(evidence_id, method=recheck-sha256|"
                   "reproduce-attempt|cross-check)"}
    if link_note:
        out["link"] = link_note
    return out


def state_verify_evidence(ctx: ToolContext, args: dict) -> dict:
    """给证据留下可检查的复核记录(自主、可审计):哈希复核/关联复现/交叉验证。"""
    cid = ctx.domain.verify_evidence(args["evidence_id"], args["method"],
                                     artifact=args.get("artifact"),
                                     attempt_id=args.get("attempt_id"),
                                     detail=args.get("detail", ""))
    st = ctx.domain.evidence_status(args["evidence_id"])
    out = {"status": "ok", "check_id": cid, "method": args["method"], "evidence": st}
    # 如果这个证据所属假设的尝试都已结束,就明确说"可以收口了"(数据事实,不替模型选 verdict)
    rows = ctx.domain.con.execute(
        "SELECT hypothesis_id FROM evidence WHERE id=?", (args["evidence_id"],)).fetchone()
    hid = rows["hypothesis_id"] if rows else None
    if hid:
        still = ctx.domain.con.execute(
            "SELECT COUNT(*) n FROM attempts WHERE hypothesis_id=? AND result='running'",
            (hid,)).fetchone()["n"]
        if still == 0:
            out["next"] = (f"该假设的尝试都已结束、证据已复核 ⇒ 可以收口:"
                           f"state_conclude(hypothesis_id='{hid}', verdict=..., scope_note=...)")
        else:
            out["next"] = f"还有 {still} 个实验在 running:先 state_finish_attempt 再收口"
    return out


def state_start_attempt(ctx: ToolContext, args: dict) -> dict:
    aid = ctx.domain.start_attempt(args["hypothesis_id"], args["method"], args.get("params"))
    return {"status": "ok", "attempt_id": aid}


def state_finish_attempt(ctx: ToolContext, args: dict) -> dict:
    ctx.domain.finish_attempt(args["attempt_id"], args["result"], args.get("note", ""),
                              obs_outcome=args.get("obs_outcome"), obs_reason=args.get("obs_reason", ""))
    return {"status": "ok", "attempt_id": args["attempt_id"], "result": args["result"]}


def state_record_mirror(ctx: ToolContext, args: dict) -> dict:
    if ctx.research is None:
        return {"status": "error", "error": "本次运行没有研究载体(research=None),不能记三镜"}
    mid = ctx.research.record_mirror(args["target"], args["mechanism"], args["author"],
                                     args["state"], args.get("new_faces", "无(判据: 同机制未找到新面)"))
    return {"status": "ok", "mirror_id": mid}


def state_request_user_input(ctx: ToolContext, args: dict) -> dict:
    rid = args.get("run_id") or ctx.run_id
    if not rid:
        return {"status": "error", "error": "缺少 run_id"}
    ctx.domain.request_user_input(rid, args["reason"])
    return {"status": "ok", "needs_user_input": args["reason"]}


def state_note_observation(ctx: ToolContext, args: dict) -> dict:
    ctx.domain.note_observation_outcome(args["attempt_id"], args["outcome"], args.get("reason", ""))
    return {"status": "ok", "attempt_id": args["attempt_id"], "outcome": args["outcome"]}


def state_conclude(ctx: ToolContext, args: dict) -> dict:
    cid = ctx.domain.conclude(args["hypothesis_id"], args["verdict"], args["scope_note"])
    return {"status": "ok", "conclusion_id": cid}


def _caps(ctx: ToolContext, args: dict) -> dict:
    from .providers import inventory
    inv = inventory(ctx.root)
    only = args.get("only")
    if only:
        inv = [e for e in inv if only in e["capability"]]
    return {"status": "ok", "capabilities": inv}


def _sha256(path: str) -> str:
    """算文件哈希;读不了就返回空串(**不**让哈希失败变成工具失败),调用方据此判"无产物"。"""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def default_registry() -> Registry:
    r = Registry()
    r.register(ToolSpec(
        "shell_exec", "在工作区里执行一条命令(有超时;输出落盘 + sha256)",
        {"type": "object", "properties": {
            "command": {"type": "string"}, "cwd": {"type": "string"},
            "timeout": {"type": "number"}}, "required": ["command"]},
        shell_exec, risk="high"))
    r.register(ToolSpec(
        "fs_read", "读文件(默认最多 64KB,返回 sha256 与是否截断)",
        {"type": "object", "properties": {
            "path": {"type": "string"}, "limit": {"type": "number"}}, "required": ["path"]},
        fs_read))
    r.register(ToolSpec(
        "fs_hash", "算文件 sha256",
        {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        fs_hash))
    r.register(ToolSpec(
        "state_add_hypothesis", "登记一条研究假设(来源自由文本)",
        {"type": "object", "properties": {
            "target": {"type": "string"}, "title": {"type": "string"},
            "body": {"type": "string"}, "source": {"type": "string"},
            "priority": {"type": "number"}}, "required": ["target", "title"]},
        state_add_hypothesis))
    r.register(ToolSpec(
        "state_add_observation", "登记一条观察(可绑定某次尝试)",
        {"type": "object", "properties": {
            "target": {"type": "string"}, "text": {"type": "string"},
            "attempt_id": {"type": "string"}}, "required": ["target", "text"]},
        state_add_observation))
    r.register(ToolSpec(
        "state_add_evidence",
        "登记证据(必须能定位:path 或 capture),自动绑定版本。给 tool_call_id 可把证据"
        "绑到产生它的工具调用(继承 provider/产物);不给时若 path 正好是某次成功调用的产物,"
        "会自动回溯关联。登记后仍是**未复核**状态",
        {"type": "object", "properties": {
            "target": {"type": "string"}, "kind": {"type": "string"},
            "path": {"type": "string", "description": "产物路径(相对工作区或绝对)"},
            "capture": {"type": "string"},
            "attempt_id": {"type": "string"}, "hypothesis_id": {"type": "string"},
            "version": {"type": "string"}, "tool_call_id": {"type": "string"},
            "source": {"type": "string"}}, "required": ["target", "kind"]},
        state_add_evidence))
    r.register(ToolSpec(
        "state_verify_evidence",
        "复核证据(留下可检查记录后 verified=1):recheck-sha256 重算产物哈希 / "
        "reproduce-attempt 关联 supported 尝试 / cross-check 需 artifact+detail",
        {"type": "object", "properties": {
            "evidence_id": {"type": "string"},
            "method": {"type": "string", "enum": ["recheck-sha256", "reproduce-attempt", "cross-check"]},
            "artifact": {"type": "string"}, "attempt_id": {"type": "string"},
            "detail": {"type": "string"}}, "required": ["evidence_id", "method"]},
        state_verify_evidence))
    r.register(ToolSpec(
        "state_start_attempt", "开始一次尝试(方法自由文本)",
        {"type": "object", "properties": {
            "hypothesis_id": {"type": "string"}, "method": {"type": "string"},
            "params": {"type": "object"}}, "required": ["hypothesis_id", "method"]},
        state_start_attempt))
    r.register(ToolSpec(
        "state_finish_attempt",
        "结束一次尝试。result ∈ supported|refuted|inconclusive|environment_failure|no_signal|cancelled"
        "(后三种不会关闭假设)",
        {"type": "object", "properties": {
            "attempt_id": {"type": "string"}, "result": {"type": "string"},
            "note": {"type": "string"},
            "obs_outcome": {"type": "string", "enum": ["new", "none"]},
            "obs_reason": {"type": "string"}}, "required": ["attempt_id", "result"]},
        state_finish_attempt))
    r.register(ToolSpec(
        "capabilities_list",
        "列出本机可用的研究能力与提供者(含状态:ready/partial/deps_missing/arch_mismatch/not_started)",
        {"type": "object", "properties": {"only": {"type": "string"}}},
        lambda ctx, a: _caps(ctx, a)))
    r.register(ToolSpec(
        "state_record_mirror",
        "记一行三镜刷新(面/格收口时):机制 / 若我是作者最可能漏检哪 / 换状态·主体·版本 / 新面",
        {"type": "object", "properties": {
            "target": {"type": "string"}, "mechanism": {"type": "string"}, "author": {"type": "string"},
            "state": {"type": "string"}, "new_faces": {"type": "string"}},
         "required": ["target", "mechanism", "author", "state"]},
        state_record_mirror))
    r.register(ToolSpec(
        "state_request_user_input",
        "交回控制权:声明确实需要工作区外的东西(账号/样本/真机外的具体外部事物)",
        {"type": "object", "properties": {
            "run_id": {"type": "string"}, "reason": {"type": "string"}}, "required": ["reason"]},
        state_request_user_input))
    r.register(ToolSpec(
        "state_note_observation",
        "双输出的观察一侧:new=已登记观察 / none=无新观察(必须给理由:为什么在预期内)",
        {"type": "object", "properties": {
            "attempt_id": {"type": "string"}, "outcome": {"type": "string", "enum": ["new", "none"]},
            "reason": {"type": "string"}}, "required": ["attempt_id", "outcome"]},
        state_note_observation))
    r.register(ToolSpec(
        "state_conclude", "给假设下结论(必须写适用范围 scope_note)",
        {"type": "object", "properties": {
            "hypothesis_id": {"type": "string"},
            "verdict": {"type": "string", "enum": ["vulnerable", "no_finding", "inconclusive"]},
            "scope_note": {"type": "string"}},
         "required": ["hypothesis_id", "verdict", "scope_note"]},
        state_conclude))
    return r
