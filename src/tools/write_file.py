from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


class WriteFileTool(BaseTool):
    name = "write_file"
    description = (
        "Write a complete UTF-8 file. Creates new files; replaces existing files only with a "
        "complete current read_file snapshot."
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
        action = "create"
        try:
            if requested_path and context.safe_path(str(requested_path)).exists():
                action = "replace"
        except (OSError, ValueError):
            pass
        return Operation(
            kind="fs.write",
            action=action,
            subject=str(requested_path),
            paths=[str(requested_path)] if requested_path else [],
            scope_key=f"write:{action}:{requested_path}",
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
            return self._replace_existing(requested_path, target, args["content"], context)

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

    def _replace_existing(
        self,
        requested_path: str,
        target: Path,
        content: str,
        context,
    ) -> ToolResult:
        snapshot = context.read_file_state.get(str(target))
        if snapshot is None:
            return ToolResult(
                ok=False,
                content=(
                    f"File has not been read yet: {requested_path}. "
                    "Use read_file before replacing it; do not delete and recreate it."
                ),
                error="file not read",
                metadata={
                    "error_code": "file_not_read",
                    "path": requested_path,
                    "recoverable": True,
                    "recovery_tool": "read_file",
                    "delete_not_required": True,
                },
            )
        if snapshot.partial:
            return ToolResult(
                ok=False,
                content=(
                    f"File was only partially read: {requested_path}. "
                    "Read the complete file before replacing it."
                ),
                error="partial file snapshot",
                metadata={
                    "error_code": "partial_snapshot",
                    "path": requested_path,
                    "recoverable": True,
                    "recovery_tool": "read_file",
                    "delete_not_required": True,
                },
            )

        stat = target.stat()
        original = target.read_bytes()
        if (
            stat.st_mtime_ns != snapshot.mtime_ns
            or stat.st_size != snapshot.size
            or hashlib.sha256(original).hexdigest() != snapshot.sha256
        ):
            return ToolResult(
                ok=False,
                content=f"File changed since last read: {requested_path}. Read it again before replacing.",
                error="stale file",
                metadata={
                    "error_code": "stale_file",
                    "path": requested_path,
                    "recoverable": True,
                    "recovery_tool": "read_file",
                    "delete_not_required": True,
                },
            )

        try:
            original_text = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            return ToolResult(
                ok=False,
                content=f"File is not valid UTF-8 text: {requested_path}",
                error="non-UTF-8 file",
            )
        if content == original_text:
            return ToolResult(
                ok=True,
                content=f"No changes needed for {requested_path}",
                metadata={
                    "path": str(target),
                    "changed_file": requested_path,
                    "operation": "write_file",
                    "write_mode": "replace",
                    "changed": False,
                    "snapshot_updated": False,
                },
            )

        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(content)
                temporary_path = Path(handle.name)
            shutil.copymode(target, temporary_path)
            os.replace(temporary_path, target)
        except OSError as exc:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
            return ToolResult(
                ok=False,
                content=f"Could not replace {requested_path}: {exc}",
                error=str(exc),
                metadata={"write_mode": "replace", "atomic_replace_failed": True},
            )

        written = target.read_bytes()
        context.record_file_snapshot(target, written, partial=False)
        context.record_changed_file(str(target.relative_to(context.repo_path)))
        return ToolResult(
            ok=True,
            content=f"Replaced file: {requested_path}",
            metadata={
                "path": str(target),
                "changed_file": requested_path,
                "operation": "write_file",
                "write_mode": "replace",
                "changed": True,
                "snapshot_updated": True,
                "atomic": True,
            },
        )

    def _file_exists_result(self, requested_path: str) -> ToolResult:
        return ToolResult(
            ok=False,
            content=(
                f"File appeared while creating {requested_path}. "
                "Read it before replacing it; do not delete and recreate it."
            ),
            error="file exists",
            metadata={
                "error_code": "file_exists",
                "path": requested_path,
                "recoverable": True,
                "recovery_tool": "read_file",
                "delete_not_required": True,
            },
        )
