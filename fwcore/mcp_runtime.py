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
    """把 MCP 工具名映射到 OpenAI 兼容的合法工具名(单次映射,不去重)。"""
    return _SAFE.sub("_", f"mcp_{server}_{tool}")[:64]


def disambiguated_name(server: str, tool: str) -> str:
    """确定性消歧:规范化后可能撞名(截断/字符替换),用同一输入的哈希后缀区分。

    同一个 (server, tool) 永远得到同一个名字 ⇒ 跨轮/重启稳定;不同输入撞名则不同后缀。
    """
    import hashlib

    h = hashlib.sha256(f"{server}\x00{tool}".encode()).hexdigest()[:6]
    base = tool_name(server, tool)
    return base[:57] + "_" + h


@dataclass
class MCPBridge:
    """持有若干 MCP 客户端,并把它们的工具注册进一个 Registry。"""

    root: str
    registry: Registry
    specs: list[ServerSpec]
    timeout: float = 20.0
    call_timeout: float = 120.0
    clients: dict[str, MCPClient] = field(default_factory=dict)
    # 工具名 -> (server, tool):碰撞时据此判断"同名是不是同一个工具"
    claimed: dict[str, tuple[str, str]] = field(default_factory=dict)
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
            external = True if spec.external is None else bool(spec.external)
            collisions: list[str] = []
            for t in tools:
                name = tool_name(spec.name, t["name"])
                owner = self.claimed.get(name)
                if owner is None and name not in self.registry.specs:
                    final = name
                elif owner == (spec.name, t["name"]):
                    final = name                       # 同一个工具重开桥 ⇒ 幂等
                else:
                    # 规范化后撞名(不同 server/tool 映射到同一名字):确定性加哈希后缀
                    final = disambiguated_name(spec.name, t["name"])
                    if final in self.claimed or final in self.registry.specs:
                        collisions.append(t["name"])
                        continue                       # 明确拒绝,不静默绑错工具
                self.claimed[final] = (spec.name, t["name"])
                if final in self.registry.specs:
                    names.append(final)               # 已注册(桥重开)⇒ 保留既有定义
                    continue
                self.registry.register(ToolSpec(
                    name=final,
                    description=f"[MCP:{spec.name}] {t.get('description', '')}".strip(),
                    input_schema=t.get("input_schema") or {"type": "object", "properties": {}},
                    handler=self._make_handler(spec.name, t["name"]),
                    risk="high",          # MCP 工具走"授权内允许"(requires=mcp 另有一道门)
                    requires="mcp",       # 真实授权门:没有 allow_mcp 就拒绝
                    external=external,    # 由配置声明决定;未声明 ⇒ external(默认拒绝)
                ))
                names.append(final)
            self.registered[spec.name] = names
            st = {"status": "ready", "tools": names, "server": spec.name,
                  "external": external, "external_declared": spec.external is not None}
            hint = [t["name"] for t in tools if self._is_external(t)]
            if hint:
                st["external_hint"] = hint        # 仅作审计提示,**不参与**授权判定
                if not external:
                    st["note"] = ("配置声明 external=false,但工具名/描述含外部动作关键词:"
                                  "该声明由运维负责,调用仍受授权与 confirm 约束")
            if collisions:
                st["name_collisions"] = collisions
            self.status[spec.name] = st
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
    """从 fwcore 自己的配置建桥:`FW_MCP_CONFIG` 或 `state/mcp.json`。"""
    import os

    if config_paths is None:
        # 只认 fwcore 自己的配置(显式 FW_MCP_CONFIG 或 state/mcp.json);
        # 不隐式读 Pi 的 ~/.pi/agent/mcp.json —— 那是开发期遗产,不是 fwcore 的契约
        env_cfg = os.environ.get("FW_MCP_CONFIG")
        paths = [env_cfg] if env_cfg else [os.path.join(root, "state", "mcp.json")]
    else:
        paths = list(config_paths)
    specs = list(load_servers(paths).values())
    return MCPBridge(root=root, registry=registry, specs=specs,
                     timeout=timeout, call_timeout=call_timeout)
