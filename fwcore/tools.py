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
import selectors
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
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
        # 工具文本结果留在 tool result/session;不自动写 workspace artifact。
        # 需要证据时由 Agent 用 bash 保存,或显式传 capture/path。
        result_path = out.get("result_path")
        result_sha = out.get("result_sha256")
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
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


def _capture_process(argv: list[str], *, cwd: str, env: dict[str, str] | None,
                     timeout: float, cancel_check: Callable[[], bool] | None = None,
                     max_bytes: int = 8 * 1024 * 1024,
                     tail_chars: int = 2000, pgid_dir: str | None = None) -> dict:
    """执行一个进程并把输出留在内存(Pi 风格);不创建 stdout/stderr 文件。

    只做进程生命周期与有界输出:
      - 独立进程组;超时/取消整组杀掉;
      - 每个流保留最多 max_bytes 前缀供调用方解析;
      - 模型看 tail_chars 尾部;更长时标记 truncated;
      - 不写 workspace artifact;需要长期保存由 Agent 自己用 bash 重定向。
    """
    import hashlib

    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env={**os.environ, **(env or {})},
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, bufsize=0,
        )
    except FileNotFoundError as e:
        return {"status": "error", "exit_code": None, "error": f"入口不存在: {argv[0]!r}",
                "stdout": "", "stderr": str(e), "stdout_tail": "", "stderr_tail": str(e)}
    except OSError as e:
        return {"status": "error", "exit_code": None, "error": f"启动失败: {e}",
                "stdout": "", "stderr": str(e), "stdout_tail": "", "stderr_tail": str(e)}

    if pgid_dir:
        try:
            os.makedirs(pgid_dir, exist_ok=True)
            with open(os.path.join(pgid_dir, ".pgids"), "a", encoding="utf-8") as pf:
                pf.write(f"{os.getpgid(proc.pid)} {_proc_starttime(proc.pid) or 0}\n")
        except OSError:
            pass

    sel = selectors.DefaultSelector()
    assert proc.stdout is not None and proc.stderr is not None
    sel.register(proc.stdout, selectors.EVENT_READ, "stdout")
    sel.register(proc.stderr, selectors.EVENT_READ, "stderr")
    bufs = {"stdout": bytearray(), "stderr": bytearray()}
    tails = {"stdout": bytearray(), "stderr": bytearray()}
    totals = {"stdout": 0, "stderr": 0}
    hashes = {"stdout": hashlib.sha256(), "stderr": hashlib.sha256()}
    truncated = {"stdout": False, "stderr": False}
    tail_limit = max(256, tail_chars * 4)
    timed_out = False
    cancelled = False
    deadline = time.time() + float(timeout)

    def _read(stream: str, data: bytes) -> None:
        totals[stream] += len(data)
        hashes[stream].update(data)
        room = max(0, max_bytes - len(bufs[stream]))
        if room:
            bufs[stream].extend(data[:room])
        if len(data) > room:
            truncated[stream] = True
        tails[stream].extend(data)
        if len(tails[stream]) > tail_limit * 4:
            del tails[stream][: len(tails[stream]) - tail_limit]

    try:
        while True:
            if cancel_check and cancel_check():
                cancelled = True
                _kill_group(proc)
                break
            now = time.time()
            if now >= deadline and proc.poll() is None:
                timed_out = True
                _kill_group(proc)
                break
            if not sel.get_map():
                break
            wait = max(0.02, min(0.2, deadline - now))
            events = sel.select(wait)
            for key, _mask in events:
                stream = key.data
                try:
                    data = os.read(key.fd, 65536)
                except OSError:
                    data = b""
                if not data:
                    # EOF:解除 selector 即可;fd 由 Popen 的 pipe 对象负责收尾。
                    with contextlib.suppress(Exception):
                        sel.unregister(key.fd)
                    continue
                _read(stream, data)
            if proc.poll() is not None and not sel.get_map():
                break
    except BaseException:
        _kill_group(proc)
        raise
    finally:
        with contextlib.suppress(Exception):
            sel.close()
    code = proc.wait()
    if cancelled:
        status = "cancelled"
    elif timed_out:
        status = "timeout"
    else:
        status = "ok" if code == 0 else "error"

    def _decode(stream: str) -> str:
        data = bytes(bufs[stream])
        return data.decode("utf-8", errors="replace")

    out = {"status": status, "exit_code": code,
           "stdout": _decode("stdout"), "stderr": _decode("stderr")}
    for stream in ("stdout", "stderr"):
        data = bytes(tails[stream])
        binary = b"\x00" in data
        if binary:
            out[f"{stream}_tail"] = f"[binary output: {totals[stream]} bytes; redirect to a file / use xxd/strings]"
        else:
            out[f"{stream}_tail"] = data.decode("utf-8", errors="replace")[-tail_chars:]
        out[f"{stream}_truncated"] = bool(truncated[stream] or totals[stream] > tail_limit)
        out[f"{stream}_binary"] = binary
        out[f"{stream}_bytes"] = totals[stream]
        out[f"{stream}_sha256"] = hashes[stream].hexdigest()
    out["output_truncated"] = bool(out["stdout_truncated"] or out["stderr_truncated"])
    return out


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

    return _d.datetime.now(_d.UTC).isoformat(timespec="seconds")


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


