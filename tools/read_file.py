from __future__ import annotations

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


DEFAULT_LIMIT = 200


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a UTF-8 text file under the repository with line numbers and optional offset/limit."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to WORKDIR."},
            "offset": {"type": "integer", "description": "Zero-based line offset."},
            "limit": {"type": "integer", "description": "Maximum number of lines to return."},
        },
        "required": ["path"],
    }

    read_only = True
    dangerous = False
    concurrency_safe = True

    def classify_operation(self, args: dict, context) -> Operation:
        requested_path = args.get("path", "")
        return Operation(
            kind="fs.read",
            action="read",
            subject=str(requested_path),
            paths=[str(requested_path)] if requested_path else [],
            scope_key=f"read:file:{requested_path}",
            is_read_only=True,
        )

    def validate(self, args: dict, context) -> None:
        if not args.get("path"):
            raise ToolValidationError("read_file requires path")
        if int(args.get("offset", 0)) < 0:
            raise ToolValidationError("offset must be >= 0")
        if int(args.get("limit", DEFAULT_LIMIT)) <= 0:
            raise ToolValidationError("limit must be > 0")

    def call(self, args: dict, context) -> ToolResult:
        requested_path = args["path"]
        target = context.safe_path(requested_path)
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", DEFAULT_LIMIT))

        if not target.exists():
            return ToolResult(ok=False, content=f"File not found: {requested_path}", error="file not found")
        if not target.is_file():
            return ToolResult(ok=False, content=f"Not a file: {requested_path}", error="not a file")

        raw = target.read_bytes()
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            return ToolResult(
                ok=False,
                content=f"File is not valid UTF-8: {requested_path}",
                error="decode error",
                metadata={"encoding": "utf-8", "reason": str(exc)},
            )
        selected = lines[offset : offset + limit]
        rendered = [f"{offset + index + 1:>4} | {line}" for index, line in enumerate(selected)]

        remaining = max(len(lines) - (offset + len(selected)), 0)
        if remaining:
            rendered.append(f"... {remaining} remaining lines")

        partial = offset != 0 or remaining > 0
        context.record_file_snapshot(target, raw, partial=partial)

        return ToolResult(
            ok=True,
            content="\n".join(rendered),
            metadata={
                "path": str(target),
                "requested_path": requested_path,
                "resolved_path": str(target),
                "offset": offset,
                "limit": limit,
                "total_lines": len(lines),
                "remaining_lines": remaining,
                "partial": partial,
            },
        )
