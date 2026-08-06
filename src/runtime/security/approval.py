from __future__ import annotations

from typing import Any

from runtime.operation import Operation
from runtime.security.permission_models import (
    PermissionBehavior,
    PermissionDecision,
)
from runtime.security.permission_rules import PermissionRule, PermissionRuleValue


class PermissionApproval:
    def resolve(self, decision: PermissionDecision, tool, args: dict, context) -> PermissionDecision:
        if decision.behavior != PermissionBehavior.ASK:
            return decision

        scope = self._decision_scope(decision)
        allow_rule = context.permission_rules.match("allow", tool.name, scope)
        if scope and (scope in context.approved_permission_scopes or allow_rule):
            return self.create_decision(
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
            print("[permission] allowed once; executing tool.")
            return self.create_decision(
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
            print("[permission] allowed for this run scope; executing tool.")
            return self.create_decision(
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
        return self.create_decision(
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


    def create_decision(
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


    def _decision_scope(self, decision: PermissionDecision) -> str | None:
        if decision.proposed_scope:
            return decision.proposed_scope
        if decision.operation is not None:
            return decision.operation.scope_key
        return None


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