# 只有**改变命令结构**的语法才需要 shell;`* ? ~` 是普通字符(是否展开由调用方决定),
# 不能因为文件名里有 `?` 就换成 shell 语义(那会意外改变含义)。
_SHELL_SYNTAX = (";", "|", "&&", "||", ">", "<", "$(", "`", "\n")


def _needs_shell(cmd: str) -> bool:
    return any(m in cmd for m in _SHELL_SYNTAX)


def shell_exec(ctx: ToolContext, args: dict) -> dict:
    """跑一条命令(有超时);输出留在内存,不自动落 workspace artifact。

    语义:
    - 默认按 argv 直接执行;含 shell 语法(`; | && $() >` 等)或显式 mode=shell 时用 `bash -lc`;
    - `mode=auto` 的 `* ? ~` 是普通字符,需要 glob/tilde 展开时显式 mode=shell;
    - 只返回有界尾部 + 完整输出的哈希/字节数;需要长期保存由 Agent 自己重定向到文件。
    """
    cmd = args.get("command")
    if not cmd:
        return {"status": "error", "error": "command 必填"}
    mode = str(args.get("mode") or "auto").lower()
    if isinstance(cmd, list):
        mode = "argv"
    if mode not in ("auto", "shell", "argv"):
        return {"status": "error", "error": f"mode 非法: {mode}(允许 auto|shell|argv)"}
    use_shell = (mode == "shell") or (
        mode == "auto" and isinstance(cmd, str) and _needs_shell(cmd))
    pol = ctx.policy or Policy()
    workdir = pol.resolve_path(ctx.root, args.get("cwd", "."))
    if not os.path.isdir(workdir):
        return {"status": "error", "error": f"cwd 不存在: {args.get('cwd', '.')}"}
    timeout = pol.clamp_timeout(args.get("timeout", ctx.timeout))
    # 仅为取消/看护登记 .pgids;不建 stdout/stderr 文件。
    art = ctx.artifacts_dir or os.path.join(ctx.root, "state", "artifacts", ctx.run_id or "local")
    try:
        argv = (["bash", "-lc", cmd] if use_shell
                else (shlex.split(cmd) if isinstance(cmd, str) else cmd))
    except ValueError as e:
        return {"status": "error", "error": f"命令解析失败: {e}"}
    started = _now()
    out: dict[str, Any] = _capture_process(argv, cwd=workdir, env=None, timeout=timeout,
                                           cancel_check=lambda: _cancel_requested(ctx),
                                           pgid_dir=art)
    # 模型/会话只需要有界尾部;完整原文不塞进结果,避免重复保存。
    out.pop("stdout", None)
    out.pop("stderr", None)
    out.update({"shell": use_shell, "mode": mode, "started_at": started})
    status = str(out.get("status") or "")
    if status != "ok":
        tail = (out.get("stderr_tail") or out.get("stdout_tail") or "").strip()
        out["error"] = {"timeout": f"超时({int(timeout)}s 上限)",
                        "cancelled": "运行被取消,工具被中断"}.get(
                            status, f"退出码 {out.get('exit_code')}" + (f": {tail}" if tail else ""))
    return out


