from __future__ import annotations

import json
import time
from typing import Any
import unicodedata

from mcp import InputRequiredRoundsExceededError, MCPError, types

from runtime.mcp.runtime import MCPRuntime, MCPRuntimeClosedError
from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


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


class MCPToolSearch(BaseTool):
    name = "mcp_tool_search"
    description = "Search the configured MCP tool catalog without calling a remote server."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 256},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    read_only = True
    dangerous = False
    concurrency_safe = False

    def __init__(self, runtime: MCPRuntime) -> None:
        self.runtime = runtime

    def validate(self, args: dict, context) -> None:
        unknown = set(args) - {"query", "limit"}
        if unknown or "query" not in args:
            raise ToolValidationError("Expected only query and optional limit.")
        query = args["query"]
        if not isinstance(query, str) or not query or len(query) > 256:
            raise ToolValidationError("query must be a non-empty string of at most 256 characters.")
        if not _query_terms(query):
            raise ToolValidationError("query must contain at least one alphanumeric term.")
        limit = args.get("limit", 5)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10:
            raise ToolValidationError("limit must be an integer from 1 through 10.")

    def classify_operation(self, args: dict, context) -> Operation:
        return Operation(
            kind="mcp.search",
            action="search",
            subject="mcp_catalog",
            scope_key="mcp:catalog:search",
            is_read_only=True,
            is_destructive=False,
            is_sensitive=False,
        )

    def call(self, args: dict, context) -> ToolResult:
        query = args["query"]
        limit = args.get("limit", 5)
        query_terms = _query_terms(query)
        normalized_query = _normalize_text(query)
        ranked = []
        for entry in self.runtime.catalog:
            score = sum(
                16 * (term in entry.name_terms)
                + 8 * (term in entry.server_terms)
                + 4 * (term in entry.property_terms)
                + (term in entry.description_terms)
                for term in query_terms
            )
            if query.strip().casefold() == entry.canonical_tool_id.casefold():
                score += 128
            if normalized_query == _normalize_text(entry.remote_name):
                score += 64
            if score > 0:
                ranked.append((score, entry.canonical_tool_id, entry))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        matches = [entry for _, _, entry in ranked[:limit]]
        payload = {
            "result_count": len(matches),
            "tools": [
                {
                    "canonical_tool_id": entry.canonical_tool_id,
                    "server_id": entry.server_id,
                    "remote_tool_name": entry.remote_name,
                    "description": entry.description,
                    "input_schema": entry.input_schema,
                }
                for entry in matches
            ],
        }
        return ToolResult(
            ok=True,
            content=json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            metadata={
                "mcp_search_query": query,
                "mcp_search_result_count": len(matches),
            },
        )


class MCPToolCall(BaseTool):
    name = "mcp_tool_call"
    description = "Call one configured MCP tool by its exact canonical id."
    input_schema = {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "minLength": 1},
            "arguments": {"type": "object"},
        },
        "required": ["tool", "arguments"],
        "additionalProperties": False,
    }
    read_only = False
    dangerous = False
    concurrency_safe = False

    def __init__(self, runtime: MCPRuntime) -> None:
        self.runtime = runtime

    def validate(self, args: dict, context) -> None:
        if set(args) != {"tool", "arguments"}:
            raise ToolValidationError("Expected exactly tool and arguments.")
        if not isinstance(args["tool"], str) or not args["tool"]:
            raise ToolValidationError("tool must be a non-empty canonical MCP tool id.")
        if not isinstance(args["arguments"], dict):
            raise ToolValidationError("arguments must be an object.")
        self._entry(args["tool"])

    def classify_operation(self, args: dict, context) -> Operation:
        entry = self._entry(args["tool"])
        return entry.binding.classify_operation(args["arguments"], context)

    def call(self, args: dict, context) -> ToolResult:
        entry = self._entry(args["tool"])
        return entry.binding.call(args["arguments"], context)

    def _entry(self, canonical_tool_id: str):
        entry = self.runtime.resolve_catalog_tool(canonical_tool_id)
        if entry is None:
            raise ToolValidationError(
                f"Unknown MCP tool id: {canonical_tool_id}. Use mcp_tool_search to find it."
            )
        return entry


def search_terms(value: str) -> frozenset[str]:
    return frozenset(_normalize_text(value).split())


def _query_terms(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_normalize_text(value).split()))


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(
        "".join(character if character.isalnum() else " " for character in normalized).split()
    )


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
