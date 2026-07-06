from __future__ import annotations

import subprocess

from tools.base import BaseTool, ToolResult


class ViewDiffTool(BaseTool):
    name = "view_diff"
    description = "View the current git diff."
    input_schema = {"type": "object", "properties": {}}

    read_only = True
    dangerous = False
    concurrency_safe = True

    def call(self, args: dict, context) -> ToolResult:
        workdir = context.safe_path(".")
        if not self._is_git_work_tree(workdir):
            return ToolResult(
                ok=True,
                content="No git repository; diff unavailable.",
                metadata={"git_repository": False},
            )

        completed = subprocess.run(
            ["git", "diff", "--"],
            cwd=workdir,
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

    def _is_git_work_tree(self, workdir) -> bool:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=workdir,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0 and completed.stdout.strip() == "true"