_INTERNAL_PREFIXES = ("state/fwcore.sqlite3", "state/artifacts/", "state/ida/",
                      "state/_scratch/")


def _is_internal(rel: str) -> bool:
    """`state/` 下的框架内部文件(状态库/产物目录)不是研究对象。"""
    norm = rel.replace(os.sep, "/").lstrip("./")
    return any(norm == p.rstrip("/") or norm.startswith(p) for p in _INTERNAL_PREFIXES)


def fs_read(ctx: ToolContext, args: dict) -> dict:
    pol = ctx.policy or Policy()
    try:
        p = pol.resolve_path(ctx.root, args["path"])
    except Exception as e:
        return {"status": "error", "error": f"路径不合法: {e}"}
    rel = os.path.relpath(p, ctx.root)
    if _is_internal(rel):
        return {"status": "error",
                "error": f"{rel} 是框架内部文件(状态库/产物目录),不是研究对象。"
                         "看汇总用 research_derived / research_queue;读工具输出用返回里的 "
                         "stdout_tail;二进制内容用 shell_exec 的 strings/file 而不是 fs_read"}
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
    rel = os.path.relpath(p, ctx.root)
    if _is_internal(rel):
        return {"status": "error",
                "error": f"{rel} 是框架内部文件(状态库/产物目录),不是研究对象。"
                         "看汇总用 research_derived / research_queue;读工具输出用返回里的 "
                         "stdout_tail;二进制内容用 shell_exec 的 strings/file 而不是 fs_read"}
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
    oid = ctx.domain.add_observation(
        args["target"], args["text"], attempt_id=args.get("attempt_id"),
        tool_call_id=args.get("tool_call_id"), path=args.get("path"))
    return {"status": "ok", "observation_id": oid,
            "source": {"tool_call_id": args.get("tool_call_id"), "path": args.get("path")}}


def state_add_log(ctx: ToolContext, args: dict) -> dict:
    lid = ctx.domain.add_log(args["target"], args["kind"], args["title"],
                             args.get("body", ""), source=args.get("source"),
                             needs_review=1 if args.get("needs_review") else 0)
    return {"status": "ok", "log_id": lid, "view": "fw-core view <target>"}


RELATION_BY_TOOL = {"fs_read": "read", "fs_hash": "hash",
                     "shell_exec": "execution", "ida_query": "decompiled",
                     "ghidra_query": "decompiled", "fw_extract": "extracted"}


def _auto_link_tool_call(ctx: ToolContext, path: str | None) -> tuple[str | None, str | None, str | None]:
    """按产物路径回溯到产生它的工具调用。返回 (tool_call_id, relation, weak_hint)。

    **只把"直接产出"当作关联**:
      - 证据路径 == 某次调用的 stdout/result 路径 ⇒ 直接来源,写 tool_call_id;
      - 路径只出现在命令参数里(如 `cat a > b`)⇒ 只是**弱线索**:不写 tool_call_id,
        返回提示串写进 notes(保留未关联状态,后续可补)。
    """
    if not path:
        return None, None, None
    rows = ctx.domain.con.execute(
        "SELECT id, tool, args, result_path, stdout_path FROM tool_calls"
        " WHERE run_id=? AND status='ok' ORDER BY created_at DESC LIMIT 50",
        (ctx.run_id,)).fetchall()
    target = os.path.abspath(path if os.path.isabs(path) else os.path.join(ctx.root, path))
    for r in rows:
        for out_path, direct_rel in ((r["result_path"], "tool-result"),
                                     (r["stdout_path"], "execution-output")):
            if out_path and os.path.abspath(out_path) == target:
                relation = RELATION_BY_TOOL.get(r["tool"], "tool-output") \
                    if direct_rel == "execution-output" else direct_rel
                return r["id"], relation, None
    rel_path = os.path.relpath(target, ctx.root)
    base = os.path.basename(target)
    for r in rows:
        args = r["args"] or ""
        if rel_path in args or base in args:
            return None, None, f"weak-args:路径出现在 {r['id']} 的参数里(不是直接产出)"
    return None, None, None


