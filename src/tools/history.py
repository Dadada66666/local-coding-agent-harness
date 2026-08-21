from __future__ import annotations

import json
from typing import Any

from runtime.context.budget import estimate_text_tokens
from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


MAX_HISTORY_RESULT_TOKENS = 10_000


class _HistoryTool(BaseTool):
    read_only = True
    dangerous = False
    concurrency_safe = True

    def classify_operation(self, args: dict, context) -> Operation:
        return Operation(
            kind="history.read",
            action=self.name,
            subject=self.name,
            scope_key=f"read:history:{self.name}",
            is_read_only=True,
        )

    def _canonical_item(self, context, ordinal: int) -> str:
        value: Any = context.conversation_messages[ordinal]
        redactor = getattr(context, "redactor", None)
        if redactor is not None:
            value = redactor.redact_value(value)
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )

    def _window(self, context, window_id: str) -> dict[str, Any] | None:
        return next(
            (window for window in context.history_windows() if window["window_id"] == window_id),
            None,
        )

    def _ordinal(self, context, item_id: str) -> int | None:
        prefix = f"{context.run_id}:item:"
        if not item_id.startswith(prefix):
            return None
        try:
            ordinal = int(item_id[len(prefix) :])
        except ValueError:
            return None
        if ordinal < 0 or ordinal >= len(context.conversation_messages):
            return None
        return ordinal

    def _result(self, payload: dict[str, Any], **metadata: Any) -> ToolResult:
        content = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        estimated = estimate_text_tokens(content)
        if estimated > MAX_HISTORY_RESULT_TOKENS:
            raise ValueError("history result exceeds the 10000-token bound")
        return ToolResult(
            ok=True,
            content=content,
            metadata={
                "history_recovery": True,
                "estimated_tokens": estimated,
                **metadata,
            },
        )

    def _error(self, code: str, detail: str) -> ToolResult:
        return ToolResult(
            ok=False,
            content=f"History error ({code}): {detail}",
            error=detail,
            metadata={
                "history_recovery": True,
                "history_error": code,
                "track_mutation_failure": False,
            },
        )

    def _integer(self, args: dict, name: str, default: int) -> int:
        value = args.get(name, default)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ToolValidationError(f"{name} must be an integer") from exc


class HistoryListWindowsTool(_HistoryTool):
    name = "history_list_windows"
    description = "List bounded audit-history windows for this run, newest first."
    input_schema = {
        "type": "object",
        "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100}},
    }

    def validate(self, args: dict, context) -> None:
        limit = self._integer(args, "limit", 20)
        if limit < 1 or limit > 100:
            raise ToolValidationError("limit must be between 1 and 100")

    def call(self, args: dict, context) -> ToolResult:
        limit = self._integer(args, "limit", 20)
        windows = list(reversed(context.history_windows()))[:limit]
        return self._result({"windows": windows})


