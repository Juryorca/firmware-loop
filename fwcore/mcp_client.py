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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


class MCPError(RuntimeError):
    pass


class MCPConfigError(MCPError):
    """MCP 配置文件存在但不可读/JSON 损坏/结构非法;不能静默跳过。"""


@dataclass
class ServerSpec:
    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict = field(default_factory=dict)
    url: str | None = None          # HTTP transport(Streamable HTTP;JSON/SSE + session id)
    enabled: bool = True
    # external: 配置显式声明该服务器是否"会碰外部世界"(网络/登录态/提交)。
    # None = 没声明 ⇒ 一律按 external=True 处理(默认拒绝,不靠工具名猜)。
    external: bool | None = None

    @property
    def transport(self) -> str:
        return "stdio" if self.command and not self.url else "http"


def load_servers(config_paths: list[str]) -> dict[str, ServerSpec]:
    """按顺序合并配置(靠前的优先;同名不覆盖)。配置损坏必须显式报错。"""
    out: dict[str, ServerSpec] = {}
    for path in config_paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except OSError as e:
            raise MCPConfigError(f"{path}: 无法读取: {type(e).__name__}: {e}") from e
        except json.JSONDecodeError as e:
            raise MCPConfigError(
                f"{path}: JSON 解析失败(第 {e.lineno} 行第 {e.colno} 列): {e.msg}") from e
        if not isinstance(data, dict):
            raise MCPConfigError(f"{path}: 顶层必须是 JSON object")
        servers = data.get("mcpServers")
        if servers is None:
            servers = {}
        if not isinstance(servers, dict):
            raise MCPConfigError(f"{path}: mcpServers 必须是 object")
        for name, srv in servers.items():
            if not isinstance(name, str) or not isinstance(srv, dict):
                raise MCPConfigError(f"{path}: mcpServers.{name!r} 必须是 object")
            if name in out:
                continue
            args = srv.get("args") or []
            env = srv.get("env") or {}
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise MCPConfigError(f"{path}: {name}.args 必须是字符串数组")
            if not isinstance(env, dict):
                raise MCPConfigError(f"{path}: {name}.env 必须是 object")
            url = srv.get("url")
            if url is not None and not isinstance(url, str):
                raise MCPConfigError(f"{path}: {name}.url 必须是字符串")
            command = srv.get("command") or ""
            if not isinstance(command, str):
                raise MCPConfigError(f"{path}: {name}.command 必须是字符串")
            out[name] = ServerSpec(
                name=name,
                command=command,
                args=args,
                env=env,
                url=url,
                enabled=srv.get("enabled", True) is not False,
                external=(None if srv.get("external") is None else bool(srv.get("external"))),
            )
    return out


MAX_MESSAGE_BYTES = 8 * 1024 * 1024


class MCPClient:
    """最小 stdio MCP 客户端(initialize / tools.list / tools.call)。"""

    def __init__(self, spec: ServerSpec, *, timeout: float = 20.0,
                 pgids_path: str | None = None):
        self.spec = spec
        self.timeout = timeout
        self.pgids_path = pgids_path
        self._proc: subprocess.Popen | None = None
        self._next_id = 0
        self._buf = b""
        self._http_session: str | None = None
        self._stderr_tail = bytearray()
        self._stderr_thread: threading.Thread | None = None

    # ---------- 生命周期 ----------
    def __enter__(self) -> MCPClient:
        self.start()
        return self

    def __exit__(self, *exc) -> bool:
        self.close()
        return False

    def start(self) -> None:
        if self.spec.transport == "http":
            self._http_url()
            return
        env = dict(os.environ, **{k: str(v) for k, v in self.spec.env.items()})
        self._proc = subprocess.Popen(
            [self.spec.command, *self.spec.args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env=env, start_new_session=True,
        )
        # stderr 必须持续读走:服务端日志写满 pipe 会让整个 MCP 调用永久卡住。
        if self._proc.stderr is not None:
            err_stream = self._proc.stderr
            self._stderr_thread = threading.Thread(
                target=self._drain_stderr, args=(err_stream,), daemon=True,
                name=f"fwcore-mcp-stderr-{self.spec.name}")
            self._stderr_thread.start()
        if self.pgids_path:
            # 登记到 run 的 .pgids:硬崩溃后 claim/recover/看护能按 starttime 核验并清掉孤儿 MCP
            try:
                from .watchdog import _proc_starttime
                os.makedirs(os.path.dirname(self.pgids_path), exist_ok=True)
                with open(self.pgids_path, "a", encoding="utf-8") as f:
                    f.write(f"{os.getpgid(self._proc.pid)} "
                            f"{_proc_starttime(self._proc.pid) or 0}\n")
            except OSError:
                pass

    def close(self) -> None:
        if not self._proc:
            return
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(self._proc.pid), signal.SIGKILL)
        with contextlib.suppress(subprocess.TimeoutExpired):
            self._proc.wait(timeout=5)
        self._proc = None

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
                raw = resp.read(MAX_MESSAGE_BYTES + 1)
                if len(raw) > MAX_MESSAGE_BYTES:
                    raise MCPError(f"{self.spec.name}: HTTP MCP 响应超过 {MAX_MESSAGE_BYTES} 字节上限")
                content_type = (resp.headers.get("Content-Type") or "").lower()
                if not raw:
                    return {}
                if "text/event-stream" not in content_type:
                    return json.loads(raw.decode("utf-8", errors="replace"))
                # SSE:取第一个 data: JSON 行;忽略注释/心跳/其它事件。
                for line in raw.decode("utf-8", errors="replace").splitlines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        return json.loads(data)
                    except json.JSONDecodeError:
                        continue
                raise MCPError(f"{self.spec.name}: SSE 中没有 JSON-RPC 响应")
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
            if "error" in msg:
                raise MCPError(f"{self.spec.name}: {method} 失败: {msg['error']}")
            return msg.get("result") or {}
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
        if self.spec.transport == "http":
            self._http_post({"jsonrpc": "2.0", "method": method, "params": params or {}},
                            self.timeout)
            return
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
