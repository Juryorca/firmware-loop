"""Pi-compatible MCP configuration discovery and validation.

Pi cannot be imported here (Node/TypeScript), so this module is an adapter for
the configuration contract, not a second invention of it.  The reference is:

- ``docs/mcp.md`` in the Pi package
- ``dist/core/mcp-servers.js`` (``validateMcpServerConfig``)
- ``dist/extensions/mcp/config.js`` (global/project loading and overrides)

The important semantics kept here:

- global ``~/.pi/agent/mcp.json`` and project ``<root>/.pi/mcp.json``
- project overrides may change only ``enabled``, ``exposure`` and ``toolExposure``
- a project entry with ``command``/``url``/``type`` replaces the global entry
- invalid entries are reported and skipped without poisoning valid servers
- Pi's ``mcpServers`` shape, naming rules and stdio/HTTP fields are used as-is

fwcore adds one explicit security extension, ``external``; it is deliberately
kept out of the MCP protocol and is declared in config rather than guessed from
tool names.  Legacy ``state/mcp.json`` remains a target-level source so existing
fwcore configurations keep working; it is a compatibility layer, not a new MCP
schema.
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

MCP_EXPOSURES = ("codemode", "deferred", "direct", "hidden")
MCP_EXPOSURE_ALIASES = {"codemode-deferred": "codemode"}
SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
OVERRIDE_KEYS = frozenset({"enabled", "exposure", "toolExposure"})
DEFAULT_TIMEOUT_SECONDS = 60.0


class MCPConfigError(RuntimeError):
    """Raised by the strict compatibility wrapper when config cannot be used."""


class ConfigEntryError(ValueError):
    """One invalid entry; collected instead of aborting the whole config."""


@dataclass(frozen=True)
class ConfigSource:
    """One config file and the Pi scope it represents."""

    path: str
    scope: str = "global"  # global | project | target


@dataclass
class ServerSpec:
    """Validated MCP server entry.

    ``external`` is a fwcore-only security annotation: None means "not declared"
    and is treated conservatively by the runtime.  All other fields mirror Pi's
    MCP server configuration where fwcore supports them.
    """

    name: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float | None = None
    enabled: bool = True
    exposure: str | None = None
    tool_exposure: dict[str, str] = field(default_factory=dict)
    description: str = ""
    external: bool | None = None
    type: str | None = None
    source: str | None = None
    scope: str | None = None

    @property
    def transport(self) -> str:
        if self.type in ("http", "streamable-http") or self.url:
            return "http"
        if self.type == "stdio" or self.command:
            return "stdio"
        return "invalid"


@dataclass
class McpConfig:
    servers: dict[str, ServerSpec] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    sources: list[ConfigSource] = field(default_factory=list)
    auto_enable_codemode: bool | None = None


def mcp_namespace(server: str) -> str:
    """Pi's namespace collision key: '-' and '_' are equivalent."""
    return "mcp__" + server.replace("-", "_")


def _as_sources(
    config_paths: Sequence[str | ConfigSource] | None,
) -> list[ConfigSource]:
    if not config_paths:
        return []
    out: list[ConfigSource] = []
    for item in config_paths:
        if isinstance(item, ConfigSource):
            out.append(item)
            continue
        # A positional list is interpreted as global -> project (Pi order):
        # later full entries replace earlier ones; later override entries patch.
        scope = "global" if not out else "project"
        out.append(ConfigSource(str(item), scope))
    return out


def discover_mcp_config(
    root: str, *, env: Mapping[str, str] | None = None
) -> list[ConfigSource]:
    """Return Pi-compatible config sources for a fwcore target root.

    ``FW_MCP_CONFIG`` is an explicit fwcore override and, when present, is the
    only source.  Otherwise global and project sources are read, then the legacy
    target file if it exists.
    """
    env = os.environ if env is None else env
    explicit = env.get("FW_MCP_CONFIG")
    if explicit:
        return [ConfigSource(explicit, "target")]

    agent_dir = Path(env.get("PI_CODING_AGENT_DIR") or (Path.home() / ".pi" / "agent"))
    sources = [
        ConfigSource(str(agent_dir / "mcp.json"), "global"),
        ConfigSource(str(Path(root) / ".pi" / "mcp.json"), "project"),
    ]
    legacy = Path(root) / "state" / "mcp.json"
    if legacy.is_file():
        sources.append(ConfigSource(str(legacy), "target"))
    return sources


def _read_json(path: str) -> dict:
    try:
        raw = Path(path).read_text(encoding="utf-8-sig")
        data = json.loads(raw)
    except OSError as e:
        raise MCPConfigError(f"{path}: 无法读取: {type(e).__name__}: {e}") from e
    except json.JSONDecodeError as e:
        raise MCPConfigError(
            f"{path}: JSON 解析失败(第 {e.lineno} 行第 {e.colno} 列): {e.msg}"
        ) from e
    if not isinstance(data, dict):
        raise MCPConfigError(f"{path}: 顶层必须是 JSON object")
    return data


