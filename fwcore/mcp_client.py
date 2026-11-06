"""独立 MCP 客户端(stdio):发现/调用 MCP 工具(配置来自 fwcore 自己的文件)。

设计
- **配置来自 fwcore 自己的文件**:`state/mcp.json`(或 `FW_MCP_CONFIG`)→ 用户级 `~/.pi/agent/mcp.json`(过渡期兼容)。
- 传输 = stdio + 换行分隔 JSON-RPC;子进程用独立进程组,出错/关闭时整组杀掉(不留孤儿)。
- 只实现三个必要动作:`initialize` / `tools/list` / `tools/call`;失败要**如实**返回(unreachable ≠ 有工具)。
- 探针语义:`probe()` 只有在完成 initialize + tools/list 后才算 `reachable`,否则 `unreachable`。
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import signal
import subprocess
import time
from dataclasses import dataclass, field


class MCPError(RuntimeError):
    pass


@dataclass
class ServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    url: str | None = None          # HTTP transport:本实现暂不支持,会在 probe 里如实报
    enabled: bool = True
    # external: 配置显式声明该服务器是否"会碰外部世界"(网络/登录态/提交)。
    # None = 没声明 ⇒ 一律按 external=True 处理(默认拒绝,不靠工具名猜)。
    external: bool | None = None

    @property
    def transport(self) -> str:
        return "stdio" if self.command and not self.url else "http"


def load_servers(config_paths: list[str]) -> dict[str, ServerSpec]:
    """按顺序合并配置(靠前的优先;同名不覆盖)。"""
    out: dict[str, ServerSpec] = {}
    for path in config_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for name, srv in (data.get("mcpServers") or {}).items():
            if name in out or not isinstance(srv, dict):
                continue
            out[name] = ServerSpec(
                name=name,
                command=srv.get("command") or "",
                args=list(srv.get("args") or []),
                env=dict(srv.get("env") or {}),
                url=srv.get("url"),
                enabled=srv.get("enabled", True) is not False,
                external=(None if srv.get("external") is None else bool(srv.get("external"))),
            )
    return out


class MCPClient:
    """最小 stdio MCP 客户端(initialize / tools.list / tools.call)。"""

    def __init__(self, spec: ServerSpec, *, timeout: float = 20.0):
        self.spec = spec
        self.timeout = timeout
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._buf = b""

    # ---------- 生命周期 ----------
    def __enter__(self) -> MCPClient:
        self.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False

    def start(self) -> None:
        if self.spec.transport != "stdio":
            raise MCPError(f"{self.spec.name}: 目前只支持 stdio transport(url={self.spec.url})")
        env = dict(os.environ, **{k: str(v) for k, v in self.spec.env.items()})
        self._proc = subprocess.Popen(
            [self.spec.command, *self.spec.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, start_new_session=True,
        )

    def close(self) -> None:
        if not self._proc:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._proc.wait(timeout=5)
        self._proc = None

    # ---------- 协议 ----------
    def _send(self, obj: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise MCPError("连接未建立")
        self._proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        self._proc.stdin.flush()

    def _read_message(self, deadline: float, timeout: float | None = None) -> dict:
        if not self._proc or not self._proc.stdout:
            raise MCPError("连接未建立")
        while time.time() < deadline:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    return json.loads(line)
                except json.JSONDecodeError:
                    continue
            remaining = deadline - time.time()
            if remaining <= 0:
                raise MCPError(
                    f"{self.spec.name}: 等待响应超时({timeout if timeout is not None else self.timeout}s)")
            fd = self._proc.stdout.fileno()
            ready, _, _ = select.select([fd], [], [], remaining)   # 先等等待,再读 ⇒ 超时可控
            if not ready:
                raise MCPError(
                    f"{self.spec.name}: 等待响应超时({timeout if timeout is not None else self.timeout}s)")
            chunk = os.read(fd, 4096)
            if not chunk:
                raise MCPError(f"{self.spec.name}: 服务端已关闭连接")
            self._buf += chunk
        raise MCPError(f"{self.spec.name}: 等待响应超时({self.timeout}s)")

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        self._next_id += 1
        rid = self._next_id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        while True:
            msg = self._read_message(deadline, timeout)
            if msg.get("id") != rid:
                continue                      # 忽略通知/其它响应
            if "error" in msg:
                raise MCPError(f"{self.spec.name}: {method} 失败: {msg['error']}")
            return msg.get("result") or {}

    def notify(self, method: str, params: dict | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---------- 便捷方法 ----------
    def initialize(self) -> dict:
        result = self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                             "clientInfo": {"name": "fwcore", "version": "0.4"}})
        self.notify("notifications/initialized")
        return result

    def list_tools(self) -> list[dict]:
        result = self.request("tools/list")
        tools = result.get("tools") or []
        return [{"name": t.get("name"), "description": t.get("description", ""),
                 "input_schema": t.get("inputSchema") or {}} for t in tools]

    def call_tool(self, name: str, args: dict | None = None, timeout: float | None = None) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout=timeout)
        content = result.get("content") or []
        text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        return {"is_error": bool(result.get("isError")), "content": content, "text": text}


def probe(spec: ServerSpec, *, timeout: float = 15.0) -> dict:
    """真实探针:只有 initialize + tools/list 都成功才算 reachable。"""
    if not spec.enabled:
        return {"server": spec.name, "status": "disabled", "evidence": "configured"}
    if spec.transport != "stdio":
        return {"server": spec.name, "status": "unsupported_transport", "evidence": "configured",
                "detail": f"url={spec.url}"}
    client = MCPClient(spec, timeout=timeout)
    try:
        client.start()
        info = client.initialize()
        tools = client.list_tools()
        return {"server": spec.name, "status": "reachable", "evidence": "reachable",
                "server_info": info.get("serverInfo"), "tools": [t["name"] for t in tools]}
    except (MCPError, OSError) as e:
        return {"server": spec.name, "status": "unreachable", "evidence": "configured", "detail": str(e)[:300]}
    finally:
        client.close()
