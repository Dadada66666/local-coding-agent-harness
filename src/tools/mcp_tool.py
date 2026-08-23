from __future__ import annotations

import json
import time
from typing import Any

from mcp import InputRequiredRoundsExceededError, MCPError, types

from runtime.mcp.runtime import MCPRuntime, MCPRuntimeClosedError
from runtime.operation import Operation
from tools.base import BaseTool, ToolResult


class MCPTool(BaseTool):
    read_only = False
    dangerous = False
    concurrency_safe = False

    def __init__(
        self,
        *,
        runtime: MCPRuntime,
        server_id: str,
        remote_name: str,
        name: str,
        description: str,
        input_schema: dict[str, Any],
    ) -> None:
        self.runtime = runtime
        self.server_id = server_id
        self.remote_name = remote_name
        self.name = name
        self.description = description
        self.input_schema = input_schema

    def classify_operation(self, args: dict, context) -> Operation:
        return Operation(
            kind="mcp.call",
            action=self.remote_name,
            subject=f"{self.server_id}/{self.remote_name}",
            scope_key=f"mcp:{self.server_id}:{self.remote_name}",
            paths=[],
            command=None,
            terminal_on_deny=False,
            is_read_only=False,
            is_destructive=False,
            is_sensitive=True,
            metadata={
                "mcp_server_id": self.server_id,
                "mcp_remote_tool": self.remote_name,
            },
        )

    def call(self, args: dict, context) -> ToolResult:
        started = time.monotonic()
        try:
            result = self.runtime.call_tool(self.server_id, self.remote_name, args)
            converted = self._convert_result(result)
        except Exception as exc:
            converted = self._failed_result(_error_kind(exc), _safe_error_message(exc))
        converted.metadata.setdefault(
            "mcp_duration_ms",
            round((time.monotonic() - started) * 1000, 3),
        )
        return converted

    def _convert_result(self, result: types.CallToolResult) -> ToolResult:
        unsupported = sorted(
            {
                getattr(block, "type", block.__class__.__name__)
                for block in result.content
                if not isinstance(block, types.TextContent)
            }
        )
        if unsupported:
            return self._failed_result(
                "unsupported_content",
                "MCP result contains unsupported content types: " + ", ".join(unsupported),
            )

        parts = [block.text for block in result.content if isinstance(block, types.TextContent)]
        if result.structured_content is not None:
            try:
                serialized = json.dumps(
                    result.structured_content,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            except (TypeError, ValueError):
                return self._failed_result(
                    "serialization_error",
                    "MCP result could not be serialized.",
                )
            if parts:
                parts.append("[structured_content]\n" + serialized)
            else:
                parts.append(serialized)

        content = "\n".join(parts)
        if not content:
            return self._failed_result("tool_error", "MCP tool returned no supported content.")
        if result.is_error:
            return self._failed_result("tool_error", content)
        return ToolResult(ok=True, content=content, metadata=self._base_metadata())

    def _failed_result(self, error_kind: str, message: str) -> ToolResult:
        return ToolResult(
            ok=False,
            content=message,
            error=message,
            metadata={**self._base_metadata(), "mcp_error_kind": error_kind},
        )

    def _base_metadata(self) -> dict[str, Any]:
        return {
            "mcp_server_id": self.server_id,
            "mcp_remote_tool": self.remote_name,
        }


def _error_kind(exc: Exception) -> str:
    if isinstance(exc, MCPRuntimeClosedError):
        return "runtime_closed"
    if isinstance(exc, InputRequiredRoundsExceededError):
        return "input_required"
    if isinstance(exc, MCPError) and exc.code == types.REQUEST_TIMEOUT:
        return "timeout_error"
    if isinstance(exc, TimeoutError):
        return "timeout_error"
    if isinstance(exc, MCPError):
        return "protocol_error"
    if exc.__class__.__module__.startswith("pydantic"):
        return "protocol_error"
    return "transport_error"


def _safe_error_message(exc: Exception) -> str:
    kind = _error_kind(exc)
    messages = {
        "runtime_closed": "MCP runtime is closed.",
        "input_required": "MCP tool requires unsupported additional input.",
        "timeout_error": "MCP tool call timed out.",
        "protocol_error": "MCP protocol error.",
        "transport_error": "MCP transport error.",
        "serialization_error": "MCP result could not be serialized.",
    }
    return messages.get(kind, "MCP tool call failed.")