def _string(value, field: str) -> str:
    if not isinstance(value, str):
        raise ConfigEntryError(f"{field} 必须是字符串")
    return value


def _string_dict(
    value, field: str, env: Mapping[str, str] | None = None
) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigEntryError(f"{field} 必须是 object")
    out: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ConfigEntryError(f"{field} 必须是字符串到字符串的映射")
        out[key] = _expand_value(item, f"{field}.{key}", env)
    return out


def _expand_home(value: str) -> str:
    if value == "~":
        return str(Path.home())
    if value.startswith("~/"):
        return str(Path.home() / value[2:])
    return value


def _expand_value(
    value: str, field: str, env: Mapping[str, str] | None = None
) -> str:
    """Expand ${NAME}; missing variables are a configuration error.

    Pi also accepts a whole-value ``!command``.  fwcore does not execute commands
    while merely reading config (that would be a surprising side effect during
    detection); entries using it are rejected explicitly instead of silently
    passed through with the literal ``!`` value.
    """
    if value.startswith("!"):
        raise ConfigEntryError(f"{field} 不支持 !command 值;请用环境变量")
    missing: list[str] = []
    source = os.environ if env is None else env

    def repl(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in source:
            missing.append(name)
            return ""
        return source[name]

    out = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, value)
    if missing:
        raise ConfigEntryError(
            f"{field} 引用了未设置的环境变量: {', '.join(sorted(set(missing)))}"
        )
    return out


