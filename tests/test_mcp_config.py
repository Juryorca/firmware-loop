"""Pi-compatible MCP config discovery/merge; no real server is started."""

from __future__ import annotations

import json

import pytest

from fwcore.mcp_config import (
    ConfigSource,
    MCPConfigError,
    discover_mcp_config,
    load_mcp_config,
    load_servers,
)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_project_override_only_changes_enabled(tmp_path):
    global_cfg = _write(
        tmp_path / "global.json",
        {
            "mcpServers": {
                "chrome": {
                    "command": "npx",
                    "args": ["server"],
                    "enabled": True,
                    "timeout": 12,
                }
            }
        },
    )
    project_cfg = _write(
        tmp_path / ".pi" / "mcp.json", {"mcpServers": {"chrome": {"enabled": False}}}
    )

    cfg = load_mcp_config(
        [
            ConfigSource(global_cfg, "global"),
            ConfigSource(project_cfg, "project"),
        ],
        base_dir=str(tmp_path),
    )

    assert cfg.errors == []
    assert cfg.servers["chrome"].enabled is False
    assert cfg.servers["chrome"].command == "npx"
    assert cfg.servers["chrome"].timeout == 12
    assert cfg.servers["chrome"].scope == "project"


def test_project_full_entry_replaces_global(tmp_path):
    global_cfg = _write(
        tmp_path / "global.json",
        {"mcpServers": {"x": {"command": "old", "args": ["old"]}}},
    )
    project_cfg = _write(
        tmp_path / ".pi" / "mcp.json",
        {"mcpServers": {"x": {"command": "new", "args": ["new"]}}},
    )

    cfg = load_mcp_config(
        [
            ConfigSource(global_cfg, "global"),
            ConfigSource(project_cfg, "project"),
        ],
        base_dir=str(tmp_path),
    )

    assert cfg.errors == []
    assert cfg.servers["x"].command == "new"
    assert cfg.servers["x"].args == ["new"]


def test_invalid_entry_does_not_poison_valid_server(tmp_path):
    cfg_path = _write(
        tmp_path / "mcp.json",
        {
            "mcpServers": {
                "good": {"command": "true"},
                "bad": {"command": 123},
                "sse": {"type": "sse", "url": "http://localhost:1/sse"},
            }
        },
    )

    cfg = load_mcp_config([cfg_path], base_dir=str(tmp_path))

    assert "good" in cfg.servers
    assert sorted(cfg.errors) == [
        f"{cfg_path}: bad: bad.command 必须是字符串",
        f"{cfg_path}: sse: legacy SSE transport 不支持;请用 streamable HTTP URL",
    ]


def test_server_name_and_namespace_collision_are_rejected(tmp_path):
    cfg_path = _write(
        tmp_path / "mcp.json",
        {
            "mcpServers": {
                "a-b": {"command": "true"},
                "a_b": {"command": "true"},
                "bad name": {"command": "true"},
            }
        },
    )

    cfg = load_mcp_config([cfg_path], base_dir=str(tmp_path))

    assert "a-b" in cfg.servers
    assert "a_b" not in cfg.servers
    assert "bad name" not in cfg.servers
    assert any("规范化后冲突" in e for e in cfg.errors)
    assert any("非法" in e for e in cfg.errors)


def test_env_cwd_headers_timeout_and_http_type(tmp_path, monkeypatch):
    fixture_value = "fixture-value"
    monkeypatch.setenv("MCP_TEST_TOKEN", fixture_value)
    cfg_path = _write(
        tmp_path / "mcp.json",
        {
            "mcpServers": {
                "stdio": {
                    "command": "true",
                    "env": {"TOKEN": "${MCP_TEST_TOKEN}"},
                    "cwd": "sub",
                    "timeout": 2.5,
                },
                "http": {
                    "url": "https://example.invalid/mcp",
                    "type": "streamable-http",
                    "headers": {"Authorization": "Bearer ${MCP_TEST_TOKEN}"},
                },
            }
        },
    )

    cfg = load_mcp_config([cfg_path], base_dir=str(tmp_path))

    assert cfg.errors == []
    assert cfg.servers["stdio"].env["TOKEN"] == fixture_value
    assert cfg.servers["stdio"].cwd == str(tmp_path / "sub")
    assert cfg.servers["stdio"].timeout == 2.5
    assert cfg.servers["http"].transport == "http"
    assert cfg.servers["http"].headers["Authorization"] == f"Bearer {fixture_value}"


def test_missing_env_is_reported_not_silently_empty(tmp_path):
    cfg_path = _write(
        tmp_path / "mcp.json",
        {"mcpServers": {"x": {"command": "true", "env": {"X": "${MCP_MISSING_VAR}"}}}},
    )

    cfg = load_mcp_config([cfg_path], base_dir=str(tmp_path))

    assert "x" not in cfg.servers
    assert any("未设置的环境变量" in e for e in cfg.errors)


def test_default_discovery_order_and_target_legacy(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    root = tmp_path / "root"
    _write(agent / "mcp.json", {"mcpServers": {"g": {"command": "g"}}})
    _write(root / ".pi" / "mcp.json", {"mcpServers": {"p": {"command": "p"}}})
    _write(root / "state" / "mcp.json", {"mcpServers": {"t": {"command": "t"}}})
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))

    sources = discover_mcp_config(str(root))
    assert [s.scope for s in sources] == ["global", "project", "target"]

    cfg = load_mcp_config(sources, base_dir=str(root))
    assert set(cfg.servers) == {"g", "p", "t"}


def test_load_servers_strict_wrapper_still_rejects_invalid(tmp_path):
    cfg_path = _write(tmp_path / "mcp.json", {"mcpServers": {"bad": {"command": 1}}})
    with pytest.raises(MCPConfigError, match="bad.command") as exc:
        load_servers([cfg_path])
    assert "bad.command" in str(exc.value)


def test_target_override_can_disable_global_server(tmp_path):
    global_cfg = _write(
        tmp_path / "global.json",
        {"mcpServers": {"chrome": {"command": "npx", "args": ["server"]}}},
    )
    target_cfg = _write(
        tmp_path / "state" / "mcp.json", {"mcpServers": {"chrome": {"enabled": False}}}
    )

    cfg = load_mcp_config(
        [
            ConfigSource(global_cfg, "global"),
            ConfigSource(target_cfg, "target"),
        ],
        base_dir=str(tmp_path),
    )

    assert cfg.errors == []
    assert cfg.servers["chrome"].enabled is False
    assert cfg.servers["chrome"].command == "npx"
    assert cfg.servers["chrome"].scope == "target"


def test_env_resolution_uses_explicit_mapping(tmp_path, monkeypatch):
    monkeypatch.delenv("MCP_TEST_MAPPING", raising=False)
    cfg_path = _write(
        tmp_path / "mcp.json",
        {"mcpServers": {"x": {"command": "true", "env": {"X": "${MCP_TEST_MAPPING}"}}}},
    )

    cfg = load_mcp_config([cfg_path], env={"MCP_TEST_MAPPING": "mapped"})

    assert cfg.errors == []
    assert cfg.servers["x"].env["X"] == "mapped"


def test_explicit_empty_paths_do_not_fall_back_to_discovery(tmp_path, monkeypatch):
    agent = tmp_path / "agent"
    _write(agent / "mcp.json", {"mcpServers": {"global": {"command": "true"}}})
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(agent))

    cfg = load_mcp_config([])

    assert cfg.servers == {}
    assert load_servers([]) == {}
