from __future__ import annotations

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult


SKIP_NAMES = {".agent", ".git", ".venv", "venv", "node_modules", "__pycache__"}
MAX_ENTRIES = 200


class ListDirTool(BaseTool):
    name = "list_dir"
    description = "List visible files and directories."
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }

    read_only = True
    dangerous = False
    concurrency_safe = True

    def classify_operation(self, args: dict, context) -> Operation:
        requested_path = args.get("path", ".")
        return Operation(
            kind="fs.read",
            action="list",
            subject=str(requested_path),
            paths=[str(requested_path)],
            scope_key=f"read:list:{requested_path}",
            is_read_only=True,
        )

    def call(self, args: dict, context) -> ToolResult:
        defaulted_path = "path" not in args
        requested_path = args.get("path", ".")
        target = context.safe_path(requested_path)

        if context.access_policy.is_protected_resolved_read(context.repo_path, target):
            return ToolResult(
                ok=False,
                content="Permission denied: protected read path.",
                error="protected read",
                metadata={"protected_read": True},
            )
        if not target.exists():
            return ToolResult(ok=False, content=f"Path not found: {requested_path}", error="path not found")
        if not target.is_dir():
            return ToolResult(ok=False, content=f"Not a directory: {requested_path}", error="not a directory")

        visible_children = [
            child
            for child in target.iterdir()
            if child.name not in SKIP_NAMES
            and not context.access_policy.is_protected_resolved_read(context.repo_path, child)
        ]
        entries = []
        for child in sorted(visible_children, key=lambda item: (not item.is_dir(), item.name.lower())):
            entries.append(f"{child.name}/" if child.is_dir() else child.name)
            if len(entries) >= MAX_ENTRIES:
                break

        total_visible = len(visible_children)
        truncated = total_visible > len(entries)
        if truncated:
            entries.append(f"... {total_visible - len(entries)} more entries omitted")

        return ToolResult(
            ok=True,
            content="\n".join(entries) if entries else "Directory is empty.",
            metadata={
                "path": str(target),
                "requested_path": requested_path,
                "resolved_path": str(target),
                "defaulted_path": defaulted_path,
                "searches_names": True,
                "searches_content": False,
                "entry_count": len(entries),
                "truncated": truncated,
            },
        )
