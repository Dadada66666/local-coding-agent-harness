from __future__ import annotations

import os
import platform
import shutil
import subprocess

from runtime.operation import Operation
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
            "input": {"type": "string", "description": "Optional stdin content for non-interactive commands."},
            "purpose": {"type": "string", "description": "Optional purpose label for trace metadata."},
        },
        "required": ["command"],
    }

    read_only = False
    dangerous = True
    concurrency_safe = False

    def classify_operation(self, args: dict, context) -> Operation:
        command = str(args.get("command", ""))
        return Operation(
            kind="process.exec",
            action="bash",
            subject=self._command_subject(command),
            command=command,
            scope_key=None,
            is_read_only=False,
            is_destructive=True,
        )

    def validate(self, args: dict, context) -> None:
        if not args.get("command"):
            raise ToolValidationError("bash requires command")
        if int(args.get("timeout", DEFAULT_TIMEOUT_SECONDS)) <= 0:
            raise ToolValidationError("timeout must be > 0")

    def call(self, args: dict, context) -> ToolResult:
        command = str(args["command"])
        timeout = int(args.get("timeout", DEFAULT_TIMEOUT_SECONDS))
        stdin_content = args.get("input")
        purpose = args.get("purpose")
        argv = self._build_command_argv(command)
        shell_name = self._shell_name()
        sandbox_metadata = self._sandbox_metadata(context)

        sandbox = getattr(context, "sandbox", None)
        if sandbox is not None and sandbox.should_wrap_command(command):
            wrapped_argv = sandbox.wrap_argv(argv)
            sandbox_metadata["wrapped"] = wrapped_argv != argv
            argv = wrapped_argv

        stdin_mode = "provided" if stdin_content is not None else "devnull"
        stdin_kwargs = {"input": str(stdin_content)} if stdin_content is not None else {"stdin": subprocess.DEVNULL}

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
                env=self._build_env(),
                **stdin_kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            output = self._combine_output(exc.stdout, exc.stderr).strip()
            preview = self._truncate_output(output)
            return ToolResult(
                ok=False,
                content=f"Command timed out after {timeout}s.\n{preview}".strip(),
                error=f"timeout after {timeout}s",
                metadata=self._metadata(
                    command=command,
                    shell_name=shell_name,
                    stdin_mode=stdin_mode,
                    sandbox=sandbox_metadata,
                    purpose=purpose,
                    timeout=timeout,
                    timed_out=True,
                ),
            )

        output = self._combine_output(completed.stdout, completed.stderr).strip()
        original_chars = len(output)
        content = self._truncate_output(output)

        return ToolResult(
            ok=completed.returncode == 0,
            content=content,
            error=None if completed.returncode == 0 else f"command exited {completed.returncode}",
            metadata=self._metadata(
                command=command,
                shell_name=shell_name,
                stdin_mode=stdin_mode,
                sandbox=sandbox_metadata,
                purpose=purpose,
                returncode=completed.returncode,
                truncated=original_chars > len(content),
                original_chars=original_chars,
            ),
        )

    def _build_command_argv(self, command: str) -> list[str]:
        if platform.system() == "Windows":
            wrapped_command = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "$OutputEncoding = [System.Text.Encoding]::UTF8; "
                "$env:PYTHONIOENCODING = 'utf-8'; "
                "$env:PYTHONUTF8 = '1'; "
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

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    def _shell_name(self) -> str:
        if platform.system() == "Windows":
            return f"{self._windows_shell_executable()} -NoProfile -Command"
        return "/bin/sh -lc"

    def _windows_shell_executable(self) -> str:
        return shutil.which("pwsh") or shutil.which("powershell") or "powershell.exe"

    def _command_subject(self, command: str) -> str:
        parts = command.strip().split()
        if not parts:
            return "bash"
        return " ".join(parts[:2])

    def _sandbox_metadata(self, context) -> dict:
        sandbox = getattr(context, "sandbox", None)
        if sandbox is None:
            return {
                "enabled": False,
                "available": False,
                "strong_boundary": False,
                "reason": None,
                "settings_path": None,
                "executable_path": None,
                "settings_applied": False,
                "wrapped": False,
            }

        metadata = sandbox.metadata()
        metadata["wrapped"] = False
        return metadata

    def _metadata(self, command: str, shell_name: str, stdin_mode: str, sandbox: dict, purpose=None, **extra) -> dict:
        metadata = {
            "command": command,
            "shell": shell_name,
            "stdin": stdin_mode,
            "sandbox": sandbox,
        }
        if purpose is not None:
            metadata["purpose"] = str(purpose)
        metadata.update(extra)
        return metadata

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
