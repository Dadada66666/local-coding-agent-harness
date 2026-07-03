from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


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
class BashRiskDecision:
    risk: str
    reason: str
    target_paths: list[str]
    command_prefix: str | None
    suggested_tool: str | None
    confidence: str

    def to_metadata(self) -> dict[str, Any]:
        return {
            "risk": self.risk,
            "reason": self.reason,
            "target_paths": self.target_paths,
            "command_prefix": self.command_prefix,
            "suggested_tool": self.suggested_tool,
            "confidence": self.confidence,
        }


@dataclass
class PermissionDecision:
    behavior: str
    risk: str
    message: str
    proposed_scope: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class RiskClassifier:
    PYTHON_OPEN_WRITE_RE = re.compile(
        r"""\bopen\s*\(\s*(['\"])(?P<path>[^'\"]+)\1\s*,\s*(?:mode\s*=\s*)?(['\"])(?P<mode>[^'\"]*[wax][^'\"]*)\3""",
        re.IGNORECASE,
    )
    PYTHON_PATH_WRITE_RE = re.compile(
        r"""(?:\bPath|\bpathlib\.Path)\s*\(\s*(['\"])(?P<path>[^'\"]+)\1\s*\)\s*\.\s*(?P<method>write_text|write_bytes)\s*\(""",
        re.IGNORECASE,
    )
    SHELL_REDIRECT_RE = re.compile(
        r"""(?<![<>])>>?\s*(?P<path>\"[^\"]+\"|'[^']+'|[^\s&|;]+)""",
        re.IGNORECASE,
    )

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
    FILE_WRITE_PATTERNS = {
        "set-content": ("PowerShell Set-Content writes file content.", "create_file"),
        "out-file": ("PowerShell Out-File writes command output to a file.", "create_file"),
        "add-content": ("PowerShell Add-Content appends file content.", "edit_file"),
        "sed -i": ("sed -i edits a file in place through shell.", "edit_file"),
        "tee ": ("tee can write command output to a file.", "create_file"),
        "new-item": ("PowerShell New-Item can create files or directories.", "create_file"),
        "copy-item": ("PowerShell Copy-Item writes a destination path.", "create_file"),
        "move-item": ("PowerShell Move-Item mutates filesystem paths.", "edit_file"),
    }
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
        "pwd",
        "tree",
        "get-childitem",
        "type",
        "cat",
        "get-content",
        "findstr",
        "grep",
        "rg",
    ]
    PYTHON_INLINE_PREFIXES = ["python -c", "python3 -c", "py -c"]

    def classify_bash(self, command: str) -> BashRiskDecision:
        normalized = f" {command.strip().lower()} "
        stripped = normalized.strip()
        command_prefix = self._command_prefix(command)

        destructive_pattern = self._matched_pattern(normalized, self.DESTRUCTIVE_PATTERNS)
        if destructive_pattern:
            return BashRiskDecision(
                risk=BashRisk.DESTRUCTIVE,
                reason=f"Matched destructive shell pattern: {destructive_pattern}.",
                target_paths=[],
                command_prefix=command_prefix,
                suggested_tool=None,
                confidence="high",
            )

        python_paths, python_write_kind, python_suggested_tool = self._python_write_details(command)
        if python_paths or python_write_kind:
            return BashRiskDecision(
                risk=BashRisk.FILE_WRITE_VIA_BASH,
                reason=f"Python {python_write_kind} writes a file through shell.",
                target_paths=python_paths,
                command_prefix=command_prefix,
                suggested_tool=python_suggested_tool,
                confidence="high" if python_paths else "medium",
            )

        if not self._is_python_inline(command_prefix):
            redirect_paths = self._redirection_targets(command)
            if redirect_paths:
                return BashRiskDecision(
                    risk=BashRisk.FILE_WRITE_VIA_BASH,
                    reason="Shell redirection writes command output to a file.",
                    target_paths=redirect_paths,
                    command_prefix=command_prefix,
                    suggested_tool="create_file",
                    confidence="high",
                )

        file_write = self._matched_file_write_pattern(normalized)
        if file_write is not None:
            pattern, reason, suggested_tool = file_write
            return BashRiskDecision(
                risk=BashRisk.FILE_WRITE_VIA_BASH,
                reason=f"{reason} Matched pattern: {pattern}.",
                target_paths=[],
                command_prefix=command_prefix,
                suggested_tool=suggested_tool,
                confidence="high",
            )

        network_pattern = self._matched_pattern(normalized, self.NETWORK_PATTERNS)
        if network_pattern:
            return BashRiskDecision(
                risk=BashRisk.NETWORK,
                reason=f"Matched network command pattern: {network_pattern}.",
                target_paths=[],
                command_prefix=command_prefix,
                suggested_tool=None,
                confidence="high",
            )

        safe_prefix = self._matched_prefix(stripped, self.SAFE_CHECK_PREFIXES)
        if safe_prefix:
            return BashRiskDecision(
                risk=BashRisk.SAFE_CHECK,
                reason=f"Command starts with safe check prefix: {safe_prefix}.",
                target_paths=[],
                command_prefix=safe_prefix,
                suggested_tool=None,
                confidence="high",
            )

        read_only_prefix = self._matched_prefix(stripped, self.READ_ONLY_PREFIXES)
        if read_only_prefix:
            return BashRiskDecision(
                risk=BashRisk.READ_ONLY_COMMAND,
                reason=f"Command starts with read-only prefix: {read_only_prefix}.",
                target_paths=[],
                command_prefix=read_only_prefix,
                suggested_tool=None,
                confidence="medium",
            )

        return BashRiskDecision(
            risk=BashRisk.UNKNOWN,
            reason="No deterministic risk rule matched this shell command.",
            target_paths=[],
            command_prefix=command_prefix,
            suggested_tool=None,
            confidence="low",
        )

    def _python_write_details(self, command: str) -> tuple[list[str], str | None, str | None]:
        paths: list[str] = []
        modes: list[str] = []
        methods: list[str] = []

        for match in self.PYTHON_OPEN_WRITE_RE.finditer(command):
            path = self._clean_target_path(match.group("path"))
            if path:
                paths.append(path)
            modes.append(match.group("mode").lower())

        for match in self.PYTHON_PATH_WRITE_RE.finditer(command):
            path = self._clean_target_path(match.group("path"))
            if path:
                paths.append(path)
            methods.append(match.group("method"))

        if not modes and not methods:
            return [], None, None

        if modes and methods:
            kind = "open(..., write mode) or Path.write_* API"
        elif modes:
            kind = "open(..., write mode)"
        else:
            kind = "Path.write_text/write_bytes API"

        suggested_tool = "edit_file" if any(mode.startswith("a") for mode in modes) else "create_file"
        return self._unique(paths), kind, suggested_tool

    def _redirection_targets(self, command: str) -> list[str]:
        paths: list[str] = []
        for match in self.SHELL_REDIRECT_RE.finditer(command):
            path = self._clean_target_path(match.group("path"))
            if path and path not in {"&1", "&2"}:
                paths.append(path)
        return self._unique(paths)

    def _matched_file_write_pattern(self, normalized: str) -> tuple[str, str, str] | None:
        for pattern, details in self.FILE_WRITE_PATTERNS.items():
            if pattern in normalized:
                reason, suggested_tool = details
                return pattern, reason, suggested_tool
        return None

    def _matched_pattern(self, normalized: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            if pattern in normalized:
                return pattern
        return None

    def _matched_prefix(self, stripped_command: str, prefixes: list[str]) -> str | None:
        for prefix in prefixes:
            if stripped_command == prefix or stripped_command.startswith(f"{prefix} "):
                return prefix
        return None

    def _command_prefix(self, command: str) -> str | None:
        stripped = command.strip()
        if not stripped:
            return None

        lowered = stripped.lower()
        known_prefixes = sorted(
            self.SAFE_CHECK_PREFIXES
            + self.READ_ONLY_PREFIXES
            + self.PYTHON_INLINE_PREFIXES
            + ["python -m", "python", "py", "cmd /c", "powershell", "pwsh"],
            key=len,
            reverse=True,
        )
        for prefix in known_prefixes:
            if lowered.startswith(prefix):
                return prefix

        return " ".join(stripped.split()[:2])

    def _is_python_inline(self, command_prefix: str | None) -> bool:
        return command_prefix in self.PYTHON_INLINE_PREFIXES

    def _clean_target_path(self, path: str) -> str | None:
        cleaned = path.strip().strip("'\"").rstrip(",)")
        if not cleaned:
            return None
        if cleaned.lower() in {"nul", "/dev/null"}:
            return None
        if cleaned.startswith(("$", "%")):
            return None
        return cleaned

    def _unique(self, paths: list[str]) -> list[str]:
        seen: set[str] = set()
        unique_paths: list[str] = []
        for path in paths:
            if path not in seen:
                seen.add(path)
                unique_paths.append(path)
        return unique_paths


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
                metadata=decision.metadata,
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
        bash_decision = self.risk_classifier.classify_bash(command)
        risk = bash_decision.risk
        metadata = {"bash_risk": bash_decision.to_metadata()}

        path_escape = self._check_bash_target_paths(bash_decision, context, metadata)
        if path_escape is not None:
            return path_escape

        if risk == BashRisk.DESTRUCTIVE:
            return PermissionDecision(
                behavior=PermissionBehavior.DENY,
                risk=risk,
                message=(
                    "Permission denied: this operation intent is cancelled. "
                    f"Reason: {bash_decision.reason} "
                    "Do not retry with alternative destructive commands."
                ),
                metadata=metadata,
            )

        if risk in {BashRisk.SAFE_CHECK, BashRisk.READ_ONLY_COMMAND}:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                risk=risk,
                message=bash_decision.reason,
                metadata=metadata,
            )

        if risk == BashRisk.FILE_WRITE_VIA_BASH:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message=self._bash_file_write_message(bash_decision, context),
                proposed_scope=None,
                metadata=metadata,
            )

        if risk == BashRisk.NETWORK:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message=bash_decision.reason,
                proposed_scope="bash:network",
                metadata=metadata,
            )

        if context.permission_mode == PermissionMode.ACCEPT_EDITS:
            return PermissionDecision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message="Model requested an unknown shell command; approval required.",
                proposed_scope="bash:unknown",
                metadata=metadata,
            )

        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            risk=risk,
            message=f"Model requested a shell command while permission mode is {context.permission_mode}.",
            proposed_scope=f"bash:{risk}",
            metadata=metadata,
        )

    def _check_bash_target_paths(
        self,
        bash_decision: BashRiskDecision,
        context,
        metadata: dict[str, Any],
    ) -> PermissionDecision | None:
        for path in bash_decision.target_paths:
            try:
                context.safe_path(path)
            except Exception:
                return PermissionDecision(
                    behavior=PermissionBehavior.DENY,
                    risk="path_escape",
                    message=f"Permission denied: Bash command targets path outside WORKDIR: {path}",
                    metadata=metadata,
                )
        return None

    def _bash_file_write_message(self, bash_decision: BashRiskDecision, context) -> str:
        parts = [bash_decision.reason]

        if bash_decision.target_paths:
            parts.append(f"Target paths: {', '.join(bash_decision.target_paths)}.")

        suggested_tool = self._suggested_file_tool(bash_decision, context)
        if suggested_tool:
            parts.append(f"Use {suggested_tool} instead of Bash for this file operation.")
        else:
            parts.append("Prefer create_file for new files or edit_file for precise edits.")

        return " ".join(parts)

    def _suggested_file_tool(self, bash_decision: BashRiskDecision, context) -> str | None:
        if not bash_decision.target_paths:
            return bash_decision.suggested_tool

        existing_targets = []
        for path in bash_decision.target_paths:
            try:
                existing_targets.append(context.safe_path(path).exists())
            except Exception:
                continue

        if existing_targets and all(existing_targets):
            return "edit_file"
        return bash_decision.suggested_tool

    def _ask_user(self, decision: PermissionDecision, tool, args: dict, context) -> PermissionDecision:
        print("\n[permission request]")
        print(f"Tool: {tool.name}")
        print(f"Risk: {decision.risk}")
        print(f"Reason: {decision.message}")
        self._print_decision_metadata(decision)
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
                metadata=decision.metadata,
            )

        if answer in {"a", "all", "run"} and decision.proposed_scope:
            context.approved_permission_scopes.add(decision.proposed_scope)
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                risk=decision.risk,
                message=f"Allowed for this run scope: {decision.proposed_scope}",
                proposed_scope=decision.proposed_scope,
                metadata=decision.metadata,
            )

        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            risk=decision.risk,
            message="Permission denied by user approval policy.",
            proposed_scope=decision.proposed_scope,
            metadata=decision.metadata,
        )

    def _print_decision_metadata(self, decision: PermissionDecision) -> None:
        bash_risk = decision.metadata.get("bash_risk")
        if not isinstance(bash_risk, dict):
            return

        if bash_risk.get("target_paths"):
            print(f"Target paths: {bash_risk['target_paths']}")
        if bash_risk.get("suggested_tool"):
            print(f"Suggested tool: {bash_risk['suggested_tool']}")
        if bash_risk.get("confidence"):
            print(f"Confidence: {bash_risk['confidence']}")