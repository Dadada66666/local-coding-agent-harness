from __future__ import annotations

from tools.base import BaseTool, ToolResult, ToolValidationError


class CreateFileTool(BaseTool):
    name = "create_file"
    description = "Create a new UTF-8 text file inside WORKDIR. Fails if the file already exists unless overwrite=true."
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
            "overwrite": {
                "type": "boolean",
                "description": "Whether to overwrite an existing file. Default false.",
            },
        },
        "required": ["path", "content"],
    }

    read_only = False
    dangerous = True
    concurrency_safe = False

    def validate(self, args: dict, context) -> None:
        if not args.get("path"):
            raise ToolValidationError("create_file requires path")
        if "content" not in args:
            raise ToolValidationError("create_file requires content")

    def call(self, args: dict, context) -> ToolResult:
        requested_path = args["path"]
        target = context.safe_path(requested_path)
        overwrite = bool(args.get("overwrite", False))

        if target.exists() and not overwrite:
            return ToolResult(
                ok=False,
                content=f"File already exists: {requested_path}. Use edit_file or set overwrite=true.",
                error="file exists",
            )

        existed = target.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(args["content"]), encoding="utf-8")
        context.changed_files.add(str(target.relative_to(context.repo_path)))

        operation = "overwrite_file" if existed and overwrite else "create_file"
        return ToolResult(
            ok=True,
            content=f"Created file: {requested_path}" if operation == "create_file" else f"Overwrote file: {requested_path}",
            metadata={
                "changed_file": requested_path,
                "operation": operation,
            },
        )