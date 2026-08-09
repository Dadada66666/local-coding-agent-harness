from __future__ import annotations

from runtime.plan.capabilities import context_plan_capabilities
from runtime.plan.models import ExecutionPath, PlanApprovalPolicy, PlanPhase, PlanPolicy
from tools.base import ToolResult


READ_ONLY_PLAN_TOOLS = {
    "list_dir",
    "grep",
    "read_file",
    "read_artifact",
    "view_diff",
}


class PlanGate:
    """Block side effects until the plan lifecycle authorizes execution."""

    def check(self, *, tool_call, tool, context) -> ToolResult | None:
        state = getattr(context, "plan_state", None)
        if state is None or state.policy is PlanPolicy.OFF:
            return None
        capabilities = context_plan_capabilities(context)
        if capabilities.can_execute_side_effects:
            return None

        allowed = set(READ_ONLY_PLAN_TOOLS)
        if capabilities.can_select_execution_mode:
            allowed.add("select_execution_mode")
        if capabilities.can_update_plan:
            allowed.add("update_plan")
        if capabilities.can_resolve_plan_response:
            allowed.add("resolve_plan_response")

        if tool_call.name in allowed:
            return None

        requires_selection = state.execution_path is ExecutionPath.UNDECIDED
        requires_approval = (
            state.approval_policy is PlanApprovalPolicy.MANUAL
            and state.phase in {PlanPhase.PLANNING, PlanPhase.AWAITING_APPROVAL}
        )
        if requires_selection:
            message = (
                f"Plan gate blocked {tool_call.name}: call select_execution_mode before "
                "using Bash or a repository mutation tool."
            )
        elif state.phase is PlanPhase.PLANNING:
            message = (
                f"Plan gate blocked {tool_call.name}: planning is read-only. "
                "Finish the structured plan with update_plan first."
            )
        elif state.phase is PlanPhase.AWAITING_APPROVAL:
            message = (
                f"Plan gate blocked {tool_call.name}: the plan is waiting for user approval."
            )
        else:
            message = f"Plan gate blocked {tool_call.name} in phase {state.phase.value}."

        metadata = {
            "blocked_by": "plan_gate",
            "blocked_by_hook": True,
            "plan_policy": state.policy.value,
            "execution_path": state.execution_path.value,
            "plan_phase": state.phase.value,
            "tool": tool_call.name,
            "requires_mode_selection": requires_selection,
            "requires_plan_approval": requires_approval,
            "track_mutation_failure": False,
        }
        context.trace.log(
            {
                "type": "plan_gate_blocked",
                "turn_id": getattr(context, "current_turn_id", None),
                "tool_call_id": getattr(tool_call, "id", None),
                **metadata,
            }
        )
        return ToolResult(ok=False, content=message, error=message, metadata=metadata)


def plan_gate_hook(tool_call, tool, context):
    gate = getattr(context, "plan_gate", None)
    if gate is None:
        return None
    return gate.check(tool_call=tool_call, tool=tool, context=context)
