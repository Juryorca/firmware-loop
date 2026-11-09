"""MCP stdio/HTTP 客户端(协议层)。

配置发现、Pi `mcpServers` 校验、global/project override 在
`fwcore.mcp_config` 中完成；本模块只负责 transport 与 JSON-RPC。

- stdio 子进程独立进程组；关闭时按 Pi 语义先 stdin EOF、再 SIGTERM，
  超时后才 SIGKILL 整组。
- 只实现必要的 `initialize` / `tools/list` / `tools/call` / resources；
  失败如实返回（unreachable ≠ 有工具）。
"""

from __future__ import annotations

import contextlib
import json
import os
import select
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Self

# 配置发现/校验交给 Pi 兼容适配层；这里保留 re-export，兼容既有调用方。
from .mcp_config import (
    ConfigSource,
    McpConfig,
    MCPConfigError,
    ServerSpec,
    discover_mcp_config,
    load_mcp_config,
    load_servers,
)


class MCPError(RuntimeError):
    pass


__all__ = [
    "ConfigSource", "MCPClient", "MCPConfigError", "MCPError", "McpConfig",
    "ServerSpec", "discover_mcp_config", "load_mcp_config", "load_servers", "probe",
]


# MCP 单条 transport 上限（Pi 用 16 MiB）；这是消息/传输硬上限，不是图片解码上限。
# 图片另有 4 MiB/张、8 张/结果的内容安全边界，二者独立。
MAX_MESSAGE_BYTES = 16 * 1024 * 1024


