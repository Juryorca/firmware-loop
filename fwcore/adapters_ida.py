"""IDA(Windows,经 WSL)特色适配器:自动启动 supervisor → 调用 → 健康检查 → 可选收尾。

为什么它要单独一层(而不是普通 shell_exec)
- 它不是"执行一条命令":有**生命周期**(supervisor 常驻、端口 8745、IDB 暂存、幂等启动)、
  有**跨进程状态**(WSL 侧脚本 + Windows 侧 IDA worker)、有**明确失败面**(worker 没激活/端口不通)。
- 但同时:Agent Loop 不需要知道 Windows/端口/暂存目录 —— 它只看到 `ida_query` 这个普通工具。
  统一契约(manifest + 适配器 + Registry)负责这件事,IDA 的细节留在这里。

修掉的既有缺陷
- 旧实现:`shell_exec(ctx, "bash tools/ida_start.sh")` 的返回**根本没检查** ⇒ supervisor 起不来也照样
  往下调 `ida_call.sh`,模型拿到的是"调用失败"而不是"启动失败"(错误归因错、也更难恢复)。
- 这里:启动失败立刻返回明确错误(带日志尾),不进入调用阶段。
"""

from __future__ import annotations

import json
import os

from .toolreg import Adapter, Manifest, num, run_process
from .tools import ToolContext

PORT = 8745
PROBE_URL = f"http://127.0.0.1:{PORT}/mcp"


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


def _supervisor_up(ctx: ToolContext) -> bool:
    """探测 supervisor 是否在线(只读、带超时;不启动任何东西)。"""
    if not _probe_enabled():
        return False
    out = run_process(ctx, ["curl", "-s", "--noproxy", "*", "--max-time", "3",
                            "-o", os.devnull, "-w", "%{http_code}", PROBE_URL],
                      timeout=6)
    code = ""
    try:
        with open(out.get("stdout_path") or "", encoding="utf-8", errors="replace") as f:
            code = f.read().strip()
    except OSError:
        code = ""
    return code not in ("", "000")


class IdAdapter(Adapter):
    """IDA 的 ensure/call/health/close。只被 manifest(kind=feature, adapter=ida)引用。"""

    def __init__(self, root: str) -> None:
        self.root = root
        self._started = False
        self.last_error: str | None = None

    # -- lifecycle --
    def ensure(self, ctx: ToolContext) -> dict | None:
        """幂等启动 supervisor;失败必须**显式**返回错误(不能静默继续)。"""
        if _probe_enabled() and _supervisor_up(ctx):
            self._started = True
            return None
        script = os.path.join(self.root, "tools", "ida_start.sh")
        if not os.path.isfile(script):
            return {"status": "error", "error": f"IDA 启动脚本不存在: {script}",
                    "meta": {"capability": "debug.ida", "provider": "ida:supervisor"}}
        argv = ["bash", script]
        init = os.environ.get("FW_IDA_INIT")
        if init:
            argv.append(init)
        out = run_process(ctx, argv, timeout=num(os.environ.get("FW_IDA_START_TIMEOUT"), 150))
        if out.get("status") != "ok":
            detail = (_tail(out.get("stdout_path")) + _tail(out.get("stderr_path"))).strip()
            self.last_error = detail[-400:] or out.get("status")
            return {"status": "error",
                    "error": f"IDA supervisor 启动失败({out.get('status')}): {self.last_error}",
                    "meta": {"capability": "debug.ida", "provider": "ida:supervisor",
                             "startup_exit": out.get("exit_code")}}
        if _probe_enabled() and not _supervisor_up(ctx):
            detail = (_tail(out.get("stdout_path")) + _tail(out.get("stderr_path"))).strip()
            self.last_error = detail[-400:]
            return {"status": "error",
                    "error": f"IDA 启动脚本返回成功,但 supervisor 不可达: {self.last_error}",
                    "meta": {"capability": "debug.ida", "provider": "ida:supervisor"}}
        self._started = True
        return None

    # -- call --
    def call(self, ctx: ToolContext, m: Manifest, args: dict) -> dict:
        script = os.path.join(self.root, "tools", "ida_call.sh")
        if not os.path.isfile(script):
            return {"status": "error", "error": f"IDA 调用脚本不存在: {script}"}
        argv = ["bash", script]
        raw = args.get("args") or ""
        if raw:
            import shlex

            argv.extend(shlex.split(raw))
        out = run_process(ctx, argv, timeout=num(args.get("timeout"), m.timeout))
        out.setdefault("meta", {})
        out["meta"].update({"capability": "debug.ida", "provider": "ida:supervisor",
                            "arch": args.get("arch")})
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
        """默认**不停**共享 supervisor(多个研究会话可能共用);需要时显式开启。"""
        if not self._started or os.environ.get("FW_IDA_STOP_ON_CLOSE") != "1":
            return
        script = os.path.join(self.root, "tools", "ida_stop.sh")
        if os.path.isfile(script):
            import subprocess

            subprocess.run(["bash", script], cwd=self.root, timeout=60,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._started = False

    def health(self) -> dict:
        return {"status": "ok" if self._started else "idle", "started": self._started,
                "last_error": self.last_error, "port": PORT}


def factory(root: str) -> IdAdapter:
    return IdAdapter(root)
