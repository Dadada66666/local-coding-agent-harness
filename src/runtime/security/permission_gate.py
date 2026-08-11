from __future__ import annotations

import re

from runtime.operation import Operation
from runtime.security.approval import PermissionApproval
from runtime.security.permission_models import (
    BashRisk,
    BashRiskDecision,
    PermissionBehavior,
    PermissionDecision,
    PermissionMode,
)
from runtime.security.risk_classifier import RiskClassifier


class PermissionGate:

    def __init__(self, risk_classifier: RiskClassifier | None = None) -> None:
        self.risk_classifier = risk_classifier or RiskClassifier()
        self.approval = PermissionApproval()


    def check(self, tool, args: dict, context) -> PermissionDecision:
        operation = self._classify_operation(tool, args, context)
        self._log_operation_classified(tool, args, operation, context)

        protocol_decision = self._check_bash_protocol(operation, args, context)
        if protocol_decision is not None:
            return protocol_decision

        for path in operation.paths:
            try:
                context.safe_path(path)
            except Exception:
                return self.approval.create_decision(
                    behavior=PermissionBehavior.DENY,
                    risk="path_escape",
                    message=f"Permission denied: path escapes WORKDIR: {path}",
                    operation=operation,
                    terminal_on_deny=(
                        operation.is_destructive or operation.kind == "fs.delete"
                    ),
                    decision_reason="path_escape",
                )

        if operation.scope_key and operation.scope_key in context.denied_permission_scopes:
            return self.approval.create_decision(
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
            return self.approval.create_decision(
                behavior=PermissionBehavior.DENY,
                risk="permission_rule",
                message=f"Denied by permission rule: {operation.scope_key or tool.name}",
                operation=operation,
                terminal_on_deny=operation.terminal_on_deny,
                decision_reason="deny_rule",
            )

        ask_rule = context.permission_rules.match("ask", tool.name, operation.scope_key)
        if ask_rule:
            return self.approval.create_decision(
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
                return self.approval.create_decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk="read_only_operation",
                    message="Allowed read-only operation.",
                    operation=operation,
                    decision_reason="accept_edits_read",
                )
            if operation.kind == "fs.delete":
                if self._all_paths_created_in_current_task(operation, context):
                    return self.approval.create_decision(
                        behavior=PermissionBehavior.ALLOW,
                        risk="task_created_file_cleanup",
                        message=f"Allowed cleanup of current-task file {operation.subject}.",
                        operation=operation,
                        decision_reason="accept_edits_owned_cleanup",
                    )
                return self.approval.create_decision(
                    behavior=PermissionBehavior.ASK,
                    risk="preexisting_file_delete",
                    message=f"Model requested deletion of existing file {operation.subject}.",
                    operation=operation,
                    decision_reason="accept_edits_delete_approval",
                )
            if operation.kind == "fs.write" and not operation.is_sensitive:
                return self.approval.create_decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk="file_write",
                    message=f"Allowed file write for {operation.subject}.",
                    operation=operation,
                    decision_reason="accept_edits_write",
                )

        if context.permission_mode == PermissionMode.READ_ONLY:
            if operation.kind in {"fs.write", "fs.delete"}:
                return self.approval.create_decision(
                    behavior=PermissionBehavior.ASK,
                    risk=(
                        "delete_tool_in_read_only"
                        if operation.kind == "fs.delete"
                        else "write_tool_in_read_only"
                    ),
                    message=(
                        f"Model requested mutation {operation.scope_key} "
                        "while permission mode is read_only."
                    ),
                    operation=operation,
                    terminal_on_deny=True,
                    decision_reason="read_only_escalation",
                )
            if operation.is_read_only or operation.kind == "fs.read":
                return self.approval.create_decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk="read_only_operation",
                    message="Allowed read-only operation.",
                    operation=operation,
                    decision_reason="read_only_allow",
                )

        if context.permission_mode == PermissionMode.MANUAL_APPROVAL:
            if operation.kind in {"fs.write", "fs.delete", "process.exec"}:
                return self.approval.create_decision(
                    behavior=PermissionBehavior.ASK,
                    risk="manual_approval",
                    message=f"Model requested {operation.scope_key or operation.subject}.",
                    operation=operation,
                    terminal_on_deny=operation.terminal_on_deny,
                    decision_reason="manual_approval",
                )
            if operation.is_read_only or operation.kind == "fs.read":
                return self.approval.create_decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk="read_only_operation",
                    message="Allowed read-only operation.",
                    operation=operation,
                    decision_reason="manual_read",
                )

        allow_rule = context.permission_rules.match("allow", tool.name, operation.scope_key)
        if allow_rule:
            return self.approval.create_decision(
                behavior=PermissionBehavior.ALLOW,
                risk="permission_rule",
                message=f"Allowed by permission rule: {operation.scope_key or tool.name}",
                operation=operation,
                decision_reason="allow_rule",
            )

        if operation.is_read_only or operation.kind == "fs.read":
            return self.approval.create_decision(
                behavior=PermissionBehavior.ALLOW,
                risk="read_only_operation",
                message="Allowed read-only operation.",
                operation=operation,
                decision_reason="fallback_read",
            )

        return self.approval.create_decision(
            behavior=PermissionBehavior.ASK,
            risk="unknown_operation",
            message=f"Model requested {operation.scope_key or operation.subject}; approval required.",
            operation=operation,
            terminal_on_deny=operation.terminal_on_deny,
            decision_reason="fallback_ask",
        )


    def _all_paths_created_in_current_task(self, operation: Operation, context) -> bool:
        if not operation.paths:
            return False

        task_created_files = getattr(context, "task_created_files", set())
        for path in operation.paths:
            target = context.safe_path(path)
            relative_path = str(target.relative_to(context.repo_path))
            if relative_path not in task_created_files:
                return False
        return True


    def resolve(
        self,
        decision: PermissionDecision,
        tool,
        args: dict,
        context,
    ) -> PermissionDecision:
        return self.approval.resolve(decision, tool, args, context)


    def _bash_approval_scope(self, risk: str, bash_decision: BashRiskDecision) -> str:
        parts = [risk, bash_decision.command_prefix or "unknown"]
        if risk == BashRisk.NETWORK:
            parts.append(bash_decision.network_hosts[0] if bash_decision.network_hosts else "any-host")
        if bash_decision.target_paths:
            parts.append(bash_decision.target_paths[0])
        elif "file_write" in bash_decision.effects:
            parts.append("write-any-path")
        normalized = [
            re.sub(r"[^a-zA-Z0-9_.-]+", "_", part).strip("_") or "unknown"
            for part in parts
        ]
        return "bash:" + ":".join(normalized)


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
                terminal_on_deny=False,
                is_sensitive=True,
                metadata=metadata,
            )

        if risk == BashRisk.FILE_DELETE_VIA_BASH:
            return Operation(
                kind="fs.delete",
                action="bash",
                subject=bash_decision.command_prefix or operation.subject,
                paths=bash_decision.target_paths,
                command=command,
                scope_key=self._bash_approval_scope(risk, bash_decision),
                terminal_on_deny=False,
                is_sensitive=True,
                metadata=metadata,
            )

        is_read_only = risk == BashRisk.READ_ONLY_COMMAND
        is_destructive = risk == BashRisk.DESTRUCTIVE
        has_file_mutation = "file_write" in bash_decision.effects
        return Operation(
            kind="fs.write" if has_file_mutation else "process.exec",
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
        if operation.command and operation.kind in {"fs.write", "fs.delete"}:
            protected_references = context.access_policy.protected_write_references(
                operation.command
            )
            if protected_references:
                return self.approval.create_decision(
                    behavior=PermissionBehavior.DENY,
                    risk=(
                        "protected_delete"
                        if operation.kind == "fs.delete"
                        else "protected_write"
                    ),
                    message=(
                        "Permission denied: Bash command references protected mutation path(s): "
                        f"{', '.join(protected_references)}"
                    ),
                    operation=operation,
                    terminal_on_deny=True,
                    decision_reason="access_policy_bash_write",
                )

        if operation.command:
            protected_references = context.access_policy.protected_read_references(
                operation.command
            )
            if protected_references:
                return self.approval.create_decision(
                    behavior=PermissionBehavior.DENY,
                    risk="protected_read",
                    message=(
                        "Permission denied: Bash command references protected read path(s): "
                        f"{', '.join(protected_references)}"
                    ),
                    operation=operation,
                    terminal_on_deny=False,
                    decision_reason="access_policy_bash_read",
                )

        if operation.kind == "fs.read":
            for path in operation.paths:
                target = context.safe_path(path)
                if context.access_policy.is_protected_resolved_read(context.repo_path, target):
                    return self.approval.create_decision(
                        behavior=PermissionBehavior.DENY,
                        risk="protected_read",
                        message=f"Permission denied: protected read path: {path}",
                        operation=operation,
                        decision_reason="access_policy_read",
                    )

        if operation.kind in {"fs.write", "fs.delete"}:
            for path in operation.paths:
                target = context.safe_path(path)
                if context.access_policy.is_protected_resolved_write(context.repo_path, target):
                    return self.approval.create_decision(
                        behavior=PermissionBehavior.DENY,
                        risk="protected_delete" if operation.kind == "fs.delete" else "protected_write",
                        message=f"Permission denied: protected mutation path: {path}",
                        operation=operation,
                        terminal_on_deny=True,
                        decision_reason=(
                            "access_policy_delete"
                            if operation.kind == "fs.delete"
                            else "access_policy_write"
                        ),
                    )

        return None


    def _check_bash_protocol(
        self,
        operation: Operation,
        args: dict,
        context,
    ) -> PermissionDecision | None:
        bash_risk = operation.metadata.get("bash_risk")
        if not isinstance(bash_risk, dict):
            return None

        risk = str(bash_risk.get("risk"))
        purpose = str(args.get("purpose", "")).strip().lower()
        effects = set(bash_risk.get("effects") or [])

        if purpose == "verify" and (
            risk in {BashRisk.FILE_WRITE_VIA_BASH, BashRisk.FILE_DELETE_VIA_BASH}
            or "file_write" in effects
        ):
            return self.approval.create_decision(
                behavior=PermissionBehavior.DENY,
                risk=risk,
                message=(
                    "A verification command cannot also perform explicit file mutations."
                    " Run verification directly in a separate Bash call without creating "
                    "a temporary file."
                ),
                operation=operation,
                terminal_on_deny=False,
                decision_reason="bash_mixed_mutation_verification",
                metadata={"track_mutation_failure": False},
            )

        if bash_risk.get("execution_route") == "structured_tool":
            suggested_tool = self._suggested_file_tool_from_metadata(bash_risk, context)
            tool_hint = suggested_tool or "the matching structured file tool"
            return self.approval.create_decision(
                behavior=PermissionBehavior.DENY,
                risk=risk,
                message=(
                    "Shell-based patch application is not executed. "
                    f"Use {tool_hint} so the runtime can validate and track the edit."
                ),
                operation=operation,
                terminal_on_deny=False,
                decision_reason="bash_structured_tool_route",
            )

        return None


    def _check_bash_operation(self, operation: Operation, context) -> PermissionDecision | None:
        bash_risk = operation.metadata.get("bash_risk")
        if not isinstance(bash_risk, dict):
            return None

        risk = str(bash_risk.get("risk"))
        if risk == BashRisk.DESTRUCTIVE:
            return self.approval.create_decision(
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

        if risk == BashRisk.FILE_DELETE_VIA_BASH:
            return self.approval.create_decision(
                behavior=PermissionBehavior.DENY,
                risk=risk,
                message=(
                    "Shell-based file deletion is not executed. "
                    "Use delete_file for one reviewed file and split cleanup from verification."
                ),
                operation=operation,
                terminal_on_deny=False,
                decision_reason="bash_file_delete_route",
            )

        if risk == BashRisk.FILE_WRITE_VIA_BASH:
            return self.approval.create_decision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message=self._bash_file_write_message_from_metadata(bash_risk, context),
                operation=operation,
                terminal_on_deny=False,
                decision_reason="bash_file_write",
            )

        if (
            risk == BashRisk.SAFE_CHECK
            and context.permission_mode == PermissionMode.ACCEPT_EDITS
        ):
            return self.approval.create_decision(
                behavior=PermissionBehavior.ALLOW,
                risk=risk,
                message="Allowed verification command in accept_edits mode.",
                operation=operation,
                decision_reason="accept_edits_safe_check",
            )

        if risk == BashRisk.NETWORK:
            details = [str(bash_risk.get("reason") or "Network command requires approval.")]
            hosts = bash_risk.get("network_hosts") or []
            paths = bash_risk.get("target_paths") or []
            if hosts:
                details.append(f"Hosts: {', '.join(hosts)}.")
            if paths:
                details.append(f"Filesystem mutations: {', '.join(paths)}.")
            elif "file_write" in set(bash_risk.get("effects") or []):
                details.append("Filesystem mutations: target path was not statically resolved.")
            return self.approval.create_decision(
                behavior=PermissionBehavior.ASK,
                risk=risk,
                message=" ".join(details),
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
                return self.approval.create_decision(
                    behavior=PermissionBehavior.ALLOW,
                    risk=risk,
                    message="Allowed unknown bash command because sandbox is enabled and available.",
                    operation=operation,
                    metadata={"sandbox_auto_allowed": True},
                    decision_reason="sandbox_auto_allow",
                )
            return self.approval.create_decision(
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
        suggested_tool = self._suggested_file_tool_from_metadata(bash_risk, context)
        if suggested_tool:
            parts.append(f"Use {suggested_tool} instead of Bash for this file operation.")
        else:
            parts.append("Prefer write_file for new files or edit_file for precise edits.")
        return " ".join(parts)


    def _suggested_file_tool_from_metadata(self, bash_risk: dict, context) -> str | None:
        target_paths = bash_risk.get("target_paths") or []
        if not target_paths:
            return bash_risk.get("suggested_tool")

        existing_targets = []
        for path in target_paths:
            try:
                existing_targets.append(context.safe_path(path).exists())
            except Exception:
                continue

        if existing_targets and all(existing_targets):
            return "edit_file"
        return bash_risk.get("suggested_tool")


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
