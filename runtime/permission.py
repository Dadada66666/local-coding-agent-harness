from __future__ import annotations


class PermissionMode:
    READ_ONLY = "read_only"
    ACCEPT_EDITS = "accept_edits"
    MANUAL_APPROVAL = "manual_approval"


class PermissionGate:
    DENY_PATTERNS = [
        "rm -rf",
        "sudo",
        "chmod 777",
        "git push",
        "git reset --hard",
        "mkfs",
        "dd if=",
        "curl | sh",
        "wget | sh",
        ":(){ :|:& };:",
    ]

    SAFE_BASH_PREFIXES = [
        "pytest",
        "python -m pytest",
        "ruff check",
        "mypy",
        "npm test",
        "python -m unittest",
    ]

    def check(self, tool, args: dict, context) -> tuple[bool, str | None]:
        if getattr(tool, "read_only", False):
            return True, None

        if context.permission_mode == PermissionMode.READ_ONLY:
            return False, "Permission denied: read-only mode."

        if tool.name == "bash":
            return self._check_bash(args, context)

        if tool.name == "edit_file":
            return self._check_edit(args, context)

        if getattr(tool, "dangerous", False):
            if context.permission_mode == PermissionMode.MANUAL_APPROVAL:
                return self.ask_user(tool, args)

        return True, None

    def _check_bash(self, args: dict, context) -> tuple[bool, str | None]:
        command = str(args.get("command", ""))
        normalized = command.lower()

        if any(pattern in normalized for pattern in self.DENY_PATTERNS):
            return False, (
                "Permission denied: this operation intent is cancelled. "
                "Do not retry with alternative destructive commands."
            )

        if context.permission_mode == PermissionMode.MANUAL_APPROVAL:
            return self.ask_user_for_command(command)

        return True, None

    def _check_edit(self, args: dict, context) -> tuple[bool, str | None]:
        path = args.get("path", "")
        try:
            context.safe_path(path)
        except Exception:
            return False, f"Permission denied: path escapes repository: {path}"

        if context.permission_mode == PermissionMode.MANUAL_APPROVAL:
            return self.ask_user_for_edit(path)

        return True, None

    def ask_user(self, tool, args: dict) -> tuple[bool, str | None]:
        return self._ask(f"Allow tool {tool.name} with args {args}?")

    def ask_user_for_command(self, command: str) -> tuple[bool, str | None]:
        return self._ask(f"Allow command: {command}?")

    def ask_user_for_edit(self, path: str) -> tuple[bool, str | None]:
        return self._ask(f"Allow edit: {path}?")

    def _ask(self, question: str) -> tuple[bool, str | None]:
        try:
            answer = input(f"[approval] {question} [y/N] ").strip().lower()
        except EOFError:
            answer = ""

        if answer in {"y", "yes"}:
            return True, None

        return False, "Permission denied by user approval policy."

