from __future__ import annotations

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


class WriteFileTool(BaseTool):
    name = "write_file"
    description = (
        "Write a new UTF-8 text file. Fails if it already exists; "
        "use edit_file for existing files."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["path", "content"],
    }

    read_only = False
    dangerous = True
    concurrency_safe = False

    def classify_operation(self, args: dict, context) -> Operation:
        requested_path = args.get("path", "")
        return Operation(
            kind="fs.write",
            action="create",
            subject=str(requested_path),
            paths=[str(requested_path)] if requested_path else [],
            scope_key=f"write:create:{requested_path}",
            terminal_on_deny=True,
        )

    def validate(self, args: dict, context) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            raise ToolValidationError("write_file requires a non-empty string path")
        if "content" not in args:
            raise ToolValidationError("write_file requires content")
        if not isinstance(args["content"], str):
            raise ToolValidationError("write_file content must be a string")
        try:
            args["content"].encode("utf-8")
        except UnicodeEncodeError as exc:
            raise ToolValidationError("write_file content must be valid UTF-8") from exc

    def call(self, args: dict, context) -> ToolResult:
        requested_path = args["path"]
        target = context.safe_path(requested_path)

        if target.exists():
            if not target.is_file():
                return ToolResult(
                    ok=False,
                    content=f"Path already exists and is not a file: {requested_path}",
                    error="not a file",
                )
            return self._file_exists_result(requested_path)

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except FileExistsError:
            return ToolResult(
                ok=False,
                content=f"Parent path is not a directory: {requested_path}",
                error="parent not a directory",
            )

        try:
            with target.open("x", encoding="utf-8") as handle:
                handle.write(args["content"])
        except FileExistsError:
            return self._file_exists_result(requested_path)
        except IsADirectoryError:
            return ToolResult(
                ok=False,
                content=f"Path is not a writable file: {requested_path}",
                error="not a file",
            )

        context.record_file_snapshot(target, target.read_bytes(), partial=False)
        context.record_created_file(str(target.relative_to(context.repo_path)))

        return ToolResult(
            ok=True,
            content=f"Wrote new file: {requested_path}",
            metadata={
                "changed_file": requested_path,
                "operation": "write_file",
                "write_mode": "create",
                "changed": True,
                "snapshot_updated": True,
            },
        )

    def _file_exists_result(self, requested_path: str) -> ToolResult:
        return ToolResult(
            ok=False,
            content=f"File already exists: {requested_path}. Use edit_file for precise edits.",
            error="file exists",
        )
