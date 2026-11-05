"""统一工具注册:声明式 manifest + 适配器 + 生命周期契约。

为什么要这一层
- Registry(`tools.py`)本身是对的:它只管"执行一次工具调用 + 记录真实状态"。
  问题是"新增工具要改框架代码":`toolkit.py` 里每个研究工具都是一段硬编码 lambda。
- 这一层把**接入方式**变成数据(manifest)+ 少量通用适配器;新增"已有执行类型"的工具
  只需要写一个 JSON(和必要时的适配器/解析器),不动 Agent Loop、不改中央清单。

契约(所有工具一致,不管背后是什么)
  identity      name / description / capability / provider
  schema        模型可见的 JSON Schema(参数校验在 Registry 里统一做)
  invocation    python:函数 / script:argv / feature:深度适配(如 Windows IDA)
  lifecycle     ensure(ctx) 懒启动;close() 收尾;健康状态可查
  semantics     status ∈ ok|error|timeout|cancelled;失败不外溢成"研究结论"
  outputs       原始产物落盘 + sha256 + 结构化 observation + provider/capability 元数据

刻意不做的事:不统一工具**内部实现**,不做插件市场,不做自动安装(那是后续"工具适配 Agent")。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib
import json
import os
import re
import shlex
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .tools import Registry, ToolContext, ToolSpec

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# python 适配器只能从这些包前缀里导入(manifest 是工作区文件,但也不该成为任意代码加载器;
# manifest 来自不受信来源时可以额外收紧/放宽:`FW_TOOL_ADAPTER_PREFIXES=fwcore.,myadapters.`)
ADAPTER_PREFIXES = ("fwcore.", "fwadapters.", "tests.")


def _adapter_prefixes() -> tuple[str, ...]:
    env = os.environ.get("FW_TOOL_ADAPTER_PREFIXES")
    if env:
        return tuple(p.strip() + "." if not p.strip().endswith(".") else p.strip()
                     for p in env.split(",") if p.strip())
    return ADAPTER_PREFIXES


def _kill_group(proc: subprocess.Popen) -> None:
    """杀掉整个进程组(工具自己起的孩子也要收),再兜底 kill 本体。"""
    import signal

    with contextlib.suppress(Exception):
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    with contextlib.suppress(Exception):
        proc.kill()
KINDS = ("python", "script", "feature")


class ManifestError(ValueError):
    """manifest 不合法(加载期就报,不让坏定义进 Registry)。"""


# ---------------------------------------------------------------- manifest

@dataclass
class Manifest:
    name: str
    description: str
    input_schema: dict
    kind: str
    adapter: str | None = None            # python: "pkg.mod:func";feature: 适配器名(如 "ida")
    argv: list[str] | None = None         # script: ["bash", "tools/x.sh", "{args}"]
    split_args: list[str] = field(default_factory=list)   # 这些占位符按 shell 规则拆成多个 argv
    timeout: float = 60.0
    timeout_arg: str | None = None        # 从参数里取超时(如 "timeout")
    risk: str = "low"
    external: bool = False
    requires: str | None = None
    capability: str | None = None
    provider: str | None = None
    observer: str | None = None           # toolkit.OBSERVERS 的键(结构化观测)
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    requires_file: str | None = None      # 脚本/入口文件不存在 ⇒ 该工具不注册(能力缺失如实报)
    capability_required: str | None = None  # 调用前必须能力可用(如 debug.ghidra)
    source: str = ""                      # 来自哪个 manifest 文件(审计)

    # -- 校验:坏定义在加载期就炸,不进运行时 --
    def validate(self) -> Manifest:
        if not NAME_RE.match(self.name or ""):
            raise ManifestError(f"{self.source}: 工具名非法 {self.name!r}(只允许字母数字 _ - ,≤64)")
        if not isinstance(self.input_schema, dict) or self.input_schema.get("type") != "object":
            raise ManifestError(f"{self.source}: input_schema 必须是 {{'type':'object', ...}}")
        if self.kind not in KINDS:
            raise ManifestError(f"{self.source}: kind 非法 {self.kind!r}(允许 {KINDS})")
        if self.kind == "python" and not self.adapter:
            raise ManifestError(f"{self.source}: python 适配器必须给 adapter(module:func)")
        if self.kind == "script" and not self.argv:
            raise ManifestError(f"{self.source}: script 工具必须给 argv")
        if self.kind == "feature" and not self.adapter:
            raise ManifestError(f"{self.source}: feature 工具必须给 adapter 名")
        if self.risk not in ("low", "high"):
            raise ManifestError(f"{self.source}: risk 非法 {self.risk!r}")
        if self.requires not in (None, "mcp"):
            raise ManifestError(f"{self.source}: requires 只支持 None/'mcp',收到 {self.requires!r}")
        return self

    @classmethod
    def from_dict(cls, data: dict, source: str = "<dict>") -> Manifest:
        known = {f for f in cls.__dataclass_fields__ if f != "source"}
        unknown = set(data) - known
        if unknown:
            raise ManifestError(f"{source}: 未知字段 {sorted(unknown)}(拼错会被静默忽略,所以直接拒绝)")
        try:
            m = cls(source=source, **data)
        except TypeError as e:                    # 缺必填字段/类型不符
            raise ManifestError(f"{source}: manifest 字段不完整: {e}") from e
        return m.validate()


def load_manifests(paths: list[str] | str) -> tuple[list[Manifest], list[dict]]:
    """从目录(或单个文件)加载 manifest;返回 (合法清单, 被跳过的说明)。"""
    if isinstance(paths, str):
        paths = [paths]
    out: list[Manifest] = []
    skipped: list[dict] = []
    seen: dict[str, str] = {}
    for p in paths:
        if os.path.isdir(p):
            try:
                files = sorted(os.path.join(p, f) for f in os.listdir(p) if f.endswith(".json"))
            except OSError as e:
                skipped.append({"file": p, "reason": f"目录不可读: {e}"})
                continue
        else:
            files = [p]
        for f in files:
            try:
                with open(f, encoding="utf-8") as fh:
                    data = json.load(fh)
                m = Manifest.from_dict(data, source=f)
                if m.name in seen:
                    raise ManifestError(f"{f}: 工具名与 {seen[m.name]} 冲突({m.name})")
            except (OSError, json.JSONDecodeError, ManifestError, TypeError) as e:
                skipped.append({"file": f, "reason": f"{type(e).__name__}: {e}"})
                continue
            seen[m.name] = f
            out.append(m)
    return out, skipped


# ---------------------------------------------------------------- 进程执行

def run_process(ctx: ToolContext, argv: list[str], *, timeout: float, cwd: str | None = None,
                env: dict[str, str] | None = None, cancel_check: Callable[[], bool] | None = None,
                ) -> dict:
    """跑一个外部程序(统一执行语义):独立进程组 + 取消即杀组 + 产物落盘 + sha256。

    与 `tools.shell_exec` 的差别:这里接受 **argv 列表**(不经 shell 解析),
    适合 manifest 里声明的入口;两者共用同一套状态/超时/取消语义。
    """
    art = ctx.artifacts_dir or os.path.join(ctx.root, "state", "artifacts", ctx.run_id or "local")
    try:
        os.makedirs(art, exist_ok=True)
        oid = "OUT-" + hashlib.sha256(f"{time.time()}-{os.getpid()}".encode()).hexdigest()[:8]
        op = os.path.join(art, f"{oid}.stdout")
        ep = os.path.join(art, f"{oid}.stderr")
        open(op, "wb").close()                      # 先建空文件(创建失败要能干净报错)
        open(ep, "wb").close()
    except OSError as e:
        return {"status": "error", "error": f"无法创建输出文件: {e}"}

    status = "error"
    code: int | None = None
    try:
        with open(op, "ab") as fo, open(ep, "ab") as fe:
            try:
                proc = subprocess.Popen(argv, cwd=cwd or ctx.root, stdout=fo, stderr=fe,
                                        env={**os.environ, **(env or {})}, start_new_session=True)
            except FileNotFoundError as e:
                fe.write(f"入口不存在: {e}".encode())
                return {"status": "error", "error": f"入口不存在: {argv[0]!r}",
                        "stdout_path": op, "stderr_path": ep}
            except OSError as e:
                return {"status": "error", "error": f"启动失败: {e}",
                        "stdout_path": op, "stderr_path": ep}
            deadline = time.time() + float(timeout)
            while True:
                slice_s = max(0.02, min(0.2, deadline - time.time()))
                try:
                    proc.wait(timeout=slice_s)
                    code = proc.returncode
                    status = "ok" if code == 0 else (
                        "cancelled" if (cancel_check and cancel_check()) else "error")
                    break
                except subprocess.TimeoutExpired:
                    if cancel_check and cancel_check():
                        _kill_group(proc)
                        with contextlib.suppress(Exception):
                            proc.wait(timeout=2)
                        status = "cancelled"
                        break
                    if time.time() >= deadline:
                        _kill_group(proc)
                        status = "timeout"
                        break
    except OSError as e:
        return {"status": "error", "error": f"无法写输出文件: {e}"}
    return {"status": status, "exit_code": code, "stdout_path": op, "stderr_path": ep,
            "stdout_sha256": sha256_file(op), "stderr_sha256": sha256_file(ep)}


def num(value: Any, default: float) -> float:
    """把参数里的数字安全转成 float(非法值不炸工具调用,退回默认并让调用方记录)。"""
    if value is None or value == "":
        return float(default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _tail(paths: list[str | None], keep: int = 3000) -> str:
    text = ""
    for p in paths:
        if not (p and os.path.isfile(p)):
            continue
        try:
            with open(p, "rb") as f:
                f.seek(max(0, os.path.getsize(p) - keep))
                text += f.read().decode("utf-8", errors="replace")
        except OSError:
            continue
    return text[-keep:]


# ---------------------------------------------------------------- 适配器

class Adapter:
    """调用方式适配器:只负责"怎么执行",不碰授权/记录(Registry 统一做)。"""

    def ensure(self, ctx: ToolContext) -> dict | None:
        """懒启动。返回错误 dict 表示启动失败(调用方据此失败,不静默继续)。"""
        return None

    def call(self, ctx: ToolContext, m: Manifest, args: dict) -> dict:
        raise NotImplementedError

    def close(self) -> None:
        return None

    def health(self) -> dict:
        return {"status": "unknown"}


class PythonAdapter(Adapter):
    def __init__(self, ref: str) -> None:
        self.ref = ref
        self._fn: Callable[..., Any] | None = None

    def _resolve(self) -> Callable[..., Any]:
        fn = self._fn
        if fn is None:
            ref = self.ref or ""
            if ":" not in ref:
                raise ManifestError(f"adapter 必须是 module:func,收到 {self.ref!r}")
            mod_name, func_name = ref.split(":", 1)
            allowed = _adapter_prefixes()
            if not any(mod_name.startswith(pre) and mod_name.strip(".") != pre.strip(".")
                       for pre in allowed):
                raise ManifestError(
                    f"adapter 模块不在白名单内: {mod_name!r}(允许前缀 {allowed};"
                    "确实需要时设 FW_TOOL_ADAPTER_PREFIXES)")
            mod = importlib.import_module(mod_name)
            candidate = getattr(mod, func_name, None)
            if not callable(candidate):
                raise ManifestError(f"adapter 不可调用: {self.ref}")
            self._fn = candidate
            fn = candidate
        return fn

    def validate(self) -> None:
        """注册期检查:模块必须在白名单前缀内(不 import,只校验名字)。"""
        ref = self.ref or ""
        if ":" not in ref:
            raise ManifestError(f"adapter 必须是 module:func,收到 {self.ref!r}")
        mod_name = ref.split(":", 1)[0]
        allowed = _adapter_prefixes()
        if not any(mod_name.startswith(pre) and mod_name.strip(".") != pre.strip(".")
                   for pre in allowed):
            raise ManifestError(
                f"adapter 模块不在白名单内: {mod_name!r}(允许前缀 {allowed};"
                "确实需要时设 FW_TOOL_ADAPTER_PREFIXES)")

    def call(self, ctx: ToolContext, m: Manifest, args: dict) -> dict:
        return self._resolve()(ctx, args) or {}


class ScriptAdapter(Adapter):
    """把参数填进 argv 模板:`{name}` 占位;`split_args` 里的占位符按 shell 规则拆开。"""

    def build_argv(self, m: Manifest, args: dict) -> list[str]:
        def sub(part: str) -> list[str]:
            out: list[str] = []
            for name, val in args.items():
                tok = "{" + name + "}"
                if tok in part:
                    val_s = "" if val is None else str(val)
                    if name in m.split_args:
                        out.extend(shlex.split(val_s))
                    else:
                        part = part.replace(tok, val_s)
            out.append(part)
            return out

        argv: list[str] = []
        for part in m.argv or []:
            argv.extend(sub(part))
        return argv

    def call(self, ctx: ToolContext, m: Manifest, args: dict) -> dict:
        timeout = num(args.get(m.timeout_arg), m.timeout) if m.timeout_arg else float(m.timeout)
        return run_process(ctx, self.build_argv(m, args), timeout=timeout, cwd=m.cwd, env=m.env,
                           cancel_check=lambda: _cancel_requested(ctx))


class FeatureAdapter(Adapter):
    """深度适配(如 Windows IDA):生命周期与调用完全自定义,但对 Registry 仍是普通工具。"""

    def __init__(self, name: str, impl: Callable[[ToolContext, Manifest, dict], dict],
                 ensure: Callable[[ToolContext], dict | None] | None = None,
                 close: Callable[[], None] | None = None,
                 health: Callable[[], dict] | None = None) -> None:
        self.name = name
        self._call = impl
        self._ensure = ensure
        self._close = close
        self._health = health

    def ensure(self, ctx: ToolContext) -> dict | None:
        return self._ensure(ctx) if self._ensure else None

    def call(self, ctx: ToolContext, m: Manifest, args: dict) -> dict:
        return self._call(ctx, m, args) or {}

    def close(self) -> None:
        if self._close:
            self._close()

    def health(self) -> dict:
        return self._health() if self._health else {"status": "unknown"}


_FEATURES: dict[str, Callable[[str], Adapter]] = {}


def register_feature(name: str, factory: Callable[[str], Adapter]) -> None:
    """注册一个"特色适配器"工厂(参数是 root)。Registry 只按名字取,不知道内部细节。"""
    _FEATURES[name] = factory


def feature_adapter(name: str, root: str) -> Adapter:
    if name not in _FEATURES:
        raise ManifestError(f"未注册的特色适配器: {name}(可用: {sorted(_FEATURES)})")
    return _FEATURES[name](root)


def _cancel_requested(ctx: ToolContext) -> bool:
    if not ctx.run_id:
        return False
    try:
        row = ctx.domain.con.execute("SELECT handback_reason FROM runs WHERE id=?",
                                     (ctx.run_id,)).fetchone()
    except Exception:
        return False
    return bool(row and (row["handback_reason"] or "").startswith("user_stop"))


# ---------------------------------------------------------------- 装配

def _capability_probe_hint(ctx: ToolContext, cap_name: str) -> str | None:
    """能力必须可用(用于有硬依赖的工具,如 ghidra)⇒ 不可用时返回错误文本。"""
    from .providers import default_capabilities

    cap = default_capabilities(ctx.root).get(cap_name)
    if cap is None:
        return None
    best, probe = cap.best()
    if probe is None or probe.status == "missing":
        return f"能力不可用:{cap_name} 没有可用 provider(不会静默成功)"
    return None


def build_spec(reg: Registry, root: str, m: Manifest) -> ToolSpec:
    """把 manifest 变成 Registry 里的 ToolSpec(执行语义统一在 Registry 里,不在这)。"""
    if m.kind == "python":
        adapter = PythonAdapter(m.adapter or "")
        adapter.validate()                       # 坏适配器在注册期就暴露(不是等调用)
    elif m.kind == "script":
        adapter = ScriptAdapter()
    else:
        adapter = feature_adapter(m.adapter or "", root)

    observer_fn = None
    if m.observer:
        from .toolkit import OBSERVERS

        observer_fn = OBSERVERS.get(m.observer)

    def handler(ctx: ToolContext, args: dict) -> dict:
        started = adapter.ensure(ctx)
        if started and started.get("status") not in (None, "ok"):
            return started                       # 生命周期启动失败 ⇒ 工具失败(不静默)
        if m.capability_required:
            hint = _capability_probe_hint(ctx, m.capability_required)
            if hint:
                return {"status": "error", "error": hint,
                        "meta": {"capability": m.capability_required, "provider": None}}
        out = adapter.call(ctx, m, args) or {}
        out.setdefault("status", "ok")
        text = out.get("text") or _tail([out.get("stdout_path"), out.get("stderr_path")])
        if text:
            out["output_tail"] = text[-3000:]
            if observer_fn is not None:
                try:
                    out["observation"] = observer_fn(text)
                except Exception as e:            # 解析失败不改变工具结论,但要如实标注
                    out["observation"] = {"status": "unparsed",
                                          "error": f"{type(e).__name__}: {e}"}
        meta = dict(out.get("meta") or {})
        if m.capability:
            meta.setdefault("capability", m.capability)
        if m.provider:
            meta.setdefault("provider", m.provider)
        if meta:
            out["meta"] = meta
        return out

    spec = ToolSpec(name=m.name, description=m.description, input_schema=m.input_schema,
                    handler=handler, risk=m.risk, requires=m.requires, external=m.external)
    spec.manifest = m          # 审计用:这个工具从哪来、依赖什么(运行时不再读)
    reg.register(spec)
    return spec


FRAMEWORK_REGISTRY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "tools", "registry")


def default_registry_dirs(root: str) -> list[str]:
    """manifest 目录解析顺序(显式 > 工作区 > 框架自带)。

    工具定义属于**框架**(随代码走);研究工作区可以放自己的 `tools/registry` 覆盖/扩展,
    但临时工作区(如测试/评测的 scratch)不该因为没有 registry 目录就丢掉全部工具。
    """
    env = os.environ.get("FW_TOOL_REGISTRY")
    if env:
        return [env]
    dirs: list[str] = []
    seen: set[str] = set()
    for cand in (os.path.join(root, "tools", "registry"), FRAMEWORK_REGISTRY):
        real = os.path.realpath(cand)
        if os.path.isdir(cand) and real not in seen:
            seen.add(real)
            dirs.append(cand)
    return dirs


def register_manifests(reg: Registry, root: str, *, dirs: list[str] | None = None,
                       ) -> dict:
    """加载并注册 manifest;缺依赖的工具**不注册**并如实记录(不是静默成功)。

    目录解析顺序:显式 dirs > `$FW_TOOL_REGISTRY` > `<root>/tools/registry`。
    """
    if dirs is None:
        dirs = default_registry_dirs(root)
    manifests: list[Manifest] = []
    skipped: list[dict] = []
    for d in dirs:
        if not os.path.exists(d):
            skipped.append({"file": d, "reason": "目录不存在"})
            continue
        got, bad = load_manifests(d)
        manifests.extend(got)
        skipped.extend(bad)
    registered: list[str] = []
    unavailable: list[dict] = []
    for m in manifests:
        if m.requires_file and not os.path.isfile(os.path.join(root, m.requires_file)):
            unavailable.append({"name": m.name, "reason": f"缺少入口 {m.requires_file}"})
            continue
        try:
            build_spec(reg, root, m)
            registered.append(m.name)
        except (ManifestError, OSError, ImportError) as e:
            unavailable.append({"name": m.name, "reason": f"{type(e).__name__}: {e}"})
    return {"registered": registered, "unavailable": unavailable, "skipped": skipped,
            "dirs": dirs}


def capability_index(root: str, *, dirs: list[str] | None = None) -> dict[str, list[dict]]:
    """manifest 声明的 能力 → provider 列表(给 providers/inventory 用,不重复维护清单)。"""
    if dirs is None:
        dirs = default_registry_dirs(root)
    manifests: list[Manifest] = []
    for d in dirs:
        if os.path.isdir(d):
            manifests.extend(load_manifests(d)[0])
    out: dict[str, list[dict]] = {}
    for m in manifests:
        if not m.capability:
            continue
        available = not (m.requires_file and not os.path.isfile(os.path.join(root, m.requires_file)))
        out.setdefault(m.capability, []).append(
            {"tool": m.name, "provider": m.provider or m.source, "available": available,
             "kind": m.kind})
    return out


def close_feature_adapters(reg: Registry) -> None:
    """收尾(如停 Windows IDA supervisor):只关 manifest 声明过的特色适配器。"""
    for spec in list(reg.specs.values()):
        m = getattr(spec, "manifest", None)
        if m is None or m.kind != "feature":
            continue
        with contextlib.suppress(Exception):
            feature_adapter(m.adapter or "", "").close()


__all__ = ["Manifest", "ManifestError", "load_manifests", "register_manifests",
           "build_spec", "run_process", "sha256_file", "capability_index",
           "register_feature", "feature_adapter", "FeatureAdapter", "PythonAdapter",
           "ScriptAdapter", "Adapter", "close_feature_adapters"]
