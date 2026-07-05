from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from runtime.operation import Operation
from runtime.permission_rules import PermissionRule, PermissionRuleValue


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
    operation: Operation | None = None
    terminal_on_deny: bool = False
    decision_reason: str | None = None


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
    HEREDOC_START_RE = re.compile(
        r"""<<-?\s*(?P<quote>['"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*) (?P=quote)""".replace(
            " ", ""
        ),
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
        "clear-content",
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
        "tee-object": ("PowerShell Tee-Object can write command output to a file.", "create_file"),
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
        "py -m pytest",
        "python -m py_compile",
        "py -m py_compile",
        "python -m compileall",
        "py -m compileall",
        "uv run pytest",
        "poetry run pytest",
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
        "gci",
        "get-location",
        "gl",
        "select-string",
        "sls",
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

        if self._is_apply_patch_heredoc(command):
            return BashRiskDecision(
                risk=BashRisk.FILE_WRITE_VIA_BASH,
                reason="apply_patch through shell heredoc can write files.",
                target_paths=[],
                command_prefix=command_prefix,
                suggested_tool="edit_file",
                confidence="high",
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

    def _is_apply_patch_heredoc(self, command: str) -> bool:
        return bool(re.search(r"\bapply_patch\b[^\n\r]*<<", command, re.IGNORECASE))

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
        command = self._strip_heredoc_bodies(command)
        paths: list[str] = []
        for match in self.SHELL_REDIRECT_RE.finditer(command):
            path = self._clean_target_path(match.group("path"))
            if path and path not in {"&1", "&2"}:
                paths.append(path)
        return self._unique(paths)

    def _strip_heredoc_bodies(self, command: str) -> str:
        lines = command.splitlines()
        if not lines:
            return command

        kept: list[str] = []
        active_tag: str | None = None

        for line in lines:
            if active_tag is not None:
                if line.strip() == active_tag:
                    active_tag = None
                continue

            kept.append(line)
            match = self.HEREDOC_START_RE.search(line)
            if match:
                active_tag = match.group("tag")

        return "\n".join(kept)

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

        if lowered.startswith(("python -c", "python3 -c", "py -c")):
            return "python -c"

        if lowered.startswith(("python -m", "python3 -m", "py -m")):
            parts = stripped.split()
            if len(parts) >= 3:
                return f"{parts[0]} -m {parts[2]}".lower()
            return "python -m"

        if lowered.startswith(("python ", "python3 ", "py ")):
            return "python script"

        if lowered.startswith("powershell"):
            return "powershell"

        if lowered.startswith("pwsh"):
            return "pwsh"

        if lowered.startswith("cmd /c"):
            return "cmd /c"

        known_prefixes = sorted(
            self.SAFE_CHECK_PREFIXES + self.READ_ONLY_PREFIXES,
            key=len,
            reverse=True,
        )
        for prefix in known_prefixes:
            if lowered == prefix or lowered.startswith(f"{prefix} "):
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
        operation = self._classify_operation(tool, args, context)
        self._log_operation_classified(tool, args, operation, context)

        for path in operation.paths:
            try:
                context.safe_path(path)
            except Exception:
                return self._decision(
                    behavior=PermissionBehavior.DENY,
                    risk="path_escape",
                    message=f"Permission denied: path escapes WORKDIR: {path}",
                    operation=operation,
                    terminal_on_deny=operation.terminal_on_deny,
                    decision_reason="path_escape",
                )

        if operation.scope_key and operation.scope_key in context.denied_permission_scopes:
            return self._decision(
                behavior=PermissionBehavior.DENY,
                risk="previously_denied_scope",
                message=f"User already denied this operation: {operation.scope_key}",
                operation=operation,
                terminal_on_deny=operation.terminal_on_deny,
                decision_reason="previously_denied_scope",
            )

        policy_decision = self._check_access_policy(operation, context)
        if policy_decision is not None:
            return policy_decision

        deny_rule = context.permission_rules.match("deny", tool.name, operation.scope_key)
        if deny_rule:
            return self._decision(
                behavior=PermissionBehavior.DENY,
                risk="permission_rule",
                message=f"Denied by permission rule: {operation.scope_key or tool.name}",
                operation=operation,
                terminal_on_deny=operation.terminal_on_deny,
                decision_reason="deny_rule",
            )

        ask_rule = context.permission_rules.match("ask", tool.name, operation.scope_key)
        if ask_rule:
            return self._decision(
                behavior=PermissionBehavior.ASK,
                risk="permission_rule",
                message=f"Permission rule requires approval: {operation.scope_key or tool.name}",
                operation=operation,
                terminal_on_deny=operation.terminal_on_deny,
                decision_reason="ask_rule",
            )

        bash_decision = self._check_bash_operation(operation, context)
        if bash_decision is not None:
            return bash_decision

        tool_decision = tool.check_permissions(args, context, operation)
        if tool_decision is not None and tool_decision.behavior in {
            PermissionBehavior.DENY,
            PermissionBehavior.ASK,
        }:
            return self._with_operation(tool_decision, operation)

        if context.permission_mode == PermissionMode.ACCEPT_EDITS:
            if operation.is_read_only or operation.kind == "fs.read":
                return self._decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk="read_only_operation",
                    message="Allowed read-only operation.",
                    operation=operation,
                    decision_reason="accept_edits_read",
                )
            if operation.kind == "fs.write" and not operation.is_sensitive:
                return self._decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk="file_write",
                    message=f"Allowed file write for {operation.subject}.",
                    operation=operation,
                    decision_reason="accept_edits_write",
                )

        if context.permission_mode == PermissionMode.READ_ONLY:
            if operation.kind == "fs.write":
                return self._decision(
                    behavior=PermissionBehavior.ASK,
                    risk="write_tool_in_read_only",
                    message=(
                        f"Model requested write operation {operation.scope_key} "
                        "while permission mode is read_only."
                    ),
                    operation=operation,
                    terminal_on_deny=True,
                    decision_reason="read_only_escalation",
                )
            if operation.is_read_only or operation.kind == "fs.read":
                return self._decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk="read_only_operation",
                    message="Allowed read-only operation.",
                    operation=operation,
                    decision_reason="read_only_allow",
                )

        if context.permission_mode == PermissionMode.MANUAL_APPROVAL:
            if operation.kind in {"fs.write", "process.exec"}:
                return self._decision(
                    behavior=PermissionBehavior.ASK,
                    risk="manual_approval",
                    message=f"Model requested {operation.scope_key or operation.subject}.",
                    operation=operation,
                    terminal_on_deny=operation.terminal_on_deny,
                    decision_reason="manual_approval",
                )
            if operation.is_read_only or operation.kind == "fs.read":
                return self._decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk="read_only_operation",
                    message="Allowed read-only operation.",
                    operation=operation,
                    decision_reason="manual_read",
                )

        allow_rule = context.permission_rules.match("allow", tool.name, operation.scope_key)
        if allow_rule:
            return self._decision(
                behavior=PermissionBehavior.ALLOW,
                risk="permission_rule",
                message=f"Allowed by permission rule: {operation.scope_key or tool.name}",
                operation=operation,
                decision_reason="allow_rule",
            )

        if operation.is_read_only or operation.kind == "fs.read":
            return self._decision(
                behavior=PermissionBehavior.ALLOW,
                risk="read_only_operation",
                message="Allowed read-only operation.",
                operation=operation,
                decision_reason="fallback_read",
            )

        return self._decision(
            behavior=PermissionBehavior.ASK,
            risk="unknown_operation",
            message=f"Model requested {operation.scope_key or operation.subject}; approval required.",
            operation=operation,
            terminal_on_deny=operation.terminal_on_deny,
            decision_reason="fallback_ask",
        )

    def resolve(self, decision: PermissionDecision, tool, args: dict, context) -> PermissionDecision:
        if decision.behavior != PermissionBehavior.ASK:
            return decision

        scope = self._decision_scope(decision)
        allow_rule = context.permission_rules.match("allow", tool.name, scope)
        if scope and (scope in context.approved_permission_scopes or allow_rule):
            return self._decision(
                behavior=PermissionBehavior.ALLOW,
                risk=decision.risk,
                message=f"Allowed by prior approval for scope: {scope}",
                operation=decision.operation,
                proposed_scope=scope,
                metadata=decision.metadata,
                terminal_on_deny=decision.terminal_on_deny,
                decision_reason="approved_scope",
            )

        return self._ask_user(decision, tool, args, context)

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

    def _bash_approval_scope(self, risk: str, bash_decision: BashRiskDecision) -> str:
        prefix = bash_decision.command_prefix or "unknown"
        prefix = re.sub(r"[^a-zA-Z0-9_.-]+", "_", prefix).strip("_") or "unknown"
        return f"bash:{risk}:{prefix}"

    def _ask_user(self, decision: PermissionDecision, tool, args: dict, context) -> PermissionDecision:
        print("\n[permission request]")
        print(f"Tool: {tool.name}")
        print(f"Risk: {decision.risk}")
        print(f"Reason: {decision.message}")
        self._print_decision_metadata(decision)
        print(f"Args: {args}")
        scope = self._decision_scope(decision)
        if scope:
            print("Allow? [y] once / [a] this run scope / [N] deny")
        else:
            print("Allow? [y] once / [N] deny")

        try:
            answer = input("permission> ").strip().lower()
        except EOFError:
            answer = ""

        if answer in {"y", "yes", "once"}:
            self._log_user_response(context, tool, decision, "allow_once")
            return self._decision(
                behavior=PermissionBehavior.ALLOW,
                risk=decision.risk,
                message="Allowed once by user approval.",
                proposed_scope=scope,
                operation=decision.operation,
                metadata=decision.metadata,
                terminal_on_deny=decision.terminal_on_deny,
                decision_reason="user_allow_once",
            )

        if answer in {"a", "all", "run"} and scope:
            context.approved_permission_scopes.add(scope)
            context.permission_rules.add(
                PermissionRule(
                    source="session",
                    behavior="allow",
                    value=PermissionRuleValue(tool_name=tool.name, operation_scope=scope),
                )
            )
            self._log_user_response(context, tool, decision, "allow_scope")
            self._log_scope_event(context, tool, decision, "permission_scope_approved", scope)
            return self._decision(
                behavior=PermissionBehavior.ALLOW,
                risk=decision.risk,
                message=f"Allowed for this run scope: {scope}",
                proposed_scope=scope,
                operation=decision.operation,
                metadata=decision.metadata,
                terminal_on_deny=decision.terminal_on_deny,
                decision_reason="user_allow_scope",
            )

        if decision.terminal_on_deny and scope:
            context.denied_permission_scopes.add(scope)
            context.permission_rules.add(
                PermissionRule(
                    source="session",
                    behavior="deny",
                    value=PermissionRuleValue(tool_name=tool.name, operation_scope=scope),
                )
            )
            self._log_scope_event(context, tool, decision, "permission_scope_denied", scope)

        self._log_user_response(context, tool, decision, "deny")
        return self._decision(
            behavior=PermissionBehavior.DENY,
            risk=decision.risk,
            message="Permission denied by user approval policy.",
            proposed_scope=scope,
            operation=decision.operation,
            metadata=decision.metadata,
            terminal_on_deny=decision.terminal_on_deny,
            decision_reason="user_deny",
        )

    def _print_decision_metadata(self, decision: PermissionDecision) -> None:
        bash_risk = decision.metadata.get("bash_risk")
        operation = decision.operation

        if operation is not None:
            print(f"Operation: {operation.scope_key or operation.subject}")
            if operation.paths:
                print(f"Paths: {operation.paths}")

        if not isinstance(bash_risk, dict):
            return
        if bash_risk.get("target_paths"):
            print(f"Target paths: {bash_risk['target_paths']}")
        if bash_risk.get("suggested_tool"):
            print(f"Suggested tool: {bash_risk['suggested_tool']}")
        if bash_risk.get("confidence"):
            print(f"Confidence: {bash_risk['confidence']}")

    def _classify_operation(self, tool, args: dict, context) -> Operation:
        operation = tool.classify_operation(args, context)
        if operation.kind == "process.exec" and operation.action == "bash":
            return self._classify_bash_operation(operation)
        return operation

    def _classify_bash_operation(self, operation: Operation) -> Operation:
        command = operation.command or ""
        bash_decision = self.risk_classifier.classify_bash(command)
        risk = bash_decision.risk
        metadata = {"bash_risk": bash_decision.to_metadata()}

        if risk == BashRisk.FILE_WRITE_VIA_BASH:
            paths = bash_decision.target_paths
            if paths:
                subject = ", ".join(paths)
                scope = f"write:bash:{paths[0]}" if len(paths) == 1 else self._bash_approval_scope(risk, bash_decision)
            else:
                subject = operation.subject
                scope = self._bash_approval_scope(risk, bash_decision)
            return Operation(
                kind="fs.write",
                action="bash",
                subject=subject,
                paths=paths,
                command=command,
                scope_key=scope,
                terminal_on_deny=True,
                is_sensitive=True,
                metadata=metadata,
            )

        is_read_only = risk in {BashRisk.SAFE_CHECK, BashRisk.READ_ONLY_COMMAND}
        is_destructive = risk == BashRisk.DESTRUCTIVE
        return Operation(
            kind="process.exec",
            action=risk,
            subject=bash_decision.command_prefix or operation.subject,
            paths=bash_decision.target_paths,
            command=command,
            scope_key=self._bash_approval_scope(risk, bash_decision),
            terminal_on_deny=is_destructive,
            is_read_only=is_read_only,
            is_destructive=is_destructive,
            is_sensitive=risk in {BashRisk.NETWORK, BashRisk.UNKNOWN, BashRisk.DESTRUCTIVE},
            metadata=metadata,
        )

    def _check_access_policy(self, operation: Operation, context) -> PermissionDecision | None:
        if operation.kind == "fs.read":
            for path in operation.paths:
                if context.access_policy.is_protected_read(path):
                    return self._decision(
                        behavior=PermissionBehavior.DENY,
                        risk="protected_read",
                        message=f"Permission denied: protected read path: {path}",
                        operation=operation,
                        decision_reason="access_policy_read",
                    )

        if operation.kind == "fs.write":
            for path in operation.paths:
                if context.access_policy.is_protected_write(path):
                    return self._decision(
                        behavior=PermissionBehavior.DENY,
                        risk="protected_write",
                        message=f"Permission denied: protected write path: {path}",
                        operation=operation,
                        terminal_on_deny=True,
                        decision_reason="access_policy_write",
                    )

        return None

    def _check_bash_operation(self, operation: Operation, context) -> PermissionDecision | None:
        bash_risk = operation.metadata.get("bash_risk")
        if not isinstance(bash_risk, dict):
            return None

        risk = str(bash_risk.get("risk"))
        if risk == BashRisk.DESTRUCTIVE:
            return self._decision(
                behavior=PermissionBehavior.DENY,
                risk=risk,
                message=(
                    "Permission denied: this operation intent is cancelled. "
                    f"Reason: {bash_risk.get('reason')} "
                    "Do not retry with alternative destructive commands."
                ),
                operation=operation,
                terminal_on_deny=True,
                decision_reason="bash_destructive",
            )

        if risk == BashRisk.FILE_WRITE_VIA_BASH:
            return self._decision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message=self._bash_file_write_message_from_metadata(bash_risk, context),
                operation=operation,
                terminal_on_deny=True,
                decision_reason="bash_file_write",
            )

        if risk == BashRisk.NETWORK:
            return self._decision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message=str(bash_risk.get("reason") or "Network command requires approval."),
                operation=operation,
                decision_reason="bash_network",
            )

        if risk == BashRisk.UNKNOWN:
            sandbox = getattr(context, "sandbox", None)
            if (
                context.permission_mode == PermissionMode.ACCEPT_EDITS
                and sandbox is not None
                and sandbox.can_auto_allow_unknown_bash()
            ):
                context.sandbox_auto_allowed_unknown_bash_count = (
                    getattr(context, "sandbox_auto_allowed_unknown_bash_count", 0) + 1
                )
                return self._decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk=risk,
                    message="Allowed unknown bash command because sandbox is enabled and available.",
                    operation=operation,
                    metadata={"sandbox_auto_allowed": True},
                    decision_reason="sandbox_auto_allow",
                )
            return self._decision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message="Model requested an unknown shell command; approval required.",
                operation=operation,
                decision_reason="bash_unknown",
            )

        return None

    def _bash_file_write_message_from_metadata(self, bash_risk: dict, context) -> str:
        parts = [str(bash_risk.get("reason") or "Shell command writes files.")]
        target_paths = bash_risk.get("target_paths") or []
        if target_paths:
            parts.append(f"Target paths: {', '.join(target_paths)}.")
        suggested_tool = bash_risk.get("suggested_tool")
        if suggested_tool:
            parts.append(f"Use {suggested_tool} instead of Bash for this file operation.")
        else:
            parts.append("Prefer create_file for new files or edit_file for precise edits.")
        return " ".join(parts)

    def _decision(
        self,
        behavior: str,
        risk: str,
        message: str,
        operation: Operation | None = None,
        proposed_scope: str | None = None,
        metadata: dict[str, Any] | None = None,
        terminal_on_deny: bool | None = None,
        decision_reason: str | None = None,
    ) -> PermissionDecision:
        enriched_metadata = dict(metadata or {})
        if operation is not None:
            enriched_metadata.setdefault("operation", operation.to_metadata())
        scope = proposed_scope or (operation.scope_key if operation else None)
        if terminal_on_deny is None:
            terminal = bool(operation.terminal_on_deny) if operation else False
        else:
            terminal = bool(terminal_on_deny)
        return PermissionDecision(
            behavior=behavior,
            risk=risk,
            message=message,
            proposed_scope=scope,
            metadata=enriched_metadata,
            operation=operation,
            terminal_on_deny=terminal,
            decision_reason=decision_reason,
        )

    def _with_operation(self, decision: PermissionDecision, operation: Operation) -> PermissionDecision:
        if decision.operation is not None and "operation" in decision.metadata:
            return decision
        metadata = dict(decision.metadata)
        metadata.setdefault("operation", operation.to_metadata())
        return PermissionDecision(
            behavior=decision.behavior,
            risk=decision.risk,
            message=decision.message,
            proposed_scope=decision.proposed_scope or operation.scope_key,
            metadata=metadata,
            operation=decision.operation or operation,
            terminal_on_deny=decision.terminal_on_deny or operation.terminal_on_deny,
            decision_reason=decision.decision_reason,
        )

    def _decision_scope(self, decision: PermissionDecision) -> str | None:
        if decision.proposed_scope:
            return decision.proposed_scope
        if decision.operation is not None:
            return decision.operation.scope_key
        return None

    def _log_operation_classified(self, tool, args: dict, operation: Operation, context) -> None:
        trace = getattr(context, "trace", None)
        if trace is None:
            return
        trace.log(
            {
                "type": "operation_classified",
                "tool": tool.name,
                "args_preview": str(args)[:500],
                "operation": operation.to_metadata(),
            }
        )

    def _log_user_response(self, context, tool, decision: PermissionDecision, response: str) -> None:
        trace = getattr(context, "trace", None)
        if trace is None:
            return
        trace.log(
            {
                "type": "permission_user_response",
                "tool": tool.name,
                "response": response,
                "operation": decision.operation.to_metadata() if decision.operation else None,
                "decision": self._decision_metadata(decision),
            }
        )

    def _log_scope_event(
        self,
        context,
        tool,
        decision: PermissionDecision,
        event_type: str,
        scope: str,
    ) -> None:
        trace = getattr(context, "trace", None)
        if trace is None:
            return
        trace.log(
            {
                "type": event_type,
                "tool": tool.name,
                "scope": scope,
                "operation": decision.operation.to_metadata() if decision.operation else None,
                "decision": self._decision_metadata(decision),
            }
        )

    def _decision_metadata(self, decision: PermissionDecision) -> dict:
        return {
            "behavior": decision.behavior,
            "risk": decision.risk,
            "message": decision.message,
            "proposed_scope": decision.proposed_scope,
            "terminal_on_deny": decision.terminal_on_deny,
            "decision_reason": decision.decision_reason,
        }
