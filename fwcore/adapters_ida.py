"""IDA(Windows,经 WSL)特色适配器:自动启动 supervisor → 调用 → 健康检查 → 可选收尾。

为什么它要单独一层(而不是普通 shell_exec)
- 它不是"执行一条命令":有**生命周期**(supervisor 常驻、IDB 暂存、幂等启动)、
  有**跨进程状态**(WSL 侧脚本 + Windows 侧 IDA worker)、有**明确失败面**(worker 没激活/端口不通)。
- 但 Agent Loop 不需要知道 Windows/端口/暂存目录 —— 它只看到 `ida_query`。

环境差异(本轮修复)
- 端口、Supervisor URL、暂存目录、IDA 安装路径、idalib-mcp 位置、Windows python 路径
  **全部来自 `fwcore/winenv.py` 的同一份配置**(环境变量 > `state/ida.json` > 兼容默认值),
  不再写死在本文件。
- 配置错误(缺 exe / 暂存目录不存在 / 盘符没挂载)会**明确报出缺什么**,
  不会被误当成"IDA 分析失败"。

修掉的既有缺陷
- 旧实现:`shell_exec(ctx, "bash tools/ida_start.sh")` 的返回**根本没检查** ⇒ supervisor 起不来也照样
  往下调 `ida_call.sh`,模型拿到的是"调用失败"而不是"启动失败"(错误归因错、更难恢复)。
"""

from __future__ import annotations

import json
import os

from .toolreg import Adapter, Manifest, num, run_process
from .tools import ToolContext
from .winenv import IdaConfig
from .winenv import load as load_ida_config


def _tail(path: str | None, keep: int = 600) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - keep))
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def _probe_enabled() -> bool:
    """默认探测 supervisor;测试里可用 FW_IDA_PROBE=0 关掉(生产不要关)。"""
    return os.environ.get("FW_IDA_PROBE", "1") != "0"


def _supervisor_up(ctx: ToolContext, cfg: IdaConfig) -> bool:
    """探测 supervisor 是否在线(只读、带超时;不启动任何东西)。"""
    if not _probe_enabled():
        return False
    out = run_process(ctx, ["curl", "-s", "--noproxy", "*", "--max-time",
                            str(max(1, int(cfg.probe_timeout))),
                            "-o", os.devnull, "-w", "%{http_code}", cfg.url],
                      timeout=cfg.probe_timeout + 3)
    try:
        with open(out.get("stdout_path") or "", encoding="utf-8", errors="replace") as f:
            code = f.read().strip()
    except OSError:
        code = ""
    return code not in ("", "000")


def _shell_env(cfg: IdaConfig) -> dict[str, str]:
    """把同一份配置传给 shell 脚本(脚本不再自己写死路径/端口)。"""
    return {
        "FW_IDA_STAGE": cfg.stage,
        "FW_IDA_WIN_STAGE": cfg.win_stage,
        "FW_IDA_MCP_EXE": cfg.mcp_exe,
        "FW_IDA_WIN_MCP_EXE": cfg.get("IDA_MCP_EXE"),
        "FW_IDA_WIN_DIR": cfg.get("IDA_WIN_DIR"),
        "FW_IDA_WIN_PYTHON": cfg.get("IDA_WIN_PYTHON"),
        "FW_IDA_URL": cfg.url,
        "FW_IDA_LOG": cfg.log_path,
        "FW_IDA_START_TIMEOUT": str(int(cfg.start_timeout)),
        "FW_IDA_PROBE_TIMEOUT": str(int(cfg.probe_timeout)),
        "FW_IDA_INSTANCES": cfg.instances_dir,   # 收尾时 worker 注册表要用
        "IDA_MCP_URL": cfg.url,                  # 旧接口兼容
    }


