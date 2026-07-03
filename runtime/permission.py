from __future__ import annotations

from dataclasses import dataclass


class PermissionMode:
    READ_ONLY = "read_only"
    ACCEPT_EDITS = "accept_edits"
    MANUAL_APPROVAL = "manual_approval"


class PermissionBehavior:
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class BashRisk:
    SAFE_CHECK = "safe_check"
    READ_ONLY_COMMAND = "read_only_command"
    FILE_WRITE_VIA_BASH = "file_write_via_bash"
    DESTRUCTIVE = "destructive"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PermissionDecision:
    behavior: str
    risk: str
    message: str
    proposed_scope: str | None = None


class RiskClassifier:
    DESTRUCTIVE_PATTERNS = [
        "rm -rf",
        "rm ",
        "git reset --hard",
        "git clean",
        "sudo",
        "chmod 777",
        "mkfs",
        "dd if=",
        "shutdown",
        "reboot",
        ":(){ :|:& };:",
        "remove-item",
        "rmdir",
        " del ",
        "erase ",
    ]
    FILE_WRITE_PATTERNS = [
        ">",
        ">>",
        "set-content",
        "out-file",
        "add-content",
        "sed -i",
        "tee ",
        "new-item",
        "copy-item",
        "move-item",
    ]
    NETWORK_PATTERNS = [
        "curl",
        "wget",
        "invoke-webrequest",
        "invoke-restmethod",
        "irm ",
        "iwr ",
        "pip install",
        "npm install",
        "git clone",
        "git pull",
        "git fetch",
    ]
    SAFE_CHECK_PREFIXES = [
        "pytest",
        "python -m pytest",
        "ruff check",
        "mypy",
        "npm test",
        "python -m unittest",
    ]
    READ_ONLY_PREFIXES = [
        "git status",
        "git diff",
        "git log",
        "dir",
        "ls",
        "get-childitem",
        "type",
        "cat",
        "get-content",
        "findstr",
    ]

    def classify_bash(self, command: str) -> str:
        normalized = f" {command.strip().lower()} "

        if any(pattern in normalized for pattern in self.DESTRUCTIVE_PATTERNS):
            return BashRisk.DESTRUCTIVE
        if any(pattern in normalized for pattern in self.FILE_WRITE_PATTERNS):
            return BashRisk.FILE_WRITE_VIA_BASH
        if any(pattern in normalized for pattern in self.NETWORK_PATTERNS):
            return BashRisk.NETWORK

        stripped = normalized.strip()
        if any(stripped.startswith(prefix) for prefix in self.SAFE_CHECK_PREFIXES):
            return BashRisk.SAFE_CHECK
        if any(stripped.startswith(prefix) for prefix in self.READ_ONLY_PREFIXES):
            return BashRisk.READ_ONLY_COMMAND

        return BashRisk.UNKNOWN


