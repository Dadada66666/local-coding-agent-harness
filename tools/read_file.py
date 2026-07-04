from __future__ import annotations

import hashlib

from agent.context import ReadFileSnapshot
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
        lines = raw.decode("utf-8").splitlines()
        selected = lines[offset : offset + limit]
        rendered = [f"{offset + index + 1:>4} | {line}" for index, line in enumerate(selected)]

        remaining = max(len(lines) - (offset + len(selected)), 0)
        if remaining:
            rendered.append(f"... {remaining} remaining lines")

        stat = target.stat()
        partial = offset != 0 or remaining > 0
        context.read_file_state[str(target)] = ReadFileSnapshot(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            sha256=hashlib.sha256(raw).hexdigest(),
            partial=partial,
        )

        return ToolResult(
            ok=True,
            content="\n".join(rendered),
            metadata={
                "path": str(target),
                "offset": offset,
                "limit": limit,
                "total_lines": len(lines),
                "remaining_lines": remaining,
                "partial": partial,
            },
        )
