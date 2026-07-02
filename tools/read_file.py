from __future__ import annotations

from tools.base import BaseTool, ToolResult, ToolValidationError


DEFAULT_LIMIT = 200


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a UTF-8 text file under the repository with line numbers."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to repo root."},
            "offset": {"type": "integer", "description": "Zero-based line offset."},
            "limit": {"type": "integer", "description": "Maximum number of lines to return."},
        },
        "required": ["path"],
    }

    read_only = True
    dangerous = False
    concurrency_safe = True

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

        lines = target.read_text(encoding="utf-8").splitlines()
        selected = lines[offset : offset + limit]
        rendered = [f"{offset + index + 1:>4} | {line}" for index, line in enumerate(selected)]

        remaining = max(len(lines) - (offset + len(selected)), 0)
        if remaining:
            rendered.append(f"... {remaining} remaining lines")

        return ToolResult(
            ok=True,
            content="\n".join(rendered),
            metadata={
                "path": str(target),
                "offset": offset,
                "limit": limit,
                "total_lines": len(lines),
                "remaining_lines": remaining,
            },
        )