class IdAdapter(Adapter):
    """IDA 的 ensure/call/health/close。只被 manifest(kind=feature, adapter=ida)引用。"""

    def __init__(self, root: str) -> None:
        self.root = root
        self._started = False
        self.last_error: str | None = None
        self.cfg = load_ida_config(root)

    # -- lifecycle --
    def ensure(self, ctx: ToolContext) -> dict | None:
        """幂等启动 supervisor;配置/启动失败必须**显式**返回错误(不能静默继续)。"""
        cfg = self.cfg
        if _probe_enabled() and _supervisor_up(ctx, cfg):
            self._started = True
            return None

        problems = cfg.problems()
        if problems:
            # 环境配置问题 ≠ IDA 分析失败:报清楚缺哪个文件/目录/挂载点
            self.last_error = "; ".join(problems)
            return {"status": "error",
                    "error": f"IDA 环境配置不可用: {self.last_error}",
                    "meta": {"capability": "debug.ida", "provider": "ida:supervisor",
                             "config_source": cfg.source, "config_problems": problems}}

        script = os.path.join(self.root, "tools", "ida_start.sh")
        if not os.path.isfile(script):
            return {"status": "error", "error": f"IDA 启动脚本不存在: {script}",
                    "meta": {"capability": "debug.ida", "provider": "ida:supervisor"}}
        argv = ["bash", script]
        init = os.environ.get("FW_IDA_INIT")
        if init:
            argv.append(init)
        out = run_process(ctx, argv, timeout=cfg.start_timeout, env=_shell_env(cfg))
        if out.get("status") != "ok":
            detail = (_tail(out.get("stdout_path")) + _tail(out.get("stderr_path"))).strip()
            self.last_error = detail[-400:] or out.get("status")
            return {"status": "error",
                    "error": f"IDA supervisor 启动失败({out.get('status')}): {self.last_error}",
                    "meta": {"capability": "debug.ida", "provider": "ida:supervisor",
                             "startup_exit": out.get("exit_code"), "config_source": cfg.source}}
        if _probe_enabled() and not _supervisor_up(ctx, cfg):
            detail = (_tail(out.get("stdout_path")) + _tail(out.get("stderr_path"))).strip()
            self.last_error = detail[-400:]
            return {"status": "error",
                    "error": f"IDA 启动脚本返回成功,但 supervisor 不可达({cfg.url}): {self.last_error}",
                    "meta": {"capability": "debug.ida", "provider": "ida:supervisor",
                             "url": cfg.url, "config_source": cfg.source}}
        self._started = True
        return None

    # -- call --
    def call(self, ctx: ToolContext, m: Manifest, args: dict) -> dict:
        cfg = self.cfg
        script = os.path.join(self.root, "tools", "ida_call.sh")
        if not os.path.isfile(script):
            return {"status": "error", "error": f"IDA 调用脚本不存在: {script}"}
        argv = ["bash", script]
        raw = args.get("args") or ""
        if raw:
            import shlex

            argv.extend(shlex.split(raw))
        out = run_process(ctx, argv, timeout=num(args.get("timeout"), m.timeout),
                          env=_shell_env(cfg))
        out.setdefault("meta", {})
        out["meta"].update({"capability": "debug.ida", "provider": "ida:supervisor",
                            "arch": args.get("arch"), "url": cfg.url})
        # 输出若能解析成 JSON,就把它作为结构化 text(观测器还会再抽一层字段)
        text = (_tail(out.get("stdout_path")) if out.get("stdout_path") else "")
        if text.strip().startswith("{"):
            try:
                json.loads(text)
                out["text"] = text
            except json.JSONDecodeError:
                pass
        return out

    def close(self) -> None:
        """默认**不停**共享 supervisor(多个研究会话可能共用);显式开启才停,且只停本环境。"""
        # 收尾时重新读一次配置:显式开关(env/配置文件)以**当前**值为准
        cfg = load_ida_config(self.root)
        if not self._started or not cfg.stop_on_close:
            return
        script = os.path.join(self.root, "tools", "ida_stop.sh")
        if not os.path.isfile(script):
            return
        import subprocess

        with contextlib_suppress():
            subprocess.run(["bash", script], cwd=self.root, timeout=90,
                           env={**os.environ, **_shell_env(cfg)},
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._started = False

    def health(self) -> dict:
        return {"status": "ok" if self._started else "idle", "started": self._started,
                "last_error": self.last_error, "url": self.cfg.url,
                "stage": self.cfg.stage, "config_source": self.cfg.source,
                "config_problems": self.cfg.problems()}


class contextlib_suppress:
    """局部极小的 suppress(避免为了它 import contextlib 影响可读性)。"""

    def __enter__(self) -> None:
        return None

    def __exit__(self, exc_type, exc, tb) -> bool:
        return exc_type is not None and issubclass(exc_type, OSError)


def factory(root: str) -> Adapter:
    return IdAdapter(root)
