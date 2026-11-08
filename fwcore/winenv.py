"""Windows(WSL)接入环境配置:一份配置,被 Python 适配器、能力探针和 shell 脚本共同使用。

为什么需要它
- IDA 的自动启动链路跨三个世界:WSL 的路径、Windows 的路径、HTTP Supervisor。
  这些值原来写死在 `providers.py` / `adapters_ida.py` / `tools/ida_*.sh` 里
  ( `/mnt/d/ida_test`、`D:\\ida9.3`、`C:\\Users\\30104\\...`、端口 8745 ),
  换机器/换盘符/换安装位置就全坏,而且同一个值在 Python 和 shell 里各写一遍。
- 这里做**单一来源**:所有键集中定义、集中解析、集中导出给 shell(`ida-env --sh`)。

优先级(高 → 低)
  1. 显式环境变量 `FW_IDA_*`
  2. 工作区配置文件 `<root>/state/ida.json`(或 `$FW_IDA_CONFIG` 指定)
  3. 兼容默认值(本项目开发机上的实际路径;**可以被完全覆盖**,不是唯一实现)

诚实边界:这里只做配置解析与路径转换,不做 Windows 进程管理;IDA 的 Supervisor 生命周期仍由
`fwcore/adapters_ida.py` 负责。
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from dataclasses import dataclass, field

DEFAULTS: dict[str, str] = {
    # Windows 侧:IDA 安装目录、idalib-mcp 可执行文件、暂存目录、Windows 侧 python
    "IDA_WIN_DIR": r"D:\ida9.3",
    "IDA_MCP_EXE": r"D:\python313\Scripts\idalib-mcp.exe",
    "IDA_WIN_STAGE": r"D:\ida_test",
    "IDA_WIN_PYTHON": r"D:\python313\python.exe",
    # WSL 侧:暂存目录(必须与 WIN_STAGE 指向同一位置)、Supervisor 地址与日志
    "IDA_STAGE": "/mnt/d/ida_test",
    "IDA_URL": "http://127.0.0.1:8745/mcp",
    # 空 = <root>/state/ida/supervisor.log(不要写死系统临时目录)
    "IDA_LOG": "",
    # Windows 侧 mcp/instances 目录(worker 注册表);空 = 用 %APPDATA% 探测
    "IDA_WIN_INSTANCES": "",
    # 直接给 WSL 路径(优先级更高;导出的 FW_IDA_INSTANCES 可回灌,便于脚本/测试覆盖)
    "IDA_INSTANCES": "",
    # 生命周期参数
    "IDA_START_TIMEOUT": "150",
    "IDA_PROBE_TIMEOUT": "3",
    "IDA_STOP_ON_CLOSE": "0",
}

# 盘符挂载可显式覆盖(stage 目录不在 /mnt/<letter> 时用):FW_IDA_MOUNT_D=/mnt/winD
MOUNT_ENV = "FW_IDA_MOUNT_"


def _env_key(name: str) -> str:
    return "FW_" + name


@dataclass
class IdaConfig:
    values: dict[str, str] = field(default_factory=dict)
    source: str = "defaults"          # 这份配置从哪里来(审计/排错用)
    root: str = "."
    mount_overrides: dict[str, str] = field(default_factory=dict)

    # -- 读取 --
    def get(self, name: str) -> str:
        return self.values.get(name, DEFAULTS.get(name, ""))

    @property
    def stage(self) -> str:
        return self.get("IDA_STAGE")

    @property
    def win_stage(self) -> str:
        return self.get("IDA_WIN_STAGE")

    @property
    def url(self) -> str:
        return self.get("IDA_URL")

    @property
    def mcp_exe(self) -> str:
        """idalib-mcp 在当前(WSL)环境里的可执行路径(由 Windows 路径转换而来)。"""
        return self.windows_to_wsl(self.get("IDA_MCP_EXE"))

    @property
    def start_timeout(self) -> float:
        return _num(self.get("IDA_START_TIMEOUT"), 150.0)

    @property
    def probe_timeout(self) -> float:
        return _num(self.get("IDA_PROBE_TIMEOUT"), 3.0)

    @property
    def log_path(self) -> str:
        """Supervisor 日志:显式配置优先,否则放工作区 `state/ida/` 下。"""
        return self.get("IDA_LOG") or os.path.join(self.root, "state", "ida", "supervisor.log")

    @property
    def instances_dir(self) -> str:
        """worker 注册表目录(WSL 路径);探测不到返回空串(收尾时如实跳过)。"""
        direct = self.get("IDA_INSTANCES")
        if direct:
            return direct
        explicit = self.get("IDA_WIN_INSTANCES")
        if explicit:
            return self.windows_to_wsl(explicit)
        try:
            # 中文 Windows 的 cmd 输出可能是 GBK ⇒ 按字节取、逐编码尝试(不猜死一个编码)
            r = subprocess.run(["cmd.exe", "/c", "echo %APPDATA%"], capture_output=True,
                               timeout=3, check=False)
            raw = (getattr(r, "stdout", b"") or b"")
            appdata = ""
            for enc in ("utf-8", "gbk", "cp1252"):
                try:
                    appdata = raw.decode(enc).strip()
                    break
                except UnicodeDecodeError:
                    continue
        except (OSError, subprocess.SubprocessError):
            return ""
        if not appdata or "%" in appdata:
            return ""
        return self.windows_to_wsl(appdata + r"\Hex-Rays\IDA Pro\mcp\instances")

    @property
    def stop_on_close(self) -> bool:
        return self.get("IDA_STOP_ON_CLOSE") in ("1", "true", "yes", "on")

    # -- 路径转换(用同一张挂载表,双向一致) --
    def drive_mount(self, drive: str) -> str:
        d = drive.rstrip(":").upper()
        override = self.mount_overrides.get(d)
        if override:
            return override
        env = os.environ.get(f"{MOUNT_ENV}{d}")
        if env:
            return env
        return f"/mnt/{d.lower()}"

    def windows_to_wsl(self, win_path: str) -> str:
        """`D:\\ida_test\\a b.exe` → `/mnt/d/ida_test/a b.exe`(支持任意盘符/空格)。"""
        if not win_path:
            return ""
        if win_path.startswith("\\\\"):          # UNC:无法可靠映射 ⇒ 原样返回,由调用方报错
            return win_path
        p = win_path
        if len(p) >= 2 and p[1] == ":":
            drive, rest = p[0], p[2:]
            mount = self.drive_mount(drive)
            rest = rest.replace("\\", "/").lstrip("/")
            return f"{mount}/{rest}" if rest else mount
        return p.replace("\\", "/")

    def wsl_to_windows(self, wsl_path: str) -> str:
        """`/mnt/d/ida_test/a b` → `D:\\ida_test\\a b`(反向用同一张挂载表)。"""
        if not wsl_path:
            return ""
        p = os.path.abspath(wsl_path) if wsl_path.startswith("/") else wsl_path
        for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            mount = self.drive_mount(drive).rstrip("/")
            if p == mount or p.startswith(mount + "/"):
                rest = p[len(mount):].lstrip("/")
                return f"{drive}:\\" + rest.replace("/", "\\")
        # 明确配置的挂载点(可能不是 /mnt/<x>)
        for drive, mount in self.mount_overrides.items():
            m = mount.rstrip("/")
            if p == m or p.startswith(m + "/"):
                rest = p[len(m):].lstrip("/")
                return f"{drive}:\\" + rest.replace("/", "\\")
        return p

    # -- 自检:配置错要**准确**报出来(而不是让 IDA 分析失败) --
    def problems(self) -> list[str]:
        out: list[str] = []
        exe = self.mcp_exe
        if not os.path.isfile(exe):
            out.append(f"idalib-mcp 不存在: {exe}(Windows 路径 {self.get('IDA_MCP_EXE')};"
                       "用 FW_IDA_MCP_EXE 或 state/ida.json 配置)")
        stage = self.stage
        if not os.path.isdir(stage):
            out.append(f"暂存目录不存在: {stage}(Windows 路径 {self.win_stage})")
        else:
            try:
                writable = os.access(stage, os.W_OK)
            except OSError:
                writable = False
            if not writable:
                out.append(f"暂存目录不可写: {stage}")
        mount = self.drive_mount(self.win_stage[:1] or "D")
        if not os.path.isdir(mount):
            out.append(f"盘符挂载点不存在: {mount}(盘符 {self.win_stage[:1]}:"
                       f",可用 FW_IDA_MOUNT_{self.win_stage[:1].upper()}=<挂载点> 指定)")
        return out

    def shell_exports(self) -> str:
        """给 shell 脚本用的 `export` 语句(值经过 shlex.quote,路径含空格也安全)。"""
        pairs = {
            "FW_IDA_STAGE": self.stage,
            "FW_IDA_WIN_STAGE": self.win_stage,
            "FW_IDA_MCP_EXE": self.mcp_exe,
            "FW_IDA_WIN_MCP_EXE": self.get("IDA_MCP_EXE"),
            "FW_IDA_WIN_DIR": self.get("IDA_WIN_DIR"),
            "FW_IDA_WIN_PYTHON": self.get("IDA_WIN_PYTHON"),
            "FW_IDA_URL": self.url,
            "FW_IDA_LOG": self.log_path,
            "FW_IDA_START_TIMEOUT": _int_str(self.start_timeout),
            "FW_IDA_PROBE_TIMEOUT": _int_str(self.probe_timeout),
            "FW_IDA_STOP_ON_CLOSE": "1" if self.stop_on_close else "0",
            "FW_IDA_INSTANCES": self.instances_dir,
        }
        for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            pairs[f"FW_IDA_MOUNT_{drive}"] = self.drive_mount(drive)
        return "\n".join(f"export {k}={shlex.quote(v)}" for k, v in pairs.items())

    def describe(self) -> dict:
        return {"source": self.source, "values": dict(self.values),
                "derived": {"mcp_exe_wsl": self.mcp_exe, "stage_wsl": self.stage,
                            "url": self.url, "win_stage": self.win_stage},
                "problems": self.problems()}


def _int_str(value: float) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "0"


def _num(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _load_config_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def load(root: str | None = None, *, env: dict[str, str] | None = None) -> IdaConfig:
    """按 环境变量 > 配置文件 > 默认值 解析;`mounts` 支持显式盘符映射。"""
    env = dict(os.environ if env is None else env)
    cfg: dict[str, str] = {}
    source = "defaults"
    mounts: dict[str, str] = {}

    cfg_path = env.get("FW_IDA_CONFIG") or (
        os.path.join(root, "state", "ida.json") if root else "")
    if cfg_path and os.path.isfile(cfg_path):
        data = _load_config_file(cfg_path)
        for k, v in (data.get("values") or {}).items():
            if k in DEFAULTS:
                cfg[k] = str(v)
        mounts.update({str(k).rstrip(":").upper(): str(v) for k, v in (data.get("mounts") or {}).items()})
        source = cfg_path

    for key in DEFAULTS:
        v = env.get(_env_key(key))
        if v not in (None, ""):
            cfg[key] = v
            source = "env"
    for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        v = env.get(f"{MOUNT_ENV}{drive}")
        if v:
            mounts[drive] = v

    return IdaConfig(values=cfg, source=source, mount_overrides=mounts,
                     root=os.path.abspath(root) if root else ".")


def shell_exports(root: str | None = None) -> str:
    return load(root).shell_exports()
