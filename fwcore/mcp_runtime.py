"""把 MCP 服务器真正接进 Agent Runtime(工具注册 → 模型选择 → 执行 → 记录 → 清理)。

设计要点(对齐 Pi 的工具生命周期)
- **注册即探测**:只有 `initialize` + `tools/list` 成功的服务器才把工具注册进 Registry;
  不可达/未启用的服务器**如实记录状态**,不注册、不影响其它工具。
- **命名规范**:OpenAI 兼容端点要求 `^[A-Za-z0-9_-]+$` ⇒ 工具名 = `mcp_<server>_<tool>`(非法字符转 `_`)。
- **Schema 映射**:MCP 的 `inputSchema` 直接作为 ToolSpec.input_schema(Shape 相同)。
- **执行语义**:成功 → `ok` + 文本内容;工具内错误 → `error`(带 isError 原文);
  连接断/非法响应 → `error`;超时 → `timeout`;并记录 provider = `mcp:<server>`。
- **生命周期**:Runner 结束时统一 `close()`(杀进程组);取消/异常路径也必须关。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .mcp_client import MCPClient, MCPError, ServerSpec, load_servers  # noqa: F401
from .tools import Registry, ToolSpec

_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def tool_name(server: str, tool: str) -> str:
    """把 MCP 工具名映射到 OpenAI 兼容的合法工具名。"""
    return _SAFE.sub("_", f"mcp_{server}_{tool}")[:64]


@dataclass
class MCPBridge:
    """持有若干 MCP 客户端,并把它们的工具注册进一个 Registry。"""

    root: str
    registry: Registry
    specs: list[ServerSpec]
    timeout: float = 20.0
    call_timeout: float = 120.0
    clients: dict[str, MCPClient] = field(default_factory=dict)
    status: dict[str, dict] = field(default_factory=dict)
    registered: dict[str, list[str]] = field(default_factory=dict)
    closed: bool = False

    def start(self) -> dict[str, dict]:
        """连接 + 注册;单个服务器失败不影响其它。可重复调用(已关闭的桥会重新打开)。"""
        self.closed = False
        self.status.clear()
        self.registered.clear()
        for spec in self.specs:
            if not spec.enabled:
                self.status[spec.name] = {"status": "disabled"}
                continue
            if spec.transport != "stdio":
                self.status[spec.name] = {"status": "unsupported_transport", "detail": f"url={spec.url}"}
                continue
            client = MCPClient(spec, timeout=self.timeout)
            try:
                client.start()
                client.initialize()
                tools = client.list_tools()
            except (MCPError, OSError) as e:
                client.close()
                self.status[spec.name] = {"status": "unreachable", "detail": str(e)[:300]}
                continue
            self.clients[spec.name] = client
            names = []
            for t in tools:
                name = tool_name(spec.name, t["name"])
                if name in self.registry.specs:
                    # 桥重开(跨轮/恢复)时工具可能已在 Registry:保留既有定义,不重复注册
                    names.append(name)
                    continue
                self.registry.register(ToolSpec(
                    name=name,
                    description=f"[MCP:{spec.name}] {t.get('description', '')}".strip(),
                    input_schema=t.get("input_schema") or {"type": "object", "properties": {}},
                    handler=self._make_handler(spec.name, t["name"]),
                    risk="high",            # MCP 工具可能触碰外部世界 ⇒ 走确认/授权
                    external=self._is_external(t),
                ))
                names.append(name)
            self.registered[spec.name] = names
            self.status[spec.name] = {"status": "ready", "tools": names,
                                      "server": (tools and spec.name) or spec.name}
        return self.status

    @staticmethod
    def _is_external(tool: dict) -> bool:
        """保守判定:名字/描述里带外部动作关键词的 MCP 工具,永久需要人类确认。"""
        blob = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
        return any(k in blob for k in ("submit", "upload", "publish", "send", "login", "http request"))

    def _make_handler(self, server: str, remote_name: str):
        def _handler(ctx, args: dict) -> dict:
            client = self.clients.get(server)
            if client is None:
                return {"status": "error", "error": f"MCP 服务器 {server} 当前不可用",
                        "meta": {"provider": f"mcp:{server}", "capability": f"mcp.{server}"}}
            meta = {"provider": f"mcp:{server}", "capability": f"mcp.{server}"}
            try:
                out = client.call_tool(remote_name, args, timeout=self.call_timeout)
            except MCPError as e:
                msg = str(e)
                status = "timeout" if "超时" in msg else "error"
                return {"status": status, "error": msg[:500], "meta": meta}
            except (OSError, ValueError) as e:
                return {"status": "error", "error": f"{type(e).__name__}: {e}"[:500], "meta": meta}
            if out.get("is_error"):
                return {"status": "error", "error": (out.get("text") or "MCP tool error")[:500],
                        "meta": meta, "observation": {"status": "error", "text": out.get("text", "")[:1000]}}
            return {"status": "ok", "text": out.get("text", "")[:4000],
                    "meta": meta,
                    "observation": {"status": "ok", "text": out.get("text", "")[:1000],
                                    "content_items": len(out.get("content") or [])}}
        return _handler

    def close(self) -> None:
        """幂等:重复调用安全(关闭后 clients 为空、closed=True)。"""
        for name, client in list(self.clients.items()):
            try:
                client.close()
            finally:
                self.clients.pop(name, None)
        self.closed = True

    def health(self) -> dict:
        """看护/CLI 可观测:MCP 是否还活着、注册了哪些工具。"""
        return {
            "closed": self.closed,
            "clients": sorted(self.clients),
            "servers": {k: v.get("status") for k, v in self.status.items()},
            "registered": {k: len(v) for k, v in self.registered.items()},
        }


def bridge_from_config(registry: Registry, root: str, config_paths: list[str] | None = None,
                       *, timeout: float = 20.0, call_timeout: float = 120.0) -> MCPBridge:
    """从配置建桥:`state/mcp.json`(本框架)优先,其次 `~/.pi/agent/mcp.json`(过渡兼容)。"""
    import os

    if config_paths is None:
        local = os.path.join(root, "state", "mcp.json")
        # 本地配置是权威:有就只用它(合并用户 Pi 配置会把无关服务器拉进来、还会拖慢启动)
        paths = [local] if os.path.isfile(local) else [os.path.expanduser("~/.pi/agent/mcp.json")]
    else:
        paths = list(config_paths)
    specs = list(load_servers(paths).values())
    return MCPBridge(root=root, registry=registry, specs=specs,
                     timeout=timeout, call_timeout=call_timeout)
