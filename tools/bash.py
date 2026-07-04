from __future__ import annotations

import platform
import shutil
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
        argv = self._build_command_argv(command)
        shell_name = self._shell_name()

        try:
            completed = subprocess.run(
                argv,
                cwd=context.repo_path,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            output = self._combine_output(exc.stdout, exc.stderr).strip()
            preview = self._truncate_output(output)
            return ToolResult(
                ok=False,
                content=f"Command timed out after {timeout}s.\n{preview}".strip(),
                error=f"timeout after {timeout}s",
                metadata={
                    "command": command,
                    "shell": shell_name,
                    "timeout": timeout,
                    "timed_out": True,
                },
            )

        output = self._combine_output(completed.stdout, completed.stderr).strip()
        original_chars = len(output)
        content = self._truncate_output(output)

        return ToolResult(
            ok=completed.returncode == 0,
            content=content,
            error=None if completed.returncode == 0 else f"command exited {completed.returncode}",
            metadata={
                "command": command,
                "shell": shell_name,
                "returncode": completed.returncode,
                "truncated": original_chars > len(content),
                "original_chars": original_chars,
            },
        )

    def _build_command_argv(self, command: str) -> list[str]:
        if platform.system() == "Windows":
            wrapped_command = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "$OutputEncoding = [System.Text.Encoding]::UTF8; "
                f"{command}"
            )
            return [
                self._windows_shell_executable(),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                wrapped_command,
            ]

        return ["/bin/sh", "-lc", command]

    def _shell_name(self) -> str:
        if platform.system() == "Windows":
            return f"{self._windows_shell_executable()} -NoProfile -Command"
        return "/bin/sh -lc"

    def _windows_shell_executable(self) -> str:
        return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"

    def _combine_output(self, stdout, stderr) -> str:
        return self._to_text(stdout) + self._to_text(stderr)

    def _to_text(self, value) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def _truncate_output(self, output: str) -> str:
        if len(output) <= MAX_OUTPUT_CHARS:
            return output
        omitted = len(output) - MAX_OUTPUT_CHARS
        return f"{output[:MAX_OUTPUT_CHARS]}\n... {omitted} chars omitted"
