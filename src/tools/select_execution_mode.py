from __future__ import annotations

from runtime.operation import Operation
from runtime.plan import ExecutionPath, PlanError
from runtime.plan.capabilities import context_plan_capabilities
from tools.base import BaseTool, ToolResult, ToolValidationError


class SelectExecutionModeTool(BaseTool):
    name = "select_execution_mode"
    description = (
        "Under auto plan policy, choose direct execution or structured plan execution before "
        "using Bash or changing repository files."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["direct", "plan"]},
            "reason": {"type": "string", "minLength": 1},
        },
        "required": ["mode", "reason"],
        "additionalProperties": False,
    }

    read_only = True
    dangerous = False
    concurrency_safe = False

    def is_available(self, context) -> bool:
        if context is None:
            return False
        return bool(context_plan_capabilities(context).can_select_execution_mode)

    def classify_operation(self, args: dict, context) -> Operation:
        return Operation(
            kind="runtime.plan",
            action="select_execution_mode",
            subject=str(args.get("mode", "")),
            scope_key="runtime:plan:select",
            is_read_only=True,
        )

    def validate(self, args: dict, context) -> None:
        if not isinstance(args, dict):
            raise ToolValidationError("select_execution_mode arguments must be an object")
        unknown = set(args) - {"mode", "reason"}
        if unknown:
            raise ToolValidationError(f"unknown fields: {', '.join(sorted(unknown))}")
        if args.get("mode") not in {"direct", "plan"}:
            raise ToolValidationError("mode must be direct or plan")
        reason = args.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ToolValidationError("reason must be a non-empty string")

    def call(self, args: dict, context) -> ToolResult:
        try:
            state = context.plan_controller.select_execution_path(
                args["mode"],
                reason=args["reason"],
                has_mutations=context.has_task_mutations(),
            )
        except PlanError as exc:
            return ToolResult(
                ok=False,
                content=f"Execution mode selection failed: {exc}",
                error=str(exc),
                metadata={"plan_error": True, "track_mutation_failure": False},
            )

        return ToolResult(
            ok=True,
            content=(
                f"Execution path selected: {state.execution_path.value}. "
                + (
                    "Continue with read-only analysis and create a structured plan."
                    if state.execution_path is ExecutionPath.PLAN
                    else "Continue the task through the existing runtime controls."
                )
            ),
            metadata={
                "plan_action": "select_execution_mode",
                "execution_path": state.execution_path.value,
                "plan_phase": state.phase.value,
                "changed": False,
            },
        )
