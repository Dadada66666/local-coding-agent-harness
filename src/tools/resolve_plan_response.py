from __future__ import annotations

from runtime.operation import Operation
from runtime.plan import PlanError
from runtime.plan.capabilities import context_plan_capabilities
from runtime.plan.resolution import apply_plan_response
from tools.base import BaseTool, ToolResult, ToolValidationError


class ResolvePlanResponseTool(BaseTool):
    name = "resolve_plan_response"
    description = (
        "Interpret the latest real user response to a pending plan as approval, revision, "
        "or cancellation. This tool is unavailable without fresh user input."
    )
    input_schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {"action": {"const": "approve"}},
                "required": ["action"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "revise"},
                    "feedback": {"type": "string", "minLength": 1},
                },
                "required": ["action", "feedback"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "cancel"},
                    "reason": {"type": "string", "minLength": 1},
                },
                "required": ["action"],
                "additionalProperties": False,
            },
        ]
    }

    read_only = True
    dangerous = False
    concurrency_safe = False

    def is_available(self, context) -> bool:
        return bool(
            context is not None and context_plan_capabilities(context).can_resolve_plan_response
        )

    def classify_operation(self, args: dict, context) -> Operation:
        return Operation(
            kind="runtime.plan",
            action=f"resolve_{args.get('action', 'response')}",
            subject="latest user continuation",
            scope_key="runtime:plan:resolve-user-response",
            is_read_only=True,
        )

    def validate(self, args: dict, context) -> None:
        if not isinstance(args, dict):
            raise ToolValidationError("resolve_plan_response arguments must be an object")
        if not context_plan_capabilities(context).can_resolve_plan_response:
            raise ToolValidationError(
                "a fresh user continuation is required while the plan awaits approval"
            )
        action = args.get("action")
        fields = {
            "approve": {"action"},
            "revise": {"action", "feedback"},
            "cancel": {"action", "reason"},
        }
        if action not in fields:
            raise ToolValidationError("action must be approve, revise, or cancel")
        unknown = set(args) - fields[action]
        if unknown:
            raise ToolValidationError(
                f"fields not valid for {action}: {', '.join(sorted(unknown))}"
            )
        if action == "revise":
            self._required_text(args.get("feedback"), "revise requires non-empty feedback")
        if action == "cancel" and "reason" in args:
            self._required_text(args.get("reason"), "cancel reason must not be empty")

    def call(self, args: dict, context) -> ToolResult:
        action = args["action"]
        try:
            resolution = apply_plan_response(
                context,
                action,
                feedback=args.get("feedback"),
                reason=args.get("reason") or "Cancelled by user response.",
                source="model_resolver",
                require_continuation=True,
            )
        except (PlanError, ValueError) as exc:
            return ToolResult(
                ok=False,
                content=f"Plan response resolution failed: {exc}",
                error=str(exc),
                metadata={"plan_error": True, "track_mutation_failure": False},
            )

        state = resolution.state
        return ToolResult(
            ok=True,
            content=(
                f"User plan response recorded as {action}: phase={state.phase.value}, "
                f"version={state.version}."
            ),
            metadata={
                "plan_action": f"user_{action}",
                "plan_phase": state.phase.value,
                "plan_version": state.version,
                "approved_version": state.approved_version,
                "approval_source": state.approval_source,
                "user_continuation_id": resolution.continuation_id,
                "changed": False,
                "control_plane_transition": True,
            },
        )

    def _required_text(self, value, message: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ToolValidationError(message)