def state_add_evidence(ctx: ToolContext, args: dict) -> dict:
    path = args.get("path")
    tid = args.get("tool_call_id")
    relation = None
    weak_hint = None
    if not tid:
        tid, relation, weak_hint = _auto_link_tool_call(ctx, path)
    eid = ctx.domain.add_evidence(
        args["target"], args["kind"], path=path, capture=args.get("capture"),
        attempt_id=args.get("attempt_id"), hypothesis_id=args.get("hypothesis_id"),
        version=args.get("version"),
        # 绑定到具体工具调用:Domain 会校验状态、继承 source/产物路径/哈希,并标"未复核"
        tool_call_id=tid, source=args.get("source"),
    )
    # 关系由**路径证据**判定(与 state_link_evidence 同一条规则):
    # stdout/result ⇒ 直接;只在命令参数里 ⇒ command-write(弱);证不出 ⇒ 不写关系(不猜)
    link_relation = None
    out_link = ""
    if tid:
        try:
            info = ctx.domain.link_evidence_source(eid, tid, note="建证时按路径判定来源")
            link_relation = info.get("relation")
            out_link = f"{link_relation}:{tid}"
        except Exception as e:
            weak_hint = f"relation-unproven: {str(e)[:120]}"
    out = {"status": "ok", "evidence_id": eid, "linked_tool_call_id": tid,
           "relation": relation, "link_relation": link_relation, "verified": False,
           "next": "复核后才算证据:state_verify_evidence(evidence_id, method=recheck-sha256|"
                   "reproduce-attempt|cross-check)"}
    if link_relation:
        out["link"] = out_link
    if weak_hint:
        out["link"] = weak_hint
        if tid:
            out["note"] = ("已绑定工具调用,但来源**关系**无法按路径证明 ⇒ 不写关系"
                           "(弱于直接产出,复核时按 command-write 处理)")
        else:
            out["note"] = ("未建立来源关联(弱线索);如需确定来源,补充 tool_call_id 或"
                           "用产生该文件的那次调用的产物路径")
            out["next"] = ("补来源:state_add_evidence(..., tool_call_id=<产生该文件的那次调用 id>);"
                           "工具结果里的字段是 db_tool_call_id(数据库 id,不是协议 call_…)")
    return out


def state_link_evidence(ctx: ToolContext, args: dict) -> dict:
    """给**已有**证据补来源(保留 E-ID/哈希/复核历史;不生成重复证据)。"""
    res = ctx.domain.link_evidence_source(args["evidence_id"], args["tool_call_id"],
                                          note=args.get("note", ""))
    out = {"status": "ok", **res}
    if not res["direct"]:
        out["note"] = ("弱来源(command-write):路径只出现在命令里,不能当直接产出证据;"
                       "要强来源请用产生该文件的调用的 stdout 路径重新登记或补充更精确的来源")
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
    return {"status": "ok", "attempt_id": args["attempt_id"], "result": args["result"],
            "next": "若结论要靠这次实验:证据需绑定来源(tool_call_id 或工具 stdout 路径),"
                    "并用 state_verify_evidence 做因果复核后再 state_conclude"}


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
    from .toolmeta import ToolConfigError
    try:
        inv = inventory(ctx.root)
    except ToolConfigError as e:
        return {"status": "error", "error": f"工具元数据配置错误: {e}"}
    only = args.get("only")
    if only:
        inv = [e for e in inv if only in e["capability"]]
    return {"status": "ok", "capabilities": inv}


