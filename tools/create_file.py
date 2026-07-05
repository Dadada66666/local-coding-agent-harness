from __future__ import annotations

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


class CreateFileTool(BaseTool):
    name = "create_file"
    description = "Create a new UTF-8 text file inside WORKDIR. Fails if the file already exists."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "File path relative to WORKDIR.",
            },
            "content": {
                "type": "string",
                "description": "Complete file content.",
            },
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

    def check_permissions(self, args: dict, context, operation: Operation):
        requested_path = str(args.get("path", ""))
        if not requested_path:
            return None

        target = context.safe_path(requested_path)
        if target.exists():
            from runtime.permission import PermissionBehavior, PermissionDecision

            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                risk="file_exists",
                message=f"File already exists: {requested_path}. Use edit_file for precise edits.",
                operation=operation,
                decision_reason="tool_permission",
            )
        return None

    def validate(self, args: dict, context) -> None:
        if not args.get("path"):
            raise ToolValidationError("create_file requires path")
        if "content" not in args:
            raise ToolValidationError("create_file requires content")

    def call(self, args: dict, context) -> ToolResult:
        requested_path = args["path"]
        target = context.safe_path(requested_path)

        if target.exists():
            return ToolResult(
                ok=False,
                content=f"File already exists: {requested_path}. Use edit_file for precise edits.",
                error="file exists",
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(args["content"]), encoding="utf-8")
        context.changed_files.add(str(target.relative_to(context.repo_path)))

        return ToolResult(
            ok=True,
            content=f"Created file: {requested_path}",
            metadata={
                "changed_file": requested_path,
                "operation": "create_file",
            },
        )