class HistoryListItemsTool(_HistoryTool):
    name = "history_list_items"
    description = "List bounded canonical audit items from one history window."
    input_schema = {
        "type": "object",
        "properties": {
            "window_id": {"type": "string"},
            "after_item_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            "max_chars_per_item": {
                "type": "integer",
                "minimum": 1,
                "maximum": 2000,
            },
        },
        "required": ["window_id"],
    }

    def validate(self, args: dict, context) -> None:
        if not isinstance(args.get("window_id"), str) or not args["window_id"]:
            raise ToolValidationError("window_id is required")
        limit = self._integer(args, "limit", 20)
        chars = self._integer(args, "max_chars_per_item", 1000)
        if limit < 1 or limit > 20:
            raise ToolValidationError("limit must be between 1 and 20")
        if chars < 1 or chars > 2000:
            raise ToolValidationError("max_chars_per_item must be between 1 and 2000")

    def call(self, args: dict, context) -> ToolResult:
        window_id = str(args.get("window_id", ""))
        window = self._window(context, window_id)
        if window is None:
            return self._error("unknown_window_id", f"Unknown window_id: {window_id}")
        start = int(window["start_ordinal"])
        after = args.get("after_item_id")
        if after is not None:
            ordinal = self._ordinal(context, str(after))
            if ordinal is None or not start <= ordinal < int(window["end_ordinal"]):
                return self._error(
                    "invalid_after_item_id",
                    "after_item_id is not in the requested window",
                )
            start = ordinal + 1
        limit = self._integer(args, "limit", 20)
        chars = self._integer(args, "max_chars_per_item", 1000)
        items: list[dict[str, Any]] = []
        for ordinal in range(start, int(window["end_ordinal"])):
            if len(items) >= limit:
                break
            message = context.conversation_messages[ordinal]
            canonical = self._canonical_item(context, ordinal)
            kinds = self._content_kinds(message)
            item = {
                "item_id": context.audit_item_id(ordinal),
                "role": message.get("role"),
                "types": kinds,
                "preview": canonical[:chars],
            }
            candidate = self._render_items(items + [item], window_id)
            if estimate_text_tokens(candidate) > MAX_HISTORY_RESULT_TOKENS:
                break
            items.append(item)
        return self._result({"window_id": window_id, "items": items})

    def _render_items(self, items: list[dict[str, Any]], window_id: str) -> str:
        return json.dumps(
            {"window_id": window_id, "items": items},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    def _content_kinds(self, message: dict) -> list[str]:
        content = message.get("content")
        if isinstance(content, str):
            return ["text"]
        if not isinstance(content, list):
            return [type(content).__name__]
        return sorted(
            {
                str(block.get("type", "unknown"))
                if isinstance(block, dict)
                else type(block).__name__
                for block in content
            }
        )


class HistorySearchContentsTool(_HistoryTool):
    name = "history_search_contents"
    description = "Search canonical audit content with a case-sensitive literal query."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "maxLength": 1000},
            "window_id": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    }

    def validate(self, args: dict, context) -> None:
        query = args.get("query")
        if not isinstance(query, str) or not query or len(query) > 1000:
            raise ToolValidationError("query must contain 1 to 1000 characters")
        limit = self._integer(args, "limit", 20)
        if limit < 1 or limit > 20:
            raise ToolValidationError("limit must be between 1 and 20")

    def call(self, args: dict, context) -> ToolResult:
        query = str(args.get("query", ""))
        window_id = args.get("window_id")
        if window_id is not None and self._window(context, str(window_id)) is None:
            return self._error("unknown_window_id", f"Unknown window_id: {window_id}")
        limit = self._integer(args, "limit", 20)
        matches: list[dict[str, Any]] = []
        for ordinal, _message in enumerate(context.conversation_messages):
            window = context.history_window_for_ordinal(ordinal)
            if window is None or (window_id is not None and window["window_id"] != window_id):
                continue
            canonical = self._canonical_item(context, ordinal)
            position = canonical.find(query)
            if position < 0:
                continue
            snippet_start = max(position - 160, 0)
            snippet_end = min(position + len(query) + 160, len(canonical))
            matches.append(
                {
                    "item_id": context.audit_item_id(ordinal),
                    "window_id": window["window_id"],
                    "snippet": canonical[snippet_start:snippet_end],
                }
            )
            if len(matches) >= limit:
                break
        return self._result({"query": query, "matches": matches})


class HistoryReadItemTool(_HistoryTool):
    name = "history_read_item"
    description = "Read a bounded character slice from one canonical audit item."
    input_schema = {
        "type": "object",
        "properties": {
            "item_id": {"type": "string"},
            "offset_chars": {"type": "integer", "minimum": 0},
            "limit_chars": {"type": "integer", "minimum": 1, "maximum": 20000},
        },
        "required": ["item_id"],
    }

    def validate(self, args: dict, context) -> None:
        if not isinstance(args.get("item_id"), str) or not args["item_id"]:
            raise ToolValidationError("item_id is required")
        offset = self._integer(args, "offset_chars", 0)
        limit = self._integer(args, "limit_chars", 4000)
        if offset < 0:
            raise ToolValidationError("offset_chars must be >= 0")
        if limit < 1 or limit > 20_000:
            raise ToolValidationError("limit_chars must be between 1 and 20000")

    def call(self, args: dict, context) -> ToolResult:
        item_id = str(args.get("item_id", ""))
        ordinal = self._ordinal(context, item_id)
        if ordinal is None:
            return self._error("unknown_item_id", f"Unknown item_id: {item_id}")
        canonical = self._canonical_item(context, ordinal)
        offset = min(self._integer(args, "offset_chars", 0), len(canonical))
        requested = min(self._integer(args, "limit_chars", 4000), len(canonical) - offset)
        low, high = 0, requested
        best_content = ""
        best_payload: dict[str, Any] | None = None
        while low <= high:
            length = (low + high) // 2
            content = canonical[offset : offset + length]
            next_offset = offset + len(content)
            payload = {
                "item_id": item_id,
                "content": content,
                "offset_chars": offset,
                "next_offset": next_offset,
                "total_chars": len(canonical),
                "complete": next_offset >= len(canonical),
            }
            rendered = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if estimate_text_tokens(rendered) <= MAX_HISTORY_RESULT_TOKENS:
                best_content = content
                best_payload = payload
                low = length + 1
            else:
                high = length - 1
        if best_payload is None:
            return self._error("result_budget", "History item metadata exceeds result budget")
        best_payload["content"] = best_content
        return self._result(best_payload, complete=best_payload["complete"])
