from __future__ import annotations

import subprocess

from tools.base import BaseTool, ToolResult, ToolValidationError


DEFAULT_TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 12000


class BashTool(BaseTool):
    name = "bash"
    description = "Run a shell command in the repository, typically tests or linters."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Command to run from repo root."},
            "timeout": {"type": "integer", "description": "Timeout in seconds."},
        },
        "required": ["command"],
    }

    read_only = False
    dangerous = True
    concurrency_safe = False

    def validate(self, args: dict, context) -> None:
        if not args.get("command"):
            raise ToolValidationError("bash requires command")
        if int(args.get("timeout", DEFAULT_TIMEOUT_SECONDS)) <= 0:
            raise ToolValidationError("timeout must be > 0")

    def call(self, args: dict, context) -> ToolResult:
        command = str(args["command"])
        timeout = int(args.get("timeout", DEFAULT_TIMEOUT_SECONDS))

        try:
            completed = subprocess.run(
                command,
                cwd=context.repo_path,
                shell=True,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            output = ((exc.stdout or "") + (exc.stderr or "")).strip()
            preview = self._truncate_output(output)
            return ToolResult(
                ok=False,
                content=f"Command timed out after {timeout}s.\n{preview}".strip(),
                error=f"timeout after {timeout}s",
                metadata={"timeout": timeout, "timed_out": True},
            )

        output = (completed.stdout + completed.stderr).strip()
        original_chars = len(output)
        content = self._truncate_output(output)

        return ToolResult(
            ok=completed.returncode == 0,
            content=content,
            error=None if completed.returncode == 0 else f"command exited {completed.returncode}",
            metadata={
                "command": command,
                "returncode": completed.returncode,
                "truncated": original_chars > len(content),
                "original_chars": original_chars,
            },
        )

    def _truncate_output(self, output: str) -> str:
        if len(output) <= MAX_OUTPUT_CHARS:
            return output
        omitted = len(output) - MAX_OUTPUT_CHARS
        return f"{output[:MAX_OUTPUT_CHARS]}\n... {omitted} chars omitted"