TAIL_CHARS = 2000


def _tail_info(path: str, max_chars: int = TAIL_CHARS) -> tuple[str, bool, bool]:
    """返回 (尾部文本, 是否截断, 是否二进制)。只读文件尾部,不把大输出塞进上下文。"""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            f.seek(max(0, size - max_chars * 4))     # 多读一点以处理多字节字符
            data = f.read()
    except OSError:
        return "", False, False
    binary = b"\x00" in data
    if binary:
        # 二进制不塞进上下文:只给占位,让模型用路径/xxd/strings 处理
        return (f"[binary output: {size} bytes; use stdout_tail / redirect to a file / xxd/strings]",
                size > max_chars, True)
    text = data.decode("utf-8", errors="replace")
    return text[-max_chars:], (len(text) > max_chars or size > max_chars * 4), False


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
        "shell_exec",
        "在工作区里执行一条命令(有超时;有界输出 + sha256,不自动落 artifacts)。"
        "mode=auto(默认):含真正 shell 语法(; | && || > < $( ) ` 换行)时用 bash -lc,否则按 argv 执行;"
        "mode=shell 强制 bash -lc;mode=argv 强制直接执行(不解释语法)。`* ? ~` 不触发 shell 模式",
        {"type": "object", "properties": {
            "command": {"type": "string", "description": "命令字符串或 argv 数组"},
            "mode": {"type": "string", "enum": ["auto", "shell", "argv"],
                     "description": "auto=按语法判断;shell=bash -lc;argv=直接执行"},
            "cwd": {"type": "string"},
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
        "state_add_observation",
        "登记一条**已确认但还不足以成假设**的技术事实(地址/字符串/调用关系);"
        "可带来源:tool_call_id 用工具结果里的 db_tool_call_id,path 指向该调用产出的文件",
        {"type": "object", "properties": {
            "target": {"type": "string"}, "text": {"type": "string"},
            "attempt_id": {"type": "string"},
            "tool_call_id": {"type": "string", "description": "来源:db_tool_call_id(TC-…)"},
            "path": {"type": "string", "description": "来源产物路径(相对工作区)"}},
         "required": ["target", "text"]},
        state_add_observation))
    r.register(ToolSpec(
        "state_add_log",
        "登记研究日志(逐轮记录/停止声明/下一步/决策/待核对叙述);研究事实用结构化工具,"
        "这里只放无法结构化但必须保留的叙述",
        {"type": "object", "properties": {
            "target": {"type": "string"},
            "kind": {"type": "string",
                     "enum": ["round", "stop", "next", "decision", "note", "needs_review"]},
            "title": {"type": "string"}, "body": {"type": "string"},
            "source": {"type": "string"}, "needs_review": {"type": "boolean"}},
         "required": ["target", "kind", "title"]},
        state_add_log))
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
        "state_link_evidence",
        "给已有证据补来源(校验同 target / 调用 ok / 路径关系);不新建证据,保留 E-ID、"
        "SHA256 与复核历史。direct-stdout/direct-result 是强来源,command-write 是弱来源",
        {"type": "object", "properties": {
            "evidence_id": {"type": "string"}, "tool_call_id": {"type": "string"},
            "note": {"type": "string"}}, "required": ["evidence_id", "tool_call_id"]},
        state_link_evidence))
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

    def _kb_search(ctx, a):
        from .knowledge import search
        try:
            rows = search(ctx.domain.con, str(a.get("query") or ""),
                          kind=a.get("kind"), status=a.get("status"),
                          limit=int(a.get("limit") or 20))
            return {"status": "ok", "results": rows, "count": len(rows),
                    "note": "历史知识只作参考，不是当前 target 的证据；需要全文用 knowledge_get。"}
        except (ValueError, TypeError) as e:
            return {"status": "error", "error": str(e)}

    def _kb_get(ctx, a):
        from .knowledge import get
        out = get(ctx.domain.con, str(a.get("entry_id") or ""))
        return ({"status": "ok", "entry": out} if out
                else {"status": "error", "error": f"未找到知识条目: {a.get('entry_id')}"})

    def _kb_propose(ctx, a):
        from .knowledge import propose
        try:
            out = propose(ctx.domain.con, kind=str(a.get("kind") or ""),
                          title=str(a.get("title") or ""), body_md=str(a.get("body_md") or ""),
                          tags=a.get("tags") or [], source=a.get("source"))
            return {"status": "ok", "entry": out,
                    "note": "已直接记录为 approved；Agent 自己判断是否值得沉淀，不设中间审核态。"}
        except (ValueError, TypeError, KeyError) as e:
            return {"status": "error", "error": str(e)}

    r.register(ToolSpec(
        "knowledge_search",
        "检索长期研究知识（pattern/misreport/guideline）；返回摘要与 ID；历史知识不是当前目标证据",
        {"type": "object", "properties": {
            "query": {"type": "string"},
            "kind": {"type": "string", "enum": ["pattern", "misreport", "guideline"]},
            "status": {"type": "string", "enum": ["approved", "deprecated", "superseded"]},
            "limit": {"type": "number"}}, "required": ["query"]},
        _kb_search))
    def _kb_stats(ctx, a):
        from .knowledge import stats
        return {"status": "ok", "stats": stats(ctx.domain.con)}

    def _kb_revise(ctx, a):
        from .knowledge import revise
        try:
            out = revise(ctx.domain.con, str(a.get("entry_id") or ""),
                         title=a.get("title"), body_md=a.get("body_md"),
                         change_note=str(a.get("change_note") or ""))
            return {"status": "ok", "entry": out,
                    "note": "已修订并追加版本；默认直接 approved。"}
        except (KeyError, ValueError, TypeError) as e:
            return {"status": "error", "error": str(e)}

    def _kb_deprecate(ctx, a):
        from .knowledge import set_status
        try:
            return {"status": "ok",
                    "entry": set_status(ctx.domain.con, str(a.get("entry_id") or ""),
                                       "deprecated")}
        except (KeyError, ValueError, TypeError) as e:
            return {"status": "error", "error": str(e)}

    r.register(ToolSpec(
        "knowledge_get", "读取一条知识的完整正文、标签和版本历史",
        {"type": "object", "properties": {"entry_id": {"type": "string"}},
         "required": ["entry_id"]}, _kb_get))
    r.register(ToolSpec(
        "knowledge_stats", "Research Knowledge 统计（总数/kind/status）",
        {"type": "object", "properties": {}}, _kb_stats))
    r.register(ToolSpec(
        "knowledge_revise", "修订知识正文/标题并追加版本（直接 approved，保留历史）",
        {"type": "object", "properties": {
            "entry_id": {"type": "string"}, "title": {"type": "string"},
            "body_md": {"type": "string"}, "change_note": {"type": "string"}},
         "required": ["entry_id"]}, _kb_revise))
    r.register(ToolSpec(
        "knowledge_deprecate", "把不再适用的知识标为 deprecated（不删除历史）",
        {"type": "object", "properties": {"entry_id": {"type": "string"}},
         "required": ["entry_id"]}, _kb_deprecate))
    r.register(ToolSpec(
        "knowledge_propose", "直接记录一条 Agent 判断为跨目标可复用的知识（approved；无中间审核态）",
        {"type": "object", "properties": {
            "kind": {"type": "string", "enum": ["pattern", "misreport", "guideline"]},
            "title": {"type": "string"}, "body_md": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
            "source": {"type": "string"}},
         "required": ["kind", "title", "body_md"]}, _kb_propose))
    from .skills import register_tools as _register_skill_tools
    _register_skill_tools(r, "")
    return r
