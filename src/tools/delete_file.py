from __future__ import annotations

import hashlib

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


class DeleteFileTool(BaseTool):
    name = "delete_file"
    description = (
        "Delete one known file previously read with read_file or changed by a file tool. "
        "Shell reads do not create the required snapshot; directories and symlinks are unsupported."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
        },
        "required": ["path"],
    }

    read_only = False
    dangerous = True
    concurrency_safe = False

    def classify_operation(self, args: dict, context) -> Operation:
        requested_path = args.get("path", "")
        return Operation(
            kind="fs.delete",
            action="delete",
            subject=str(requested_path),
            paths=[str(requested_path)] if requested_path else [],
            scope_key=f"delete:file:{requested_path}",
            terminal_on_deny=False,
            is_sensitive=True,
        )

    def validate(self, args: dict, context) -> None:
        path = args.get("path")
        if not isinstance(path, str) or not path:
            raise ToolValidationError("delete_file requires a non-empty string path")
        if context is None:
            return

        try:
            target = context.safe_path(path)
        except (OSError, ValueError):
            return
        if context.access_policy.is_protected_resolved_write(context.repo_path, target):
            return
        if str(target) not in context.read_file_state:
            raise ToolValidationError(
                "delete_file requires a current structured snapshot; use read_file first. "
                "A Bash read does not count."
            )

    def call(self, args: dict, context) -> ToolResult:
        requested_path = args["path"]
        unresolved = context.repo_path / requested_path
        if unresolved.is_symlink():
            return ToolResult(
                ok=False,
                content=f"Refusing to delete a symlink: {requested_path}",
                error="symlink delete unsupported",
            )

        target = context.safe_path(requested_path)
        if context.access_policy.is_protected_resolved_write(context.repo_path, target):
            return ToolResult(
                ok=False,
                content="Permission denied: protected delete path.",
                error="protected delete",
                metadata={"protected_delete": True},
            )
        if not target.exists():
            return ToolResult(
                ok=False,
                content=f"File not found: {requested_path}",
                error="file not found",
            )
        if not target.is_file():
            return ToolResult(
                ok=False,
                content=f"Not a file: {requested_path}",
                error="not a file",
            )

        snapshot = context.read_file_state.get(str(target))
        if snapshot is None:
            return ToolResult(
                ok=False,
                content=f"File has not been read yet: {requested_path}. Use read_file first.",
                error="file not read",
            )

        stat = target.stat()
        raw = target.read_bytes()
        if (
            stat.st_mtime_ns != snapshot.mtime_ns
            or stat.st_size != snapshot.size
            or hashlib.sha256(raw).hexdigest() != snapshot.sha256
        ):
            return ToolResult(
                ok=False,
                content=f"File changed since last read: {requested_path}. Read it again before deleting.",
                error="stale file",
            )

        target.unlink()
        relative_path = str(target.relative_to(context.repo_path))
        context.record_deleted_file(relative_path)

        return ToolResult(
            ok=True,
            content=f"Deleted file: {requested_path}",
            metadata={
                "path": str(target),
                "changed_file": requested_path,
                "operation": "delete_file",
                "changed": True,
            },
        )