def _validate_exposure(value, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigEntryError(f"{field} 必须是字符串")
    value = MCP_EXPOSURE_ALIASES.get(value, value)
    if value not in MCP_EXPOSURES:
        raise ConfigEntryError(f"{field} 必须是 {', '.join(MCP_EXPOSURES)} 之一")
    return value


def _validate_tool_exposure(value) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigEntryError("toolExposure 必须是 object")
    out: dict[str, str] = {}
    for tool, exposure in value.items():
        if not isinstance(tool, str):
            raise ConfigEntryError("toolExposure 的键必须是字符串")
        resolved = _validate_exposure(exposure, f"toolExposure.{tool}")
        assert resolved is not None
        out[tool] = resolved
    return out


def _parse_bool(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigEntryError(f"{field} 必须是布尔值")
    return value


def _parse_server(
    name: str, raw: dict, *, source: ConfigSource, base_dir: str,
    env: Mapping[str, str] | None = None,
) -> ServerSpec:
    if not SERVER_NAME_RE.fullmatch(name):
        raise ConfigEntryError(f"服务器名 {name!r} 非法(只允许字母、数字、_ 和 -)")
    if not isinstance(raw, dict):
        raise ConfigEntryError(f"服务器 {name!r} 必须是 object")

    value = dict(raw)
    if value.get("exposure") is not None:
        value["exposure"] = _validate_exposure(value["exposure"], f"{name}.exposure")
    if "toolExposure" in value:
        value["toolExposure"] = _validate_tool_exposure(value["toolExposure"])

    if "enabled" in value:
        enabled = _parse_bool(value["enabled"], f"{name}.enabled")
    else:
        enabled = True
    if "description" in value:
        description = _string(value["description"], f"{name}.description")
    else:
        description = ""
    if "timeout" in value:
        timeout = value["timeout"]
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or timeout <= 0
        ):
            raise ConfigEntryError(f"{name}.timeout 必须是正数秒")
        timeout = float(timeout)
    else:
        timeout = None
    external = None
    if "external" in value:
        external = _parse_bool(value["external"], f"{name}.external")

    transport_type = value.get("type")
    if transport_type is not None:
        transport_type = _string(transport_type, f"{name}.type")
        if transport_type == "sse":
            raise ConfigEntryError(
                "legacy SSE transport 不支持;请用 streamable HTTP URL"
            )
        if transport_type not in ("stdio", "http", "streamable-http"):
            raise ConfigEntryError(f"{name}.type 必须是 stdio/http/streamable-http")

    url = value.get("url")
    if url is not None:
        url = _string(url, f"{name}.url")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ConfigEntryError(f"{name}.url 必须是 http(s) URL")
        if transport_type == "stdio":
            raise ConfigEntryError(f"{name}.type=stdio 不能配 url")
    if url is not None:
        headers = _string_dict(value.get("headers"), f"{name}.headers", env)
        for key, header_value in headers.items():
            if (
                "\r" in key
                or "\n" in key
                or "\r" in header_value
                or "\n" in header_value
            ):
                raise ConfigEntryError(f"{name}.headers 含有换行(非法 header)")
        if transport_type == "sse":
            raise ConfigEntryError("legacy SSE transport 不支持")
        return ServerSpec(
            name=name,
            command="",
            url=url,
            headers=headers,
            timeout=timeout,
            enabled=enabled,
            exposure=value.get("exposure"),
            tool_exposure=value.get("toolExposure") or {},
            description=description,
            external=external,
            type=transport_type,
            source=source.path,
            scope=source.scope,
        )

    command = value.get("command")
    if command is None:
        raise ConfigEntryError(f"{name} 需要 command(stdio)或 url(HTTP)")
    command = _string(command, f"{name}.command")
    if not command:
        raise ConfigEntryError(f"{name}.command 不能为空")
    if transport_type in ("http", "streamable-http"):
        raise ConfigEntryError(f"{name}.type={transport_type} 不能配 command")
    args_raw = value.get("args")
    if args_raw is None:
        args: list[str] = []
    elif isinstance(args_raw, list) and all(isinstance(a, str) for a in args_raw):
        args = [_expand_home(a) for a in args_raw]
    else:
        raise ConfigEntryError(f"{name}.args 必须是字符串数组")
    env = _string_dict(value.get("env"), f"{name}.env", env)
    cwd = value.get("cwd")
    if cwd is not None:
        cwd = _expand_home(_string(cwd, f"{name}.cwd"))
        if not os.path.isabs(cwd):
            cwd = os.path.normpath(os.path.join(base_dir, cwd))
    return ServerSpec(
        name=name,
        command=_expand_home(command),
        args=args,
        env=env,
        cwd=cwd,
        timeout=timeout,
        enabled=enabled,
        exposure=value.get("exposure"),
        tool_exposure=value.get("toolExposure") or {},
        description=description,
        external=external,
        type=transport_type,
        source=source.path,
        scope=source.scope,
    )


def _override_server(
    base: ServerSpec, name: str, raw: dict, source: ConfigSource
) -> ServerSpec:
    extra = set(raw) - OVERRIDE_KEYS
    if extra:
        raise ConfigEntryError(
            f"服务器 {name!r} 的 project override 只能设置 "
            f"{', '.join(sorted(OVERRIDE_KEYS))};多出: {', '.join(sorted(extra))}"
        )
    updates: dict = {}
    if "enabled" in raw:
        updates["enabled"] = _parse_bool(raw["enabled"], f"{name}.enabled")
    if "exposure" in raw:
        updates["exposure"] = _validate_exposure(raw["exposure"], f"{name}.exposure")
    if "toolExposure" in raw:
        updates["tool_exposure"] = _validate_tool_exposure(raw["toolExposure"])
    return replace(base, source=source.path, scope=source.scope, **updates)


def load_mcp_config(
    config_paths: Sequence[str | ConfigSource] | None = None,
    *,
    base_dir: str | None = None,
    env: Mapping[str, str] | None = None,
) -> McpConfig:
    """Load and merge Pi-shaped MCP config.

    Invalid files/entries are returned in ``errors`` and skipped.  Valid servers
    still load, matching Pi's diagnostics behavior.
    """
    if config_paths is None:
        sources = discover_mcp_config(base_dir or os.getcwd(), env=env)
    else:
        if isinstance(config_paths, str):
            config_paths = [config_paths]
        sources = _as_sources(config_paths)
    cfg = McpConfig(sources=list(sources))
    base = os.path.abspath(base_dir or os.getcwd())

    for source in sources:
        if not os.path.isfile(source.path):
            continue
        try:
            data = _read_json(source.path)
        except MCPConfigError as e:
            cfg.errors.append(str(e))
            continue
        if "autoEnableCodemode" in data:
            value = data["autoEnableCodemode"]
            if not isinstance(value, bool):
                cfg.errors.append(f"{source.path}: autoEnableCodemode 必须是布尔值")
            else:
                cfg.auto_enable_codemode = value
        servers = data.get("mcpServers", {})
        if servers is None:
            servers = {}
        if not isinstance(servers, dict):
            cfg.errors.append(f"{source.path}: mcpServers 必须是 object")
            continue
        for name, raw in servers.items():
            if not isinstance(name, str) or not isinstance(raw, dict):
                cfg.errors.append(f"{source.path}: mcpServers 的条目必须是 object")
                continue
            try:
                if source.scope in ("project", "target") and not any(
                    key in raw for key in ("command", "url", "type")
                ):
                    base_spec = cfg.servers.get(name)
                    if base_spec is None:
                        raise ConfigEntryError(
                            f"{source.scope} override {name!r} 没有对应的 global 服务器"
                        )
                    cfg.servers[name] = _override_server(base_spec, name, raw, source)
                    continue
                spec = _parse_server(name, raw, source=source, base_dir=base, env=env)
                clash = next(
                    (
                        other
                        for other in cfg.servers
                        if other != name and mcp_namespace(other) == mcp_namespace(name)
                    ),
                    None,
                )
                if clash:
                    raise ConfigEntryError(
                        f"服务器名 {name!r} 与 {clash!r} 规范化后冲突"
                    )
                cfg.servers[name] = spec
            except ConfigEntryError as e:
                cfg.errors.append(f"{source.path}: {name}: {e}")
    return cfg


def load_servers(config_paths: list[str]) -> dict[str, ServerSpec]:
    """Strict compatibility wrapper used by existing callers/tests.

    New code should use :func:`load_mcp_config` so valid servers survive one bad
    entry.  This wrapper preserves the old raise-on-config-error contract.
    """
    cfg = load_mcp_config(list(config_paths))
    if cfg.errors:
        raise MCPConfigError("\n".join(cfg.errors))
    return cfg.servers