class MCPClient:
    """最小 stdio MCP 客户端(initialize / tools.list / tools.call)。"""

    def __init__(self, spec: ServerSpec, *, timeout: float = 20.0,
                 pgids_path: str | None = None):
        self.spec = spec
        self.timeout = float(spec.timeout or timeout)
        self.pgids_path = pgids_path
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._buf = b""
        self._http_session: str | None = None
        self.capabilities: dict = {}
        self.tools_changed = False
        self._stderr_tail = bytearray()
        self._stderr_thread: threading.Thread | None = None

    # ---------- 生命周期 ----------
    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False

    def start(self) -> None:
        if self.spec.transport == "http":
            self._http_url()
            return
        if self._proc is not None:
            if self._proc.poll() is None:
                return
            self.close()
        if self.spec.transport != "stdio":
            raise MCPError(f"{self.spec.name}: 无效 transport(需要 command 或 http(s) url)")
        env = dict(os.environ, **{k: str(v) for k, v in self.spec.env.items()})
        self._proc = subprocess.Popen(
            [self.spec.command, *self.spec.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, cwd=self.spec.cwd, start_new_session=True,
        )
        # stderr 必须持续读走:服务端日志写满 pipe 会让整个 MCP 调用永久卡住。
        if self._proc.stderr is not None:
            err_stream = self._proc.stderr
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(err_stream,), daemon=True,
                name=f"fwcore-mcp-stderr-{self.spec.name}")
            self._stderr_thread.start()
        self.register_pgid()

    def register_pgid(self) -> None:
        """把当前进程组登记到 .pgids；也供 eager 启动后绑定 run 时补登记。"""
        if not self.pgids_path or not self._proc:
            return
        try:
            from .watchdog import _proc_starttime
            os.makedirs(os.path.dirname(self.pgids_path), exist_ok=True)
            with open(self.pgids_path, "a", encoding="utf-8") as f:
                f.write(f"{os.getpgid(self._proc.pid)} "
                        f"{_proc_starttime(self._proc.pid) or 0}\n")
        except OSError:
            pass

    def close(self) -> None:
        """按 Pi 的顺序关闭:stdin EOF → SIGTERM → 超时 SIGKILL 进程组。"""
        proc = self._proc
        if not proc:
            return
        self._proc = None
        with contextlib.suppress(OSError, ValueError):
            if proc.stdin is not None:
                proc.stdin.close()
        pgid = None
        with contextlib.suppress(ProcessLookupError, PermissionError):
            pgid = os.getpgid(proc.pid)
            os.killpg(pgid, signal.SIGTERM)
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            pass
        # A server may fork a child that ignores SIGTERM after the parent exits.
        # Verify the whole group is gone before declaring cleanup complete.
        if pgid is not None:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, 0)
                os.killpg(pgid, signal.SIGKILL)
        if proc.poll() is None:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                proc.kill()
                proc.wait(timeout=1.0)

    def _drain_stderr(self, stream) -> None:
        """持续排空 stderr，只保留末尾一小段供诊断（避免 PIPE 写满死锁）。"""
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > 8192:
                    del self._stderr_tail[:-8192]
        except (OSError, ValueError):
            pass

    def last_stderr(self) -> str:
        return bytes(self._stderr_tail).decode("utf-8", errors="replace")

    # ---------- 协议 ----------
    def _send(self, obj: dict) -> None:
        if not self._proc or not self._proc.stdin:
            raise MCPError("连接未建立")
        self._proc.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode())
        self._proc.stdin.flush()

    def _http_url(self) -> str:
        """MCP HTTP 端点必须是 http(s) 且有 host；防止配置成 file:// 读本地文件。"""
        url = self.spec.url or ""
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise MCPError(f"{self.spec.name}: HTTP transport 需要合法 http(s) URL,收到 {url[:80]!r}")
        return url

    def _http_post(self, payload: dict, timeout: float) -> dict:
        """MCP Streamable HTTP:POST JSON-RPC;兼容 application/json 与 SSE。"""
        url = self._http_url()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2024-11-05",
        }
        headers.update(self.spec.headers)
        if self._http_session:
            headers["Mcp-Session-Id"] = self._http_session
        # start() 已把 transport 限定为 http(s);这里仍是用户配置 URL,标注给静态扫描器。
        req = urllib.request.Request(  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST")
        try:
            host = urllib.parse.urlparse(self.spec.url or "").hostname or ""
            if host in ("127.0.0.1", "localhost", "::1"):
                # 本机 MCP 端点不能被 http_proxy 劫持
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                stream = opener.open(req, timeout=timeout)
            else:
                stream = urllib.request.urlopen(req, timeout=timeout)  # nosemgrep: python.lang.security.audit.dynamic-urllib-use-detected.dynamic-urllib-use-detected
            with stream as resp:
                session = resp.headers.get("Mcp-Session-Id")
                if session:
                    self._http_session = session
                content_type = (resp.headers.get("Content-Type") or "").lower()
                rid = payload.get("id")
                if rid is None:
                    # JSON-RPC notification 通常返回 202/空 body；不能为一条通知
                    # 去等一个可能永不结束的 SSE 流。
                    return {}
                if "text/event-stream" not in content_type:
                    raw = resp.read(MAX_MESSAGE_BYTES + 1)
                    if len(raw) > MAX_MESSAGE_BYTES:
                        raise MCPError(f"{self.spec.name}: HTTP MCP 响应超过 {MAX_MESSAGE_BYTES} 字节上限")
                    if not raw:
                        return {}
                    msg = json.loads(raw.decode("utf-8", errors="replace"))
                    if isinstance(msg, dict) and msg.get("method") == "notifications/tools/list_changed":
                        self.tools_changed = True
                    return msg
                # SSE 必须增量读：只解析到与本次请求 id 匹配的响应，不能等连接关闭。
                # 忽略注释/心跳/通知/其它请求的响应；支持一个事件多行 data。
                deadline = time.time() + timeout
                total = 0
                data_lines: list[str] = []
                while time.time() < deadline:
                    line = resp.readline(MAX_MESSAGE_BYTES + 1)
                    if not line:
                        break
                    total += len(line)
                    if total > MAX_MESSAGE_BYTES:
                        raise MCPError(f"{self.spec.name}: SSE 响应超过 {MAX_MESSAGE_BYTES} 字节上限")
                    if line.endswith(b"\n"):
                        line = line[:-1]
                    if line.endswith(b"\r"):
                        line = line[:-1]
                    if not line:
                        if not data_lines:
                            continue
                        data = "\n".join(data_lines).strip()
                        data_lines = []
                        if not data or data == "[DONE]":
                            continue
                        try:
                            msg = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if isinstance(msg, dict) and msg.get("id") == rid:
                            return msg
                        if isinstance(msg, dict) and msg.get("method") == "notifications/tools/list_changed":
                            self.tools_changed = True
                        continue
                    if line.startswith(b":"):
                        continue                         # SSE comment / heartbeat
                    if line.startswith(b"data:"):
                        data_lines.append(line[5:].lstrip().decode("utf-8", errors="replace"))
                if data_lines:
                    data = "\n".join(data_lines).strip()
                    if data and data != "[DONE]":
                        try:
                            msg = json.loads(data)
                        except json.JSONDecodeError:
                            msg = None
                        if isinstance(msg, dict) and msg.get("id") == rid:
                            return msg
                        if isinstance(msg, dict) and msg.get("method") == "notifications/tools/list_changed":
                            self.tools_changed = True
                raise MCPError(f"{self.spec.name}: SSE 中没有匹配 id={rid} 的 JSON-RPC 响应")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            raise MCPError(f"{self.spec.name}: HTTP {e.code}: {body}") from e
        except urllib.error.URLError as e:
            raise MCPError(f"{self.spec.name}: HTTP 连接失败: {e.reason}") from e
        except (OSError, ValueError) as e:
            raise MCPError(f"{self.spec.name}: HTTP 请求失败: {type(e).__name__}: {e}") from e

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
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("method") == "notifications/tools/list_changed":
                    self.tools_changed = True
                return msg
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
            if len(self._buf) > MAX_MESSAGE_BYTES:
                raise MCPError(f"{self.spec.name}: 单条 MCP 消息超过 {MAX_MESSAGE_BYTES} 字节上限")
        raise MCPError(f"{self.spec.name}: 等待响应超时({self.timeout}s)")

    def request(self, method: str, params: dict | None = None, timeout: float | None = None) -> dict:
        self._next_id += 1
        rid = self._next_id
        if self.spec.transport == "http":
            msg = self._http_post({"jsonrpc": "2.0", "id": rid, "method": method,
                                   "params": params or {}},
                                  timeout if timeout is not None else self.timeout)
            if not isinstance(msg, dict):
                raise MCPError(f"{self.spec.name}: 非法 JSON-RPC 响应(顶层不是 object)")
            if "error" in msg:
                raise MCPError(f"{self.spec.name}: {method} 失败: {msg['error']}")
            return msg.get("result") or {}
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        deadline = time.time() + (timeout if timeout is not None else self.timeout)
        while True:
            msg = self._read_message(deadline, timeout)
            if not isinstance(msg, dict):
                raise MCPError(f"{self.spec.name}: 非法 JSON-RPC 响应(顶层不是 object)")
            if msg.get("id") != rid:
                continue                      # 忽略通知/其它响应
            if "error" in msg:
                raise MCPError(f"{self.spec.name}: {method} 失败: {msg['error']}")
            return msg.get("result") or {}

    def notify(self, method: str, params: dict | None = None) -> None:
        if self.spec.transport == "http":
            self._http_post({"jsonrpc": "2.0", "method": method, "params": params or {}},
                            self.timeout)
            return
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---------- 便捷方法 ----------
    def initialize(self) -> dict:
        result = self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                             "clientInfo": {"name": "fwcore", "version": "0.4"}})
        if not isinstance(result, dict):
            raise MCPError(f"{self.spec.name}: initialize 结果不是 object")
        caps = result.get("capabilities")
        self.capabilities = caps if isinstance(caps, dict) else {}
        self.notify("notifications/initialized")
        return result

    @property
    def supports_resources(self) -> bool:
        return "resources" in self.capabilities

    def list_tools(self) -> list[dict]:
        """读取 tools/list 的全部页（MCP 用 nextCursor 分页）。"""
        out: list[dict] = []
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(50):
            result = self.request("tools/list", {} if not cursor else {"cursor": cursor})
            if not isinstance(result, dict):
                raise MCPError(f"{self.spec.name}: tools/list 结果不是 object")
            tools = result.get("tools")
            if tools is None:
                tools = []
            if not isinstance(tools, list):
                raise MCPError(f"{self.spec.name}: tools/list.tools 不是数组")
            for t in tools:
                if not isinstance(t, dict):
                    raise MCPError(f"{self.spec.name}: tools/list 含非 object 工具")
                name = t.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise MCPError(f"{self.spec.name}: tools/list 工具有空/非字符串 name")
                schema = t.get("inputSchema")
                if schema is None:
                    schema = {}
                if not isinstance(schema, dict):
                    raise MCPError(f"{self.spec.name}: 工具 {name} 的 inputSchema 不是 object")
                description = t.get("description", "")
                out.append({"name": name,
                            "description": description if isinstance(description, str) else "",
                            "input_schema": schema})
            nxt = result.get("nextCursor")
            if not nxt:
                return out
            if not isinstance(nxt, str):
                raise MCPError(f"{self.spec.name}: tools/list nextCursor 不是字符串")
            if nxt in seen:
                raise MCPError(f"{self.spec.name}: tools/list 返回重复 cursor")
            seen.add(nxt)
            cursor = nxt
        raise MCPError(f"{self.spec.name}: tools/list 分页超过 50 页上限")

    # ---------- Resources ----------
    def list_resources(self, cursor: str | None = None) -> dict:
        params = {} if not cursor else {"cursor": cursor}
        result = self.request("resources/list", params)
        return result if isinstance(result, dict) else {}

    def list_resource_templates(self, cursor: str | None = None) -> dict:
        params = {} if not cursor else {"cursor": cursor}
        result = self.request("resources/templates/list", params)
        return result if isinstance(result, dict) else {}

    def read_resource(self, uri: str) -> dict:
        if not isinstance(uri, str) or not uri.strip():
            raise MCPError(f"{self.spec.name}: resource uri 不能为空")
        result = self.request("resources/read", {"uri": uri})
        return result if isinstance(result, dict) else {}

    def call_tool(self, name: str, args: dict | None = None, timeout: float | None = None) -> dict:
        result = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout=timeout)
        if not isinstance(result, dict):
            raise MCPError(f"{self.spec.name}: tools/call 结果不是 object")
        content = result.get("content")
        if content is None:
            content = []
        if not isinstance(content, list):
            raise MCPError(f"{self.spec.name}: tools/call.content 不是数组")
        text = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
        return {"is_error": bool(result.get("isError")), "content": content,
                "text": text, "structured": result.get("structuredContent")}


def probe(spec: ServerSpec, *, timeout: float = 15.0) -> dict:
    """真实探针:只有 initialize + tools/list 都成功才算 reachable。"""
    if not spec.enabled:
        return {"server": spec.name, "status": "disabled", "evidence": "configured"}
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
