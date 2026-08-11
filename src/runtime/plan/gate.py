from __future__ import annotations

from runtime.plan.capabilities import context_plan_capabilities
from runtime.plan.models import ExecutionPath, PlanApprovalPolicy, PlanPhase, PlanPolicy
from tools.base import ToolResult


class PlanGate:
    """Block side effects until the plan lifecycle authorizes execution."""

    def check(self, *, tool_call, tool, context) -> ToolResult | None:
        state = getattr(context, "plan_state", None)
        if state is None or state.policy is PlanPolicy.OFF:
            return None
        capabilities = context_plan_capabilities(context)
        if capabilities.can_execute_side_effects:
            return None

        if capabilities.tool_is_visible(tool_call.name):
            return None

        requires_selection = state.execution_path is ExecutionPath.UNDECIDED
        requires_approval = (
            state.approval_policy is PlanApprovalPolicy.MANUAL
            and state.phase in {PlanPhase.PLANNING, PlanPhase.AWAITING_APPROVAL}
        )
        pending_continuation = getattr(context, "has_pending_user_continuation", None)
        requires_resolution = bool(
            state.phase is PlanPhase.AWAITING_APPROVAL
            and callable(pending_continuation)
            and pending_continuation()
        )
        requires_finalization = bool(capabilities.planning_finalize_required)
        if requires_selection:
            message = (
                f"Plan gate blocked {tool_call.name}: call select_execution_mode before "
                "using Bash or a repository mutation tool."
            )
        elif state.phase is PlanPhase.PLANNING:
            if requires_finalization:
                message = (
                    f"Plan gate blocked {tool_call.name}: the planning budget is exhausted. "
                    "Use update_plan to submit a final plan, replace and submit it in one "
                    "call, or cancel."
                )
            else:
                message = (
                    f"Plan gate blocked {tool_call.name}: planning is read-only. "
                    "Finish the structured plan with update_plan first."
                )
        elif state.phase is PlanPhase.AWAITING_APPROVAL:
            if requires_resolution:
                message = (
                    f"Plan gate blocked {tool_call.name}: the latest user response is still "
                    "unresolved. Call resolve_plan_response with approve, revise, or cancel; "
                    "repository tools remain unavailable until that succeeds."
                )
            else:
                message = (
                    f"Plan gate blocked {tool_call.name}: the plan is waiting for user approval."
                )
        else:
            message = f"Plan gate blocked {tool_call.name} in phase {state.phase.value}."

        metadata = {
            "blocked_by": (
                "planning_convergence" if requires_finalization else "plan_gate"
            ),
            "blocked_by_hook": True,
            "plan_policy": state.policy.value,
            "execution_path": state.execution_path.value,
            "plan_phase": state.phase.value,
            "tool": tool_call.name,
            "requires_mode_selection": requires_selection,
            "requires_plan_approval": requires_approval,
            "requires_plan_response_resolution": requires_resolution,
            "requires_plan_finalization": requires_finalization,
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
