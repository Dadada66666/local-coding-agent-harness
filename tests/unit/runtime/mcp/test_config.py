from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mcp.config import (
    HTTPMCPServerConfig,
    MCPConfigError,
    StdioMCPServerConfig,
    load_mcp_config,
)


def write_config(path: Path, document: object) -> Path:
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_loads_strict_stdio_and_http_config_in_server_order(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "mcp.json",
        {
            "mcpServers": {
                "zeta": {"type": "http", "url": "https://example.test/mcp"},
                "alpha": {"type": "stdio", "command": "server", "args": ["--quiet"]},
            }
        },
    )

    config = load_mcp_config(path)

    assert config.servers == (
        StdioMCPServerConfig(server_id="alpha", command="server", args=("--quiet",)),
        HTTPMCPServerConfig(server_id="zeta", url="https://example.test/mcp"),
    )


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"mcpServers": {}, "extra": True},
        {"mcpServers": {}},
        {"mcpServers": {"bad.name": {"type": "stdio", "command": "server"}}},
        {"mcpServers": {"bad__name": {"type": "stdio", "command": "server"}}},
        {"mcpServers": {"server": {"type": "stdio", "command": ""}}},
        {"mcpServers": {"server": {"type": "stdio", "command": "x", "args": [1]}}},
        {"mcpServers": {"server": {"type": "stdio", "command": "x", "env": {}}}},
        {"mcpServers": {"server": {"type": "http", "url": "file:///tmp/mcp"}}},
        {"mcpServers": {"server": {"type": "http", "url": "https://u:p@host/mcp"}}},
        {"mcpServers": {"server": {"type": "http", "url": "https://host/mcp#x"}}},
        {"mcpServers": {"server": {"type": "sse", "url": "https://host/mcp"}}},
    ],
)
def test_rejects_non_contract_config(tmp_path: Path, document: object) -> None:
    path = write_config(tmp_path / "mcp.json", document)

    with pytest.raises(MCPConfigError):
        load_mcp_config(path)


def test_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(
        '{"mcpServers":{"one":{"type":"stdio","command":"a"},'
        '"one":{"type":"stdio","command":"b"}}}',
        encoding="utf-8",
    )

    with pytest.raises(MCPConfigError, match="duplicate"):
        load_mcp_config(path)


def test_parse_error_does_not_emit_config_content(tmp_path: Path) -> None:
    secret = "private-value-that-must-not-leak"
    path = tmp_path / "mcp.json"
    path.write_text(f'{{"mcpServers": {secret}', encoding="utf-8")

    with pytest.raises(MCPConfigError) as raised:
        load_mcp_config(path)

    assert secret not in str(raised.value)
    assert str(path) not in str(raised.value)
