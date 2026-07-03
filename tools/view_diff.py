from __future__ import annotations

import subprocess

from tools.base import BaseTool, ToolResult


class ViewDiffTool(BaseTool):
    name = "view_diff"
    description = "View the current git diff for repository changes."
    input_schema = {"type": "object", "properties": {}}

    read_only = True
    dangerous = False
    concurrency_safe = True

    def call(self, args: dict, context) -> ToolResult:
        completed = subprocess.run(
            ["git", "diff", "--"],
            cwd=context.safe_path("."),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        content = (completed.stdout if completed.returncode == 0 else completed.stderr) or "No diff."
        return ToolResult(
            ok=completed.returncode == 0,
            content=content,
            error=None if completed.returncode == 0 else "git diff failed",
            metadata={"returncode": completed.returncode},
        )