class PermissionGate:
    def __init__(self, risk_classifier: RiskClassifier | None = None) -> None:
        self.risk_classifier = risk_classifier or RiskClassifier()

    def check(self, tool, args: dict, context) -> PermissionDecision:
        path = args.get("path")
        if path is not None:
            try:
                context.safe_path(path)
            except Exception:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    risk="path_escape",
                    message=f"Permission denied: path escapes WORKDIR: {path}",
                )

        if tool.name == "create_file":
            return self._check_create_file(args, context)

        if tool.name == "edit_file":
            return self._check_edit(args, context)

        if tool.name == "bash":
            return self._check_bash(args, context)

        if getattr(tool, "read_only", False):
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                risk="read_only_tool",
                message="Allowed read-only tool.",
            )

        if context.permission_mode == PermissionMode.READ_ONLY:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                risk="write_tool_in_read_only",
                message=f"Model requested {tool.name} while permission mode is read_only.",
                proposed_scope=tool.name,
            )

        if context.permission_mode == PermissionMode.MANUAL_APPROVAL or getattr(tool, "dangerous", False):
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                risk="dangerous_tool",
                message=f"Model requested {tool.name}.",
                proposed_scope=tool.name,
            )

        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            risk="accepted_tool",
            message="Allowed by permission mode.",
        )

    def resolve(self, decision: PermissionDecision, tool, args: dict, context) -> PermissionDecision:
        if decision.behavior != PermissionBehavior.ASK:
            return decision

        if decision.proposed_scope and decision.proposed_scope in context.approved_permission_scopes:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                risk=decision.risk,
                message=f"Allowed by prior approval for scope: {decision.proposed_scope}",
                proposed_scope=decision.proposed_scope,
            )

        return self._ask_user(decision, tool, args, context)

    def _check_create_file(self, args: dict, context) -> PermissionDecision:
        path = str(args.get("path", ""))
        target = context.safe_path(path)

        if target.exists():
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                risk="file_exists",
                message=f"File already exists: {path}. Use edit_file for precise edits.",
            )

        if context.permission_mode == PermissionMode.ACCEPT_EDITS:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                risk="file_create",
                message=f"Allowed create_file for {path}.",
            )

        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            risk="file_create",
            message=f"Model requests creating {path} while permission mode is {context.permission_mode}.",
            proposed_scope="create_file",
        )
    def _check_edit(self, args: dict, context) -> PermissionDecision:
        path = str(args.get("path", ""))
        target = context.safe_path(path)
        old_text = args.get("old_text")

        if target.exists() and target.is_file() and old_text is not None:
            text = target.read_text(encoding="utf-8")
            count = text.count(str(old_text))
            if count == 0:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    risk="invalid_edit",
                    message=f"old_text not found in {path}",
                )
            if count > 1 and args.get("occurrence") is None:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    risk="ambiguous_edit",
                    message="old_text appears multiple times; provide occurrence.",
                )

        if context.permission_mode == PermissionMode.ACCEPT_EDITS:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                risk="file_edit",
                message=f"Allowed edit_file for {path}.",
            )

        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            risk="file_edit",
            message=f"Model requests modifying {path} while permission mode is {context.permission_mode}.",
            proposed_scope="edit_file",
        )

    def _check_bash(self, args: dict, context) -> PermissionDecision:
        command = str(args.get("command", ""))
        risk = self.risk_classifier.classify_bash(command)

        if risk == BashRisk.DESTRUCTIVE:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                risk=risk,
                message=(
                    "Permission denied: this operation intent is cancelled. "
                    "Do not retry with alternative destructive commands."
                ),
            )

        if risk in {BashRisk.SAFE_CHECK, BashRisk.READ_ONLY_COMMAND}:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                risk=risk,
                message="Allowed read-only or safe check command.",
            )

        if risk == BashRisk.FILE_WRITE_VIA_BASH:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message=(
                    "Model requested a shell command that may write files. "
                    "Prefer create_file for new files or edit_file for precise edits."
                ),
                proposed_scope=None,
            )

        if risk == BashRisk.NETWORK:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message="Model requested a network command.",
                proposed_scope="bash:network",
            )

        if context.permission_mode == PermissionMode.ACCEPT_EDITS:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message="Model requested an unknown shell command; approval required.",
                proposed_scope="bash:unknown",
            )

        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            risk=risk,
            message=f"Model requested a shell command while permission mode is {context.permission_mode}.",
            proposed_scope=f"bash:{risk}",
        )

    def _ask_user(self, decision: PermissionDecision, tool, args: dict, context) -> PermissionDecision:
        print("\n[permission request]")
        print(f"Tool: {tool.name}")
        print(f"Risk: {decision.risk}")
        print(f"Reason: {decision.message}")
        print(f"Args: {args}")
        if decision.proposed_scope:
            print("Allow? [y] once / [a] this run scope / [N] deny")
        else:
            print("Allow? [y] once / [N] deny")

        try:
            answer = input("permission> ").strip().lower()
        except EOFError:
            answer = ""

        if answer in {"y", "yes", "once"}:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                risk=decision.risk,
                message="Allowed once by user approval.",
                proposed_scope=decision.proposed_scope,
            )

        if answer in {"a", "all", "run"} and decision.proposed_scope:
            context.approved_permission_scopes.add(decision.proposed_scope)
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                risk=decision.risk,
                message=f"Allowed for this run scope: {decision.proposed_scope}",
                proposed_scope=decision.proposed_scope,
            )

        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            risk=decision.risk,
            message="Permission denied by user approval policy.",
            proposed_scope=decision.proposed_scope,
        )
