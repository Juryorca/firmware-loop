"""能力(Capability)与提供者(Provider)层。

为什么需要它
- 真实环境是混合的:apt 系统包、mamba/venv、自编译多架构 AFL tracer、改版 QEMU、
  Windows IDA(经 WSL)、Docker、MCP。**不重装、不替换**,只做发现/解析/选择/探针/记录。
- 评审要求:不能"afl-fuzz 存在就说 ARM64 fuzz 可用";不能"unblob 本体在就说 JFFS2/UBI 可用"。
  所以能力 → 多个 provider;每个 provider 自己探针,状态区分:
  missing / deps_missing / not_started / partial / arch_mismatch / ready。

非穷举:能力清单与方法一样是开放的,新增 provider 只需实现 resolve+probe。
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field

READY = "ready"
PARTIAL = "partial"
DEPS_MISSING = "deps_missing"
NOT_STARTED = "not_started"
ARCH_MISMATCH = "arch_mismatch"
MISSING = "missing"
STATUSES = (READY, PARTIAL, DEPS_MISSING, NOT_STARTED, ARCH_MISMATCH, MISSING)


@dataclass
class Probe:
    status: str
    detail: str = ""
    version: str | None = None
    arch: str | None = None
    path: str | None = None
    env: dict = field(default_factory=dict)
    # 证据强度:discovered(文件在) < configured(配置在) < reachable(能握手/能起) < verified(真跑通一次)
    evidence: str = "discovered"

    def ok(self) -> bool:
        return self.status == READY


@dataclass
class Provider:
    id: str
    kind: str                     # system | venv | built | windows | container | mcp
    resolve: Callable[[], dict]   # ⇒ {argv_prefix, env, cwd, interpreter, ...}
    probe: Callable[[], Probe]
    notes: str = ""


@dataclass
class Capability:
    name: str
    description: str
    providers: list[Provider] = field(default_factory=list)
    target_arch: str | None = None

    def best(self, *, want_arch: str | None = None) -> tuple[Provider | None, Probe | None]:
        """选一个可用 provider:跳过 arch_mismatch,优先 ready,其次 partial。"""
        want_arch = want_arch or self.target_arch
        ranked: list[tuple[int, Provider, Probe]] = []
        for p in self.providers:
            pr = p.probe()
            if want_arch and pr.arch and pr.arch != want_arch:
                ranked.append((90, p, Probe(ARCH_MISMATCH, f"架构不符({pr.arch})")))
                continue
            ranked.append(({READY: 0, PARTIAL: 10, NOT_STARTED: 20, DEPS_MISSING: 30,
                            ARCH_MISMATCH: 90, MISSING: 99}.get(pr.status, 99), p, pr))
        if not ranked:
            return None, None
        ranked.sort(key=lambda x: x[0])
        _, p, pr = ranked[0]
        return p, pr


def _run(cmd: list[str], timeout: float = 10.0) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # 探针不能抛异常
        return 127, f"{type(e).__name__}: {e}"


def _file_arch(path: str) -> str | None:
    rc, out = _run(["file", "-b", path])
    if rc != 0:
        return None
    low = out.lower()
    for key, arch in (("aarch64", "aarch64"), ("arm", "arm"), ("x86-64", "x86_64"), ("mips", "mips")):
        if key in low:
            return arch
    return None


# ---------------- 具体 provider(本机实测环境) ----------------

def afl_tracer_provider(dirpath: str, arch: str, kind: str = "built",
                        sample_roots: list[str] | None = None) -> Provider:
    """AFL++ qemu tracer 的探针。

    ⚠️ 反例(2026-11-04 实测):tracer 是**宿主二进制**,`file` 说它是 x86-64 完全正常;
    决定它能否跑 ARM64 的是 **CPU_TARGET(guest 架构)**,不是 ELF 架构。
    因此探针必须**真跑一个 guest 样本**并看是否报 "Invalid ELF image"。
    """
    tracer = os.path.join(dirpath, "afl-qemu-trace")
    has_qasan = os.path.isfile(os.path.join(dirpath, "libqasan.so"))

    def _find_sample() -> tuple[str, str] | None:
        """在给定根里找一个 arm/aarch64 ELF(用 file 判的是**样本**架构,这是对的)。"""
        for root in sample_roots or []:
            for base, _dirs, files in os.walk(root):
                for f in files[:200]:
                    p = os.path.join(base, f)
                    if not os.path.isfile(p) or os.path.islink(p):
                        continue
                    a = _file_arch(p)
                    if a == "aarch64":
                        return p, os.path.dirname(root) if os.path.basename(root) != "rootfs_merged" else root
        return None

    def probe() -> Probe:
        if not os.path.isfile(tracer):
            return Probe(MISSING, f"缺 {tracer}")
        sample = _find_sample()
        if not sample:
            return Probe(PARTIAL, "tracer 在,但没有 aarch64 样本可验(未证明 guest 支持)", path=tracer,
                         env={"AFL_PATH": dirpath})
        binpath, ldprefix = sample
        env = dict(os.environ, QEMU_LD_PREFIX=ldprefix)
        try:
            r = subprocess.run([tracer, binpath, "--help"], capture_output=True, text=True, timeout=20, env=env)
            out = (r.stdout + r.stderr).lower()
        except Exception as e:
            return Probe(PARTIAL, f"跑样本失败: {type(e).__name__}", path=tracer, env={"AFL_PATH": dirpath})
        if "invalid elf image" in out:
            return Probe(ARCH_MISMATCH, f"{os.path.basename(dirpath)} 的 tracer 不支持 {arch} guest",
                         arch=arch, path=tracer, env={"AFL_PATH": dirpath})
        detail = f"实测可跑 {arch} guest 样本({os.path.basename(binpath)})"
        if not has_qasan:
            detail += ";该目录无 libqasan ⇒ 无 ASan"
        return Probe(PARTIAL if not has_qasan else READY, detail, arch=arch, path=tracer,
                     env={"AFL_PATH": dirpath, "QEMU_LD_PREFIX": ldprefix})

    return Provider(id=f"afl-tracer-{arch}-{os.path.basename(dirpath)}", kind=kind,
                    resolve=lambda: {"env": {"AFL_PATH": dirpath, "QEMU_LD_PREFIX": (sample[1] if (sample := _find_sample()) else "")},
                                     "argv_prefix": [tracer], "arch": arch},
                    probe=probe)


def qemu_provider(binary: str, *, netlink: bool) -> Provider:
    def probe() -> Probe:
        path = shutil.which(binary) or (binary if os.path.isfile(binary) else None)
        if not path:
            return Probe(MISSING, f"未安装 {binary}")
        rc, out = _run([path, "--version"], 5)
        ver = out.splitlines()[0] if out else ""
        return Probe(READY if netlink else PARTIAL,
                     "支持 NETLINK_NETFILTER(SOCK_DIAG/XFRM/GENERIC 类 daemon)" if netlink
                     else "上游白名单:NETLINK_NETFILTER 被拒(NFQUEUE 类 daemon 跑不了)",
                     version=ver, path=path, evidence="reachable")
    return Provider(id=f"{os.path.basename(binary)}", kind="built" if netlink else "system",
                    resolve=lambda: {"argv_prefix": [shutil.which(binary) or binary], "netlink": netlink},
                    probe=probe)


def venv_python_provider(venv_python: str, module: str, *, kind: str = "venv") -> Provider:
    """在**指定解释器**里探针,不依赖当前 shell 是否激活。"""
    def probe() -> Probe:
        if not os.path.isfile(venv_python):
            return Probe(MISSING, f"缺解释器 {venv_python}")
        rc, out = _run([venv_python, "-c", f"import {module};print(getattr({module},'__version__','?'))"], 30)
        if rc != 0:
            return Probe(DEPS_MISSING, f"{module} 未安装: {out.splitlines()[-1] if out else ''}", path=venv_python)
        lines = [x for x in out.strip().splitlines() if x.strip() and not x.startswith(("import ", "Warning", "  "))]
        last = (lines or [""])[-1]
        return Probe(READY, f"{module} {last}", version=last[:60], path=venv_python, evidence="verified")
    return Provider(id=f"{os.path.basename(os.path.dirname(os.path.dirname(venv_python)))}-{module}",
                    kind=kind, resolve=lambda: {"interpreter": venv_python, "module": module}, probe=probe)


def unblob_provider(venv_python: str) -> Provider:
    """本体在 ≠ 各 extractor 都在:缺哪个就报哪个(部分能力)。"""
    required = {"jefferson": "JFFS2", "ubireader_extract_files": "UBI/UBIFS",
                "lz4": "lz4(库)", "unar": "unar(包)"}
    tools = {"simg2img": "Android sparse", "fsck.erofs": "EROFS", "partclone.restore": "partclone",
             "7z": "7z 归档", "zstd": "zstd", "debugfs": "ext2/3/4"}
    binpath = os.path.join(os.path.dirname(venv_python), "unblob")

    def probe() -> Probe:
        rc, out = _run([binpath, "--help"], 30)
        if rc != 0 or "--help" not in out:
            return Probe(MISSING, f"unblob 本体不可用({binpath})")
        missing_modules = [m for m in required if _run([venv_python, "-c", f"import {m}"], 20)[0] != 0]
        missing_tools = [t for t in tools if shutil.which(t) is None]
        detail = []
        if missing_modules:
            detail.append("缺 python 提取器: " + ", ".join(f"{m}({required[m]})" for m in missing_modules))
        if missing_tools:
            detail.append("缺外部工具: " + ", ".join(f"{t}({tools[t]})" for t in missing_tools))
        status = READY if not detail else PARTIAL
        return Probe(status, ";".join(detail) or "全部提取器可用", path=binpath)
    return Provider(id="unblob-venv", kind="venv", resolve=lambda: {"interpreter": venv_python}, probe=probe)


def system_tool_provider(name: str, extra: list[str] | None = None, version_arg: list[str] | None = None) -> Provider:
    def probe() -> Probe:
        path = shutil.which(name)
        if not path:
            return Probe(MISSING, f"未安装 {name}")
        rc, out = _run(([path] + (extra or ["--version"])), 10)
        ok = rc == 0
        ver = None
        if version_arg:
            rc2, out2 = _run([path] + version_arg, 10)
            ok = ok or rc2 == 0
            ver = out2.splitlines()[0][:60] if out2 else None
        first = (out.splitlines() or [""])[0][:120]
        return Probe(READY if ok else PARTIAL, first, version=ver, path=path)
    return Provider(id=name, kind="system", resolve=lambda: {"path": shutil.which(name)}, probe=probe)


def windows_ida_provider(root: str) -> Provider:
    """复用现有 IDA 生命周期封装(tools/ida_*.sh),不另造接口。"""
    idat = "/mnt/d/ida9.3/idat.exe"

    def probe() -> Probe:
        if not os.path.isfile(idat):
            return Probe(MISSING, f"缺 {idat}")
        stage = "/mnt/d/ida_test"
        try:
            if not os.path.isdir(stage) or not os.access(stage, os.W_OK):
                return Probe(NOT_STARTED, f"{stage} 不可写")
            ports = glob.glob(os.path.join(stage, ".port-*"))
        except OSError as e:
            return Probe(NOT_STARTED, f"暂存区不可读: {e}")
        started = bool(ports)
        return Probe(READY if started else NOT_STARTED,
                     "IDA supervisor 已启动(端口文件存在)" if started
                     else "IDA 未启动(用 tools/ida_start.sh,不要另造)",
                     path=idat, evidence="reachable" if started else "configured")

    def resolve() -> dict:
        return {"argv_prefix": ["bash", os.path.join(root, "tools/ida_call.sh")], "lifecycle":
                os.path.join(root, "tools/ida_start.sh")}

    return Provider(id="ida-windows-wsl", kind="windows", resolve=resolve, probe=probe)


def mcp_provider(name: str, config_paths: list[str]) -> Provider:
    def _load() -> dict:
        last_err = ""
        for p in config_paths:
            if os.path.isfile(p):
                try:
                    with open(p, encoding="utf-8") as f:
                        d = json.load(f)
                    srv = (d.get("mcpServers") or {}).get(name)
                    if srv:
                        return srv
                except (OSError, json.JSONDecodeError) as e:
                    last_err = f"{p}: {e}"
                    continue
        _ = last_err
        return {}

    def probe() -> Probe:
        srv = _load()
        if not srv:
            return Probe(MISSING, f"配置里没有 {name}")
        if srv.get("enabled") is False:
            return Probe(NOT_STARTED, "配置存在但 enabled=false")
        return Probe(READY, "已配置(" + ("url" if srv.get("url") else "stdio") +
                     ");独立运行时尚未实现 MCP 客户端 ⇒ 只到 configured",
                     path=(srv.get("url") or srv.get("command")), evidence="configured")
    return Provider(id=f"mcp-{name}", kind="mcp", resolve=lambda: {"config": _load()}, probe=probe)


def _guest_roots(root: str) -> list[str]:
    """找可用于验证 guest 支持的 rootfs(只读扫描 state/*/rootfs*)。"""
    import glob as _g

    roots = {os.path.dirname(p) for p in _g.glob(os.path.join(root, "state", "*", "rootfs*", "bin"))}
    roots |= set(_g.glob(os.path.join(root, "state", "*", "rootfs*")))
    return sorted(roots)


def default_capabilities(root: str, home: str | None = None) -> dict[str, Capability]:
    home = home or os.path.expanduser("~")
    pwn = os.path.join(os.environ.get("ENTER_HOME", os.path.join(home, "ENTER")), "envs", "pwn", "bin", "python")
    unblob_py = os.path.join(home, "venvs", "unblob", "bin", "python")
    caps: dict[str, Capability] = {
        "fuzz.aarch64": Capability("fuzz.aarch64", "对 aarch64 guest 做覆盖率/变异 fuzz", [
            afl_tracer_provider(os.path.join(home, ".local/bin/afl-aarch64"), "aarch64",
                                sample_roots=_guest_roots(root)),
            afl_tracer_provider("/usr/local/bin", "aarch64", kind="built",
                                sample_roots=_guest_roots(root)),
        ], target_arch="aarch64"),
        "emu.aarch64": Capability("emu.aarch64", "用户态仿真 aarch64(含 netlink 白名单解除)", [
            qemu_provider("qemu-aarch64-patched", netlink=True),
            qemu_provider("qemu-aarch64", netlink=False),
        ], target_arch="aarch64"),
        "symbolic.angr": Capability("symbolic.angr", "符号执行(angr)", [venv_python_provider(pwn, "angr")]),
        "extract.unblob": Capability("extract.unblob", "固件/文件系统提取", [unblob_provider(unblob_py)]),
        "extract.binwalk": Capability("extract.binwalk", "签名扫描与提取",
                                      [system_tool_provider("binwalk", ["--help"], version_arg=["--help"])]),
        "extract.squashfs-vendor": Capability("extract.squashfs-vendor", "厂商魔改 SquashFS",
                                             [system_tool_provider("sasquatch", ["-h"], version_arg=[])]),
        "trace.strace": Capability("trace.strace", "系统调用追踪", [system_tool_provider("strace", ["-V"])]),
        "debug.ghidra": Capability("debug.ghidra", "Ghidra 无头分析(脚本见 tools/ghidra_scripts)",
                                  [system_tool_provider("analyzeHeadless", ["-help"], version_arg=[])]),
        "debug.ida": Capability("debug.ida", "Windows IDA 无头(经 WSL,复用 tools/ida_*)",
                                [windows_ida_provider(root)]),
        "mcp.chrome": Capability("mcp.chrome", "浏览器自动化(取件/提交)", [
            mcp_provider("chrome-devtools", [os.path.join(home, ".pi/agent/mcp.json"),
                                             os.path.join(root, "state/mcp.json")]),
            mcp_provider("chrome-live", [os.path.join(home, ".pi/agent/mcp.json"),
                                         os.path.join(root, "state/mcp.json")]),
        ]),
    }
    return caps


def inventory(root: str) -> list[dict]:
    """给模型/CLI 的能力清单:能力 → 提供者 → 探针结果(**不重装任何东西**)。"""
    out = []
    for name, cap in default_capabilities(root).items():
        entries = []
        for p in cap.providers:
            pr = p.probe()
            entries.append({"provider": p.id, "kind": p.kind, "status": pr.status, "detail": pr.detail,
                            "version": pr.version, "arch": pr.arch, "path": pr.path,
                            "evidence": pr.evidence})
        best, best_probe = cap.best(want_arch=cap.target_arch)
        out.append({"capability": name, "description": cap.description,
                    "best": best.id if best else None,
                    "best_status": best_probe.status if best_probe else MISSING,
                    "providers": entries})
    # 工具 manifest 是"能力 → 工具"的单一来源:这里只读,不重复维护清单
    from .toolreg import capability_index

    tools = capability_index(root)
    for row in out:
        own = tools.get(row["capability"]) or []
        if own:
            row["tools"] = own
    return out
