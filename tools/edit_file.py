from __future__ import annotations

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Replace one or more exact text snippets in a UTF-8 text file."
    input_schema = {
        "type": "object",
        "description": "Provide old_text/new_text for one replacement, or edits for multiple replacements.",
        "properties": {
            "path": {"type": "string", "description": "File path relative to WORKDIR."},
            "old_text": {
                "type": "string",
                "description": "Exact existing text to replace for a single edit. Do not describe line numbers.",
            },
            "new_text": {
                "type": "string",
                "description": "Exact replacement text for a single edit.",
            },
            "occurrence": {
                "type": "integer",
                "description": "Optional 1-based occurrence to replace when old_text appears multiple times.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence of old_text in the file.",
            },
            "edits": {
                "type": "array",
                "description": "Optional batch of exact replacements for the same file.",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_text": {
                            "type": "string",
                            "description": "Exact existing text to replace.",
                        },
                        "new_text": {
                            "type": "string",
                            "description": "Exact replacement text.",
                        },
                        "occurrence": {
                            "type": "integer",
                            "description": "Optional 1-based occurrence for this replacement.",
                        },
                        "replace_all": {
                            "type": "boolean",
                            "description": "Replace every occurrence of old_text for this replacement.",
                        },
                    },
                    "required": ["old_text", "new_text"],
                },
            },
        },
        "required": ["path"],
    }

    read_only = False
    dangerous = True
    concurrency_safe = False

    def classify_operation(self, args: dict, context) -> Operation:
        requested_path = args.get("path", "")
        return Operation(
            kind="fs.write",
            action="edit",
            subject=str(requested_path),
            paths=[str(requested_path)] if requested_path else [],
            scope_key=f"write:edit:{requested_path}",
            terminal_on_deny=True,
        )

    def validate(self, args: dict, context) -> None:
        if not args.get("path"):
            raise ToolValidationError("edit_file requires path")
        self._edits_from_args(args)

    def call(self, args: dict, context) -> ToolResult:
        requested_path = args["path"]
        target = context.safe_path(requested_path)

        if not target.exists():
            return ToolResult(ok=False, content=f"File not found: {requested_path}", error="file not found")
        if not target.is_file():
            return ToolResult(ok=False, content=f"Not a file: {requested_path}", error="not a file")

        snapshot = context.read_file_state.get(str(target))
        if snapshot is None:
            return ToolResult(
                ok=False,
                content=f"File has not been read yet: {requested_path}. Use read_file first.",
                error="file not read",
            )

        if target.stat().st_mtime_ns != snapshot.mtime_ns:
            return ToolResult(
                ok=False,
                content=f"File changed since last read: {requested_path}. Read it again before editing.",
                error="stale file",
            )

        original = target.read_text(encoding="utf-8")
        edits = self._edits_from_args(args)
        updated = original
        applied = []

        for index, edit in enumerate(edits, start=1):
            result = self._apply_edit(updated, edit, index, requested_path)
            if isinstance(result, ToolResult):
                return result
            updated, count = result
            applied.append({"index": index, "occurrences": count})

        if updated == original:
            return ToolResult(
                ok=True,
                content=f"No changes needed for {requested_path}",
                metadata={
                    "path": str(target),
                    "changed_file": requested_path,
                    "edit_count": len(applied),
                    "edits": applied,
                    "changed": False,
                    "snapshot_updated": False,
                },
            )

        target.write_text(updated, encoding="utf-8")
        context.record_file_snapshot(target, target.read_bytes(), partial=False)
        context.record_changed_file(str(target.relative_to(context.repo_path)))

        return ToolResult(
            ok=True,
            content=f"Edited {requested_path} ({len(applied)} replacement{'s' if len(applied) != 1 else ''})",
            metadata={
                "path": str(target),
                "changed_file": requested_path,
                "edit_count": len(applied),
                "edits": applied,
                "changed": True,
                "snapshot_updated": True,
            },
        )

    def _edits_from_args(self, args: dict) -> list[dict]:
        has_batch = "edits" in args
        has_single = "old_text" in args or "new_text" in args

        if has_batch and has_single:
            raise ToolValidationError("use either edits or old_text/new_text, not both")

        if has_batch:
            edits = args["edits"]
            if not isinstance(edits, list) or not edits:
                raise ToolValidationError("edits must be a non-empty list")
            normalized = []
            for index, edit in enumerate(edits, start=1):
                if not isinstance(edit, dict):
                    raise ToolValidationError(f"edit {index} must be an object")
                normalized.append(self._normalize_edit(edit, index))
            return normalized

        if "old_text" not in args or "new_text" not in args:
            raise ToolValidationError("edit_file requires old_text/new_text or edits")
        return [self._normalize_edit(args, 1)]

    def _normalize_edit(self, edit: dict, index: int) -> dict:
        if "old_text" not in edit or "new_text" not in edit:
            raise ToolValidationError(f"edit {index} requires old_text and new_text")
        old_text = str(edit["old_text"])
        new_text = str(edit["new_text"])
        if old_text == "":
            raise ToolValidationError(f"edit {index} old_text must not be empty")

        occurrence = edit.get("occurrence")
        replace_all = bool(edit.get("replace_all", False))
        if occurrence is not None and replace_all:
            raise ToolValidationError(f"edit {index} cannot combine occurrence and replace_all")
        if occurrence is not None:
            try:
                occurrence = int(occurrence)
            except (TypeError, ValueError) as exc:
                raise ToolValidationError(f"edit {index} occurrence must be an integer") from exc
            if occurrence <= 0:
                raise ToolValidationError(f"edit {index} occurrence must be a 1-based positive integer")

        return {
            "old_text": old_text,
            "new_text": new_text,
            "occurrence": occurrence,
            "replace_all": replace_all,
        }

    def _apply_edit(self, text: str, edit: dict, index: int, requested_path: str) -> tuple[str, int] | ToolResult:
        old_text = edit["old_text"]
        new_text = edit["new_text"]
        count = text.count(old_text)

        if count == 0:
            return ToolResult(
                ok=False,
                content=f"edit {index}: old_text not found in {requested_path}",
                error="old_text not found",
                metadata={"failed_edit": index},
            )

        occurrence = edit.get("occurrence")
        if edit.get("replace_all"):
            return text.replace(old_text, new_text), count

        if occurrence is not None:
            if occurrence > count:
                return ToolResult(
                    ok=False,
                    content=f"edit {index}: occurrence {occurrence} not found; old_text appears {count} times",
                    error="occurrence not found",
                    metadata={"failed_edit": index, "occurrences": count},
                )
            return self._replace_occurrence(text, old_text, new_text, occurrence), count

        if count > 1:
            return ToolResult(
                ok=False,
                content=f"edit {index}: old_text appears multiple times; provide occurrence.",
                error="ambiguous edit",
                metadata={"failed_edit": index, "occurrences": count},
            )

        return text.replace(old_text, new_text, 1), count

    def _replace_occurrence(self, text: str, old_text: str, new_text: str, occurrence: int) -> str:
        start = -1
        search_from = 0
        for _ in range(occurrence):
            start = text.find(old_text, search_from)
            search_from = start + len(old_text)

        end = start + len(old_text)
        return text[:start] + new_text + text[end:]
