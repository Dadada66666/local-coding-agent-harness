from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse


_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class MCPConfigError(ValueError):
    """Raised when the explicit host MCP configuration is invalid."""


@dataclass(frozen=True, slots=True)
class StdioMCPServerConfig:
    server_id: str
    command: str
    args: tuple[str, ...] = ()
    transport: str = "stdio"


@dataclass(frozen=True, slots=True)
class HTTPMCPServerConfig:
    server_id: str
    url: str
    transport: str = "http"


MCPServerConfig = StdioMCPServerConfig | HTTPMCPServerConfig


@dataclass(frozen=True, slots=True)
class MCPConfig:
    servers: tuple[MCPServerConfig, ...]


def load_mcp_config(path: str | Path) -> MCPConfig:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        document = json.loads(raw, object_pairs_hook=_object_without_duplicates)
    except MCPConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise MCPConfigError("MCP configuration could not be read or parsed.") from None

    root = _require_object(document, "MCP configuration root")
    _require_exact_fields(root, {"mcpServers"}, "MCP configuration root")
    servers_raw = _require_object(root["mcpServers"], "mcpServers")
    if not servers_raw:
        raise MCPConfigError("mcpServers must contain at least one server.")

    servers = tuple(
        _parse_server(server_id, servers_raw[server_id]) for server_id in sorted(servers_raw)
    )
    return MCPConfig(servers=servers)


def _parse_server(server_id: Any, value: Any) -> MCPServerConfig:
    if not isinstance(server_id, str) or not _valid_component(server_id):
        raise MCPConfigError("MCP server ids must use letters, digits, '_' or '-'.")
    config = _require_object(value, "MCP server entry")
    transport = config.get("type")
    if transport == "stdio":
        _require_exact_fields(
            config, {"type", "command", "args"}, "stdio server", optional={"args"}
        )
        command = config.get("command")
        if not isinstance(command, str) or not command.strip():
            raise MCPConfigError("stdio command must be a non-empty string.")
        args = config.get("args", [])
        if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
            raise MCPConfigError("stdio args must be an array of strings.")
        return StdioMCPServerConfig(server_id=server_id, command=command, args=tuple(args))

    if transport == "http":
        _require_exact_fields(config, {"type", "url"}, "HTTP server")
        url = config.get("url")
        if not isinstance(url, str) or not _valid_http_url(url):
            raise MCPConfigError(
                "HTTP URL must be an absolute http(s) URL without userinfo or fragment."
            )
        return HTTPMCPServerConfig(server_id=server_id, url=url)

    raise MCPConfigError("MCP server type must be 'stdio' or 'http'.")


def _object_without_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MCPConfigError("MCP configuration contains a duplicate key.")
        result[key] = value
    return result


def _require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MCPConfigError(f"{label} must be an object.")
    return value


def _require_exact_fields(
    value: dict[str, Any],
    allowed: set[str],
    label: str,
    *,
    optional: set[str] | None = None,
) -> None:
    unknown = set(value) - allowed
    required = allowed - (optional or set())
    missing = required - set(value)
    if unknown:
        raise MCPConfigError(f"{label} contains unknown fields.")
    if missing:
        raise MCPConfigError(f"{label} is missing required fields.")


def _valid_component(value: str) -> bool:
    return bool(value) and "__" not in value and _NAME_PATTERN.fullmatch(value) is not None


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.fragment
    )
