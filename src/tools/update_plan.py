from __future__ import annotations

from runtime.operation import Operation
from runtime.plan import PlanError, PlanStepStatus
from runtime.plan.capabilities import context_plan_capabilities
from tools.base import BaseTool, ToolResult, ToolValidationError


PLAN_ACTIONS = {
    "replace_plan",
    "submit",
    "update_step",
    "request_replan",
    "cancel",
    "complete",
}


class UpdatePlanTool(BaseTool):
    name = "update_plan"
    description = (
        "Create and optionally submit a structured plan in one call, update execution-step "
        "status, or request replanning. This tool cannot approve a manual plan."
    )
    input_schema = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "action": {"const": "replace_plan"},
                    "explanation": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 100,
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string", "minLength": 1},
                                "description": {"type": "string", "minLength": 1},
                                "status": {
                                    "type": "string",
                                    "enum": [status.value for status in PlanStepStatus],
                                },
                            },
                            "required": ["id", "description"],
                            "additionalProperties": False,
                        },
                    },
                    "submit": {"type": "boolean"},
                },
                "required": ["action", "steps"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {"action": {"const": "submit"}},
                "required": ["action"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "update_step"},
                    "step_id": {"type": "string", "minLength": 1},
                    "status": {
                        "type": "string",
                        "enum": [status.value for status in PlanStepStatus],
                    },
                },
                "required": ["action", "step_id", "status"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "action": {"const": "request_replan"},
                    "reason": {"type": "string", "minLength": 1},
                },
                "required": ["action", "reason"],
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
            {
                "type": "object",
                "properties": {"action": {"const": "complete"}},
                "required": ["action"],
                "additionalProperties": False,
            },
        ]
    }

    read_only = True
    dangerous = False
    concurrency_safe = False

    def is_available(self, context) -> bool:
        if context is None:
            return False
        return bool(context_plan_capabilities(context).can_update_plan)

    def classify_operation(self, args: dict, context) -> Operation:
        return Operation(
            kind="runtime.plan",
            action=str(args.get("action", "update")),
            subject="current plan",
            scope_key="runtime:plan:update",
            is_read_only=True,
        )

    def validate(self, args: dict, context) -> None:
        if not isinstance(args, dict):
            raise ToolValidationError("update_plan arguments must be an object")
        allowed = {
            "action",
            "explanation",
            "steps",
            "ready_for_approval",
            "submit",
            "step_id",
            "status",
            "reason",
        }
        unknown = set(args) - allowed
        if unknown:
            raise ToolValidationError(f"unknown fields: {', '.join(sorted(unknown))}")
        action = args.get("action")
        if action not in PLAN_ACTIONS:
            raise ToolValidationError(f"invalid update_plan action: {action}")
        action_fields = {
            "replace_plan": {
                "action",
                "explanation",
                "steps",
                "submit",
                "ready_for_approval",
            },
            "submit": {"action"},
            "update_step": {"action", "step_id", "status"},
            "request_replan": {"action", "reason"},
            "cancel": {"action", "reason"},
            "complete": {"action"},
        }
        unexpected = set(args) - action_fields[action]
        if unexpected:
            raise ToolValidationError(
                f"fields not valid for {action}: {', '.join(sorted(unexpected))}"
            )

        if action == "replace_plan":
            steps = args.get("steps")
            if not isinstance(steps, list) or not steps:
                raise ToolValidationError("replace_plan requires a non-empty steps array")
            if len(steps) > 100:
                raise ToolValidationError("a plan may contain at most 100 steps")
            if "ready_for_approval" in args and not isinstance(
                args["ready_for_approval"], bool
            ):
                raise ToolValidationError("ready_for_approval must be a boolean")
            if "submit" in args and not isinstance(args["submit"], bool):
                raise ToolValidationError("submit must be a boolean")
            if "submit" in args and "ready_for_approval" in args:
                raise ToolValidationError(
                    "use submit; do not combine it with legacy ready_for_approval"
                )
        elif action == "update_step":
            if not isinstance(args.get("step_id"), str) or not args["step_id"].strip():
                raise ToolValidationError("update_step requires a non-empty step_id")
            if args.get("status") not in {status.value for status in PlanStepStatus}:
                raise ToolValidationError("update_step requires a valid status")
        elif action == "request_replan":
            self._validate_required_text(args.get("reason"), "request_replan requires reason")
        elif action == "cancel" and "reason" in args:
            self._validate_required_text(args.get("reason"), "cancel reason must not be empty")

    def call(self, args: dict, context) -> ToolResult:
        action = args["action"]
        controller = context.plan_controller
        try:
            if action == "replace_plan":
                controller.replace_plan(
                    args["steps"],
                    explanation=args.get("explanation"),
                )
                if args.get("submit", args.get("ready_for_approval", False)):
                    controller.submit_for_execution()
            elif action == "submit":
                controller.submit_for_execution()
            elif action == "update_step":
                controller.update_step(args["step_id"], args["status"])
            elif action == "request_replan":
                controller.request_replan(args["reason"])
            elif action == "cancel":
                controller.cancel(args.get("reason"))
            elif action == "complete":
                controller.complete()
        except PlanError as exc:
            return ToolResult(
                ok=False,
                content=f"Plan update failed: {exc}",
                error=str(exc),
                metadata={"plan_error": True, "track_mutation_failure": False},
            )

        state = controller.state
        return ToolResult(
            ok=True,
            content=(
                f"Plan action {action} recorded: phase={state.phase.value}, "
                f"version={state.version}, approved_version={state.approved_version}."
            ),
            metadata={
                "plan_action": action,
                "plan_phase": state.phase.value,
                "plan_version": state.version,
                "approved_version": state.approved_version,
                "approval_source": state.approval_source,
                "step_id": args.get("step_id"),
                "step_status": args.get("status"),
                "changed": False,
            },
        )

    def _validate_required_text(self, value, message: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ToolValidationError(message)
