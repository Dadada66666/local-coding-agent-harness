from __future__ import annotations

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


DEFAULT_LIMIT = 4000


class ReadArtifactTool(BaseTool):
    name = "read_artifact"
    description = "Read a bounded slice of a tool output by artifact_id."
    input_schema = {
        "type": "object",
        "properties": {
            "artifact_id": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["artifact_id"],
    }

    read_only = True
    dangerous = False
    concurrency_safe = True

    def classify_operation(self, args: dict, context) -> Operation:
        artifact_id = str(args.get("artifact_id", ""))
        return Operation(
            kind="artifact.read",
            action="read",
            subject=artifact_id,
            scope_key=f"read:artifact:{artifact_id}",
            is_read_only=True,
        )

    def validate(self, args: dict, context) -> None:
        artifact_id = args.get("artifact_id")
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            raise ToolValidationError("read_artifact requires artifact_id")
        offset = self._integer_arg(args, "offset", 0)
        if offset < 0:
            raise ToolValidationError("offset must be >= 0")

        max_limit = self._max_limit(context)
        limit = self._integer_arg(args, "limit", min(DEFAULT_LIMIT, max_limit))
        if limit <= 0 or limit > max_limit:
            raise ToolValidationError(f"limit must be between 1 and {max_limit}")

    def call(self, args: dict, context) -> ToolResult:
        artifact_id = str(args["artifact_id"])
        offset = self._integer_arg(args, "offset", 0)
        limit = self._integer_arg(
            args,
            "limit",
            min(DEFAULT_LIMIT, self._max_limit(context)),
        )
        try:
            result = context.artifacts.read(artifact_id, offset=offset, limit=limit)
        except KeyError:
            return ToolResult(
                ok=False,
                content=f"Unknown artifact_id: {artifact_id}",
                error="unknown artifact",
                metadata={"artifact_id": artifact_id},
            )

        footer = (
            f"\n[artifact {artifact_id}: chars {result.offset}-{result.next_offset} "
            f"of {result.total_chars}]"
        )
        if result.has_more:
            footer += f"\nMore available; use offset={result.next_offset}."
        return ToolResult(
            ok=True,
            content=f"{result.content}{footer}",
            artifact_id=artifact_id,
            artifact_path=str(context.artifacts.get(artifact_id).path),
            metadata={
                "artifact_id": artifact_id,
                "offset": result.offset,
                "next_offset": result.next_offset,
                "total_chars": result.total_chars,
                "has_more": result.has_more,
            },
        )

    def _max_limit(self, context) -> int:
        configured = int(context.config.artifact_read_max_chars)
        result_budget = max(int(context.config.max_tool_result_chars) - 256, 1)
        return min(configured, result_budget)

    def _integer_arg(self, args: dict, name: str, default: int) -> int:
        value = args.get(name, default)
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ToolValidationError(f"{name} must be an integer") from exc
