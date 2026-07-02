from __future__ import annotations

from tools.base import BaseTool, ToolResult, ToolValidationError


class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Precisely replace old_text with new_text in a UTF-8 text file."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "File path relative to repo root."},
            "old_text": {"type": "string", "description": "Existing text to replace."},
            "new_text": {"type": "string", "description": "Replacement text."},
            "occurrence": {
                "type": "integer",
                "description": "Optional 1-based occurrence to replace when old_text appears multiple times.",
            },
        },
        "required": ["path", "old_text", "new_text"],
    }

    read_only = False
    dangerous = True
    concurrency_safe = False

    def validate(self, args: dict, context) -> None:
        if not args.get("path"):
            raise ToolValidationError("edit_file requires path")
        if "old_text" not in args or "new_text" not in args:
            raise ToolValidationError("edit_file requires old_text and new_text")
        if args["old_text"] == "":
            raise ToolValidationError("old_text must not be empty")
        if "occurrence" in args and int(args["occurrence"]) <= 0:
            raise ToolValidationError("occurrence must be a 1-based positive integer")

    def call(self, args: dict, context) -> ToolResult:
        requested_path = args["path"]
        target = context.safe_path(requested_path)

        if not target.exists():
            return ToolResult(ok=False, content=f"File not found: {requested_path}", error="file not found")
        if not target.is_file():
            return ToolResult(ok=False, content=f"Not a file: {requested_path}", error="not a file")

        old_text = str(args["old_text"])
        new_text = str(args["new_text"])
        original = target.read_text(encoding="utf-8")
        count = original.count(old_text)

        if count == 0:
            return ToolResult(ok=False, content="text not found", error="text not found")

        if "occurrence" in args:
            occurrence = int(args["occurrence"])
            if occurrence > count:
                return ToolResult(
                    ok=False,
                    content=f"occurrence {occurrence} not found; old_text appears {count} times",
                    error="occurrence not found",
                    metadata={"occurrences": count},
                )
            updated = self._replace_occurrence(original, old_text, new_text, occurrence)
        else:
            if count > 1:
                return ToolResult(
                    ok=False,
                    content=f"old_text is not unique; it appears {count} times",
                    error="old_text is not unique",
                    metadata={"occurrences": count},
                )
            updated = original.replace(old_text, new_text, 1)

        target.write_text(updated, encoding="utf-8")
        context.changed_files.add(str(target.relative_to(context.repo_path)))

        return ToolResult(
            ok=True,
            content=f"edited {requested_path}",
            metadata={"path": str(target), "changed_file": requested_path, "occurrences": count},
        )

    def _replace_occurrence(self, text: str, old_text: str, new_text: str, occurrence: int) -> str:
        start = -1
        search_from = 0
        for _ in range(occurrence):
            start = text.find(old_text, search_from)
            search_from = start + len(old_text)

        end = start + len(old_text)
        return text[:start] + new_text + text[end:]

