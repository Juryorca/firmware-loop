"""把 MCP 服务器真正接进 Agent Runtime(工具注册 → 模型选择 → 执行 → 记录 → 清理)。

设计要点(工具生命周期:注册/调用/超时/取消)
- **注册即探测**:只有 `initialize` + `tools/list` 成功的服务器才把工具注册进 Registry;
  不可达/未启用的服务器**如实记录状态**,不注册、不影响其它工具。
- **命名规范**:Pi 风格 `mcp__<server>__<tool>`；只保留 `[A-Za-z0-9_]`，≤64 字符，撞名/超长用 8 位哈希后缀。
- **Schema 映射**:MCP 的 `inputSchema` 直接作为 ToolSpec.input_schema(Shape 相同)。
- **执行语义**:成功 → `ok` + Pi MCP 20 KiB 中间截断文本 + structuredContent;工具内错误 → `error`(带 isError 原文);
  连接断/非法响应 → `error`;超时 → `timeout`;并记录 provider = `mcp:<server>`。
- **生命周期**:Runner 结束时统一 `close()`(杀进程组);取消/异常路径也必须关。
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from .mcp_client import MCPClient, MCPError, ServerSpec
from .mcp_config import discover_mcp_config, load_mcp_config
from .mcp_content import (
    MCP_OUTPUT_MAX_BYTES,
    bound_parts,
    content_parts,
    parts_images,
    resource_parts,
)
from .output import limit_structured_for_model
from .tools import Registry, ToolSpec

_SAFE = re.compile(r"[^A-Za-z0-9_]")
MAX_RESOURCE_PAGES = 50


def _base_tool_name(server: str, tool: str) -> str:
    # Pi: mcp__<server>__<tool>; only [A-Za-z0-9_] is kept.
    return _SAFE.sub("_", f"mcp__{server}__{tool}")


def _hashed_tool_name(server: str, tool: str) -> str:
    import hashlib

    h = hashlib.sha256(f"{server}\x00{tool}".encode()).hexdigest()[:8]
    return _base_tool_name(server, tool)[:55] + "_" + h


def tool_name(server: str, tool: str) -> str:
    """Pi MCP 工具名：mcp__server__tool，超 64 字符用 8 位哈希后缀。"""
    base = _base_tool_name(server, tool)
    return base if len(base) <= 64 else _hashed_tool_name(server, tool)


def disambiguated_name(server: str, tool: str) -> str:
    """规范化撞名时使用同一输入的 8 位哈希后缀。"""
    return _hashed_tool_name(server, tool)


def _tool_exposure(spec: ServerSpec, tool: str) -> str:
    """Pi exposure resolution; fwcore currently materializes non-hidden tools directly.

    The important security property is that ``hidden`` is honored: a server or
    tool configured hidden must not be silently registered just because fwcore
    has no codemode/tool_search layer yet.
    """
    if tool in spec.tool_exposure:
        return spec.tool_exposure[tool]
    for pattern, exposure in spec.tool_exposure.items():
        if "*" in pattern and fnmatch.fnmatchcase(tool, pattern):
            return exposure
    return spec.exposure or "codemode"


def _bounded_mcp_structured(value):
    """模型可见结构化结果；大对象只留摘要和完整 JSON 临时路径。"""
    if value is None:
        return None, None
    try:
        return limit_structured_for_model(
            value, max_bytes=MCP_OUTPUT_MAX_BYTES, prefix="fwcore-mcp-structured"
        )
    except OSError as e:
        return {
            "_truncated": True,
            "error": f"完整结构化结果保存失败: {type(e).__name__}: {e}",
        }, None


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
    pgids_path: str | None = None
    config_error: str | None = None
    config_sources: list[str] = field(default_factory=list)
    close_errors: list[str] = field(default_factory=list)
    resource_tools_registered: bool = False

    def start(self) -> dict[str, dict]:
        """连接 + 注册;单个服务器失败不影响其它。可重复调用(已关闭的桥会重新打开)。"""
        if self.clients and not self.closed:
            # Starting an already-open bridge must not overwrite clients and leak
            # their process groups; close the previous incarnation first.
            self.close()
        self.closed = False
        self.status.clear()
        self.registered.clear()
        if self.config_error:
            self.status["_config"] = {
                "status": "config_error",
                "detail": self.config_error,
            }
        for spec in self.specs:
            if not spec.enabled:
                self.status[spec.name] = {"status": "disabled", "state": "disabled"}
                continue
            client = MCPClient(
                spec, timeout=spec.timeout or self.timeout, pgids_path=self.pgids_path
            )
            try:
                client.start()
                client.initialize()
            except (MCPError, OSError) as e:
                client.close()
                self.status[spec.name] = {
                    "status": "unreachable",
                    "state": "failed",
                    "detail": str(e)[:300],
                }
                continue
            tools_error = None
            try:
                tools = client.list_tools()
            except (MCPError, OSError) as e:
                # Resources-only MCP servers may not implement tools/list; keep them
                # only when they explicitly advertised resources.
                if not client.supports_resources:
                    client.close()
                    self.status[spec.name] = {
                        "status": "unreachable",
                        "state": "failed",
                        "detail": str(e)[:300],
                    }
                    continue
                tools = []
                tools_error = str(e)[:300]
            self.clients[spec.name] = client
            names = []
            hidden_tools: list[str] = []
            external = True if spec.external is None else bool(spec.external)
            collisions: list[str] = []
            for t in tools:
                exposure = _tool_exposure(spec, t["name"])
                if exposure == "hidden":
                    hidden_tools.append(t["name"])
                    continue
                name = tool_name(spec.name, t["name"])
                owner = self.claimed.get(name)
                if owner is None and name not in self.registry.specs:
                    final = name
                elif owner == (spec.name, t["name"]):
                    final = name  # 同一个工具重开桥 ⇒ 幂等
                else:
                    # 规范化后撞名(不同 server/tool 映射到同一名字):确定性加哈希后缀
                    final = disambiguated_name(spec.name, t["name"])
                    if final in self.claimed or final in self.registry.specs:
                        collisions.append(t["name"])
                        continue  # 明确拒绝,不静默绑错工具
                self.claimed[final] = (spec.name, t["name"])
                if final in self.registry.specs:
                    names.append(final)  # 已注册(桥重开)⇒ 保留既有定义
                    continue
                self.registry.register(
                    ToolSpec(
                        name=final,
                        description=f"[MCP:{spec.name}] {t.get('description', '')}".strip(),
                        input_schema=t.get("input_schema")
                        or {"type": "object", "properties": {}},
                        handler=self._make_handler(spec.name, t["name"]),
                        risk="high",  # MCP 工具走"授权内允许"(requires=mcp 另有一道门)
                        requires="mcp",  # 真实授权门:没有 allow_mcp 就拒绝
                        external=external,  # 由配置声明决定;未声明 ⇒ external(默认拒绝)
                        exposure=exposure,
                        namespace=spec.name,
                    )
                )
                names.append(final)
            self.registered[spec.name] = names
            st = {
                "status": "ready",
                "state": "connected",
                "tools": names,
                "server": spec.name,
                "external": external,
                "external_declared": spec.external is not None,
                "resources": bool(client.supports_resources),
                "exposure": {t["name"]: _tool_exposure(spec, t["name"]) for t in tools},
            }
            if hidden_tools:
                st["hidden_tools"] = hidden_tools
            hint = [t["name"] for t in tools if self._is_external(t)]
            if hint:
                st["external_hint"] = hint  # 仅作审计提示,**不参与**授权判定
                if not external:
                    st["note"] = (
                        "配置声明 external=false,但工具名/描述含外部动作关键词:"
                        "该声明由运维负责,调用仍受授权与 confirm 约束"
                    )
            if collisions:
                st["name_collisions"] = collisions
            if tools_error:
                st["tools_error"] = tools_error
                st["resources"] = bool(client.supports_resources)
            self.status[spec.name] = st
        self._register_resource_tools()
        return self.status

    def _content_dir(self) -> str:
        """图片/二进制资源的持久化目录（跨 checkpoint/process 可读，不写 DB）。"""
        return os.path.join(self.root, "state", "artifacts", "mcp-content")

    def _resource_servers(self) -> list[tuple[str, MCPClient]]:
        specs = {spec.name: spec for spec in self.specs}
        out: list[tuple[str, MCPClient]] = []
        for name, client in sorted(self.clients.items()):
            if not client.supports_resources:
                continue
            spec = specs.get(name)
            if spec is not None and spec.exposure == "hidden":
                continue
            out.append((name, client))
        return out

    def _replace_server_tools(self, server: str, tools: list[dict]) -> None:
        """刷新一个服务器的工具集合：先移除旧注册，再按当前 tools/list 重建。"""
        old = self.registered.get(server, [])
        for name in old:
            owner = self.claimed.get(name)
            if owner and owner[0] == server:
                self.registry.specs.pop(name, None)
                self.claimed.pop(name, None)
        spec = next((s for s in self.specs if s.name == server), None)
        if spec is None:
            return
        external = True if spec.external is None else bool(spec.external)
        names: list[str] = []
        hidden_tools: list[str] = []
        collisions: list[str] = []
        for t in tools:
            if _tool_exposure(spec, t["name"]) == "hidden":
                hidden_tools.append(t["name"])
                continue
            base = tool_name(server, t["name"])
            owner = self.claimed.get(base)
            if (owner is None and base not in self.registry.specs) or owner == (
                server,
                t["name"],
            ):
                final = base
            else:
                final = disambiguated_name(server, t["name"])
                if final in self.claimed or final in self.registry.specs:
                    collisions.append(t["name"])
                    continue
            self.claimed[final] = (server, t["name"])
            if final in self.registry.specs:
                names.append(final)
                continue
            self.registry.register(
                ToolSpec(
                    name=final,
                    description=f"[MCP:{server}] {t.get('description', '')}".strip(),
                    input_schema=t.get("input_schema")
                    or {"type": "object", "properties": {}},
                    handler=self._make_handler(server, t["name"]),
                    risk="high",
                    requires="mcp",
                    external=external,
                    exposure=_tool_exposure(spec, t["name"]),
                    namespace=spec.name,
                )
            )
            names.append(final)
        self.registered[server] = names
        st = self.status.setdefault(server, {"status": "ready", "server": server})
        st["tools"] = names
        st.pop("hidden_tools", None)
        if hidden_tools:
            st["hidden_tools"] = hidden_tools
        st["resources"] = (
            bool(self.clients[server].supports_resources)
            if server in self.clients
            else False
        )
        if collisions:
            st["name_collisions"] = collisions

    def refresh_tools(self, *, force: bool = False) -> bool:
        """拉取 tools/list；notification 后或每个 turn 开始调用，避免长期用旧列表。"""
        changed = False
        for server, client in list(self.clients.items()):
            if not force and not client.tools_changed:
                continue
            try:
                tools = client.list_tools()
            except (MCPError, OSError, ValueError) as e:
                self.status.setdefault(server, {})["tools_refresh_error"] = str(e)[:300]
                continue
            self._replace_server_tools(server, tools)
            client.tools_changed = False
            changed = True
        return changed

    def _register_resource_tools(self) -> None:
        """只在至少一个已连接服务器声明 resources 时注册三个只读工具。

        资源工具是跨服务器的入口，本身不知道该读哪一台；只要有一台是外部
        （未声明 external=false），就保守地按外部工具处理，避免用只读资源的
        名义绕过 MCP 的外部授权门。
        """
        resource_servers = self._resource_servers()
        if self.resource_tools_registered or not resource_servers:
            return
        resource_external = any(
            client.spec.external is None or bool(client.spec.external)
            for _, client in resource_servers
        )
        specs = [
            ToolSpec(
                "list_mcp_resources",
                "Lists resources provided by MCP servers. Omit server to list every page of every server.",
                {
                    "type": "object",
                    "properties": {
                        "server": {"type": "string"},
                        "cursor": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                handler=lambda ctx, args: self._list_resources_tool(args, "resources"),
                risk="low",
                requires="mcp",
                external=resource_external,
            ),
            ToolSpec(
                "list_mcp_resource_templates",
                "Lists URI templates for MCP resources. Omit server to list every page of every server.",
                {
                    "type": "object",
                    "properties": {
                        "server": {"type": "string"},
                        "cursor": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                handler=lambda ctx, args: self._list_resources_tool(
                    args, "resourceTemplates"
                ),
                risk="low",
                requires="mcp",
                external=resource_external,
            ),
            ToolSpec(
                "read_mcp_resource",
                "Reads an MCP resource by server and URI. Text becomes text and images become images; binary resources are saved.",
                {
                    "type": "object",
                    "properties": {
                        "server": {"type": "string"},
                        "uri": {"type": "string"},
                    },
                    "required": ["server", "uri"],
                    "additionalProperties": False,
                },
                handler=lambda ctx, args: self._read_resource_tool(args),
                risk="low",
                requires="mcp",
                external=resource_external,
            ),
        ]
        for spec in specs:
            if spec.name not in self.registry.specs:
                self.registry.register(spec)
        self.resource_tools_registered = True

    def _find_resource_client(self, server: str) -> MCPClient:
        clients = dict(self._resource_servers())
        client = clients.get(server)
        if client is not None:
            return client
        available = ", ".join(sorted(clients)) or "none"
        raise MCPError(
            f'MCP server "{server}" has no resources. Servers with resources: {available}'
        )

    @staticmethod
    def _visible_resource(item: dict) -> bool:
        """过滤 MCP App UI 资源；普通数据和资源模板保留。"""
        uri = str(item.get("uri") or item.get("uriTemplate") or "")
        if uri.startswith("ui://"):
            return False
        mime = str(item.get("mimeType") or "")
        return not re.search(r';\s*profile\s*=\s*"?mcp-app"?', mime, re.IGNORECASE)

    @staticmethod
    def _listed_resource(server: str, item: dict) -> dict:
        out = {k: v for k, v in item.items() if k not in ("_meta", "icons")}
        out["server"] = server
        return out

    def _resource_page(self, client: MCPClient, key: str, cursor: str | None) -> dict:
        if key == "resources":
            return client.list_resources(cursor)
        return client.list_resource_templates(cursor)

    def _list_resources_tool(self, args: dict, key: str) -> dict:
        server = args.get("server")
        cursor = args.get("cursor")
        if server is not None and (not isinstance(server, str) or not server.strip()):
            return {"status": "error", "error": "server 必须是非空字符串"}
        if cursor is not None and (not isinstance(cursor, str) or not cursor.strip()):
            return {"status": "error", "error": "cursor 必须是非空字符串"}
        try:
            if server:
                client = self._find_resource_client(server)
                result = self._resource_page(client, key, cursor)
                items = [
                    self._listed_resource(server, item)
                    for item in result.get(key, [])
                    if isinstance(item, dict) and self._visible_resource(item)
                ]
                payload = {"server": server, key: items}
                if result.get("nextCursor") is not None:
                    payload["nextCursor"] = result["nextCursor"]
            else:
                if cursor:
                    raise MCPError("cursor 只能与 server 一起使用")
                items = []
                errors = []
                for name, client in self._resource_servers():
                    try:
                        cur = None
                        pages = 0
                        while True:
                            result = self._resource_page(client, key, cur)
                            items.extend(
                                self._listed_resource(name, item)
                                for item in result.get(key, [])
                                if isinstance(item, dict)
                                and self._visible_resource(item)
                            )
                            cur = result.get("nextCursor")
                            if not cur:
                                break
                            pages += 1
                            if pages >= MAX_RESOURCE_PAGES:
                                raise MCPError(
                                    f"资源分页超过 {MAX_RESOURCE_PAGES} 页上限"
                                )
                    except (MCPError, OSError, ValueError) as e:
                        errors.append({"server": name, "error": str(e)[:300]})
                payload = {key: items}
                if errors:
                    payload["errors"] = errors
        except (MCPError, OSError, ValueError) as e:
            return {
                "status": "error",
                "error": str(e)[:500],
                "meta": {"provider": "mcp", "capability": "mcp.resources"},
            }
        text = json.dumps(payload, ensure_ascii=False)
        return self._resource_result(text, payload)

    def _read_resource_tool(self, args: dict) -> dict:
        server = args.get("server")
        uri = args.get("uri")
        if not isinstance(server, str) or not server.strip():
            return {"status": "error", "error": "server 必须是非空字符串"}
        if not isinstance(uri, str) or not uri.strip():
            return {"status": "error", "error": "uri 必须是非空字符串"}
        try:
            client = self._find_resource_client(server)
            result = client.read_resource(uri)
            contents = result.get("contents") if isinstance(result, dict) else []
            parts = resource_parts(
                contents,
                server=server,
                uri=uri,
                labels=len(contents or []) > 1,
                store_dir=self._content_dir(),
            )
            if not parts:
                parts = [{"type": "text", "text": f"Resource {uri} is empty."}]
        except (MCPError, OSError, ValueError) as e:
            return {
                "status": "error",
                "error": str(e)[:500],
                "meta": {"provider": "mcp", "capability": "mcp.resources"},
            }
        structured = {"server": server, "uri": uri, "contents": contents or []}
        return self._resource_result(parts=parts, structured=structured)

    def _resource_result(
        self,
        text: str | None = None,
        payload: dict | None = None,
        *,
        parts: list[dict] | None = None,
        structured: dict | None = None,
    ) -> dict:
        if parts is None:
            parts = [{"type": "text", "text": text or ""}]
        bounded = bound_parts(parts)
        result = {
            "status": "ok",
            "text": bounded["text"],
            "parts": bounded["parts"],
            "images": parts_images(bounded["parts"]),
            "truncated": bounded["truncated"],
            "meta": {"provider": "mcp", "capability": "mcp.resources"},
        }
        if bounded["full_output_path"]:
            result["full_output_path"] = bounded["full_output_path"]
        value = structured if structured is not None else payload
        if value is not None:
            result["structured"] = value
        return result

    @staticmethod
    def _is_external(tool: dict) -> bool:
        """保守判定:名字/描述里带外部动作关键词的 MCP 工具,永久需要人类确认。"""
        blob = f"{tool.get('name', '')} {tool.get('description', '')}".lower()
        return any(
            k in blob
            for k in ("submit", "upload", "publish", "send", "login", "http request")
        )

    def _make_handler(
        self, server: str, remote_name: str
    ) -> Callable[[object, dict], dict]:
        def _handler(ctx, args: dict) -> dict:
            client = self.clients.get(server)
            if client is None:
                return {
                    "status": "error",
                    "error": f"MCP 服务器 {server} 当前不可用",
                    "meta": {
                        "provider": f"mcp:{server}",
                        "capability": f"mcp.{server}",
                    },
                }
            meta = {"provider": f"mcp:{server}", "capability": f"mcp.{server}"}
            try:
                out = client.call_tool(remote_name, args, timeout=self.call_timeout)
            except MCPError as e:
                msg = str(e)
                status = "timeout" if "超时" in msg else "error"
                return {"status": status, "error": msg[:500], "meta": meta}
            except (OSError, ValueError) as e:
                return {
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}"[:500],
                    "meta": meta,
                }
            content = out.get("content") or []
            parts = content_parts(content, server=server, store_dir=self._content_dir())
            if not parts and out.get("structured") is not None:
                # Pi 的 fallback：没有 content blocks 时把 structuredContent 当文本。
                parts = [
                    {
                        "type": "text",
                        "text": json.dumps(
                            out["structured"], ensure_ascii=False, default=str
                        ),
                    }
                ]
            bounded = bound_parts(parts)
            structured, structured_path = _bounded_mcp_structured(out.get("structured"))
            content_types: dict[str, int] = {}
            for block in content:
                if isinstance(block, dict):
                    kind = str(block.get("type") or "unknown")
                    content_types[kind] = content_types.get(kind, 0) + 1
            images = parts_images(bounded["parts"])

            def _attach_common(result: dict) -> dict:
                result["parts"] = bounded["parts"]
                result["images"] = images
                if bounded["full_output_path"]:
                    result["full_output_path"] = bounded["full_output_path"]
                if structured is not None:
                    result["structured"] = structured
                if structured_path:
                    result["structured_full_output_path"] = structured_path
                if content:
                    result["content_items"] = len(content)
                    result["content_types"] = content_types
                return result

            text = bounded["text"]
            if out.get("is_error"):
                # 错误也走同一 20 KiB 中间截断并保存完整文本；图片仍保留。
                result = {
                    "status": "error",
                    "error": text or "MCP tool error",
                    "truncated": bounded["truncated"],
                    "meta": meta,
                    "observation": {
                        "status": "error",
                        "text": text[:2000],
                        "content_items": len(content),
                    },
                }
                return _attach_common(result)
            result = {
                "status": "ok",
                "text": text,
                "truncated": bounded["truncated"],
                "meta": meta,
                "observation": {
                    "status": "ok",
                    "text": text[:2000],
                    "content_items": len(content),
                },
            }
            return _attach_common(result)

        return _handler

    def attach_pgids(self, path: str) -> None:
        """eager 启动时还没有 run；首个 run 到来后补登记已存在的 MCP 进程组。"""
        if not path:
            return
        self.pgids_path = path
        for client in self.clients.values():
            client.pgids_path = path
            client.register_pgid()

    def _unregister_all(self) -> None:
        """Remove MCP-owned tools from the shared registry.

        Closing is a lifecycle boundary, not a permanent tombstone: a later
        ``start()`` re-registers the current tools. Removing them prevents a
        removed/renamed remote tool from staying callable after a restart.
        """
        for names in list(self.registered.values()):
            for name in names:
                self.registry.specs.pop(name, None)
                self.claimed.pop(name, None)
        for name in (
            "list_mcp_resources",
            "list_mcp_resource_templates",
            "read_mcp_resource",
        ):
            self.registry.specs.pop(name, None)
        self.registered.clear()
        self.resource_tools_registered = False

    def close(self) -> None:
        """幂等:重复调用安全(关闭后 clients 为空、closed=True)。"""
        self.close_errors.clear()
        for name, client in list(self.clients.items()):
            try:
                client.close()
            except Exception as e:  # noqa: BLE001 - one dead server must not leak the rest
                self.close_errors.append(f"{name}: {type(e).__name__}: {e}")
            finally:
                self.clients.pop(name, None)
        self._unregister_all()
        self.closed = True

    def health(self) -> dict:
        """看护/CLI 可观测:MCP 是否还活着、注册了哪些工具。"""
        servers: dict[str, str] = {}
        for name, value in self.status.items():
            if name == "_config":
                servers[name] = value.get("status", "config_error")
            elif self.closed:
                servers[name] = "closed"
            else:
                servers[name] = value.get("state") or value.get("status", "unknown")
        return {
            "closed": self.closed,
            "config_error": self.config_error,
            "config_sources": self.config_sources,
            "close_errors": list(self.close_errors),
            "clients": sorted(self.clients),
            "servers": servers,
            "registered": {k: len(v) for k, v in self.registered.items()},
        }


def bridge_from_config(
    registry: Registry,
    root: str,
    config_paths: list[str] | None = None,
    *,
    timeout: float = 20.0,
    call_timeout: float = 120.0,
    pgids_path: str | None = None,
) -> MCPBridge:
    """Build a bridge with Pi-compatible config sources.

    No second detection format: explicit ``FW_MCP_CONFIG`` wins; otherwise
    global Pi config, project ``.pi/mcp.json`` and legacy target
    ``state/mcp.json`` are merged. Invalid entries remain in ``config_error``
    while valid servers still register.
    """
    sources = discover_mcp_config(root) if config_paths is None else list(config_paths)
    cfg = load_mcp_config(sources, base_dir=root)
    err = "\n".join(cfg.errors) or None
    return MCPBridge(
        root=root,
        registry=registry,
        specs=list(cfg.servers.values()),
        timeout=timeout,
        call_timeout=call_timeout,
        pgids_path=pgids_path,
        config_error=err,
        config_sources=[s.path for s in cfg.sources],
    )
