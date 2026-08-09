from __future__ import annotations

from dataclasses import dataclass

from runtime.plan.models import ExecutionPath, PlanPhase, PlanPolicy


@dataclass(frozen=True, slots=True)
class PlanCapabilities:
    can_select_execution_mode: bool
    can_update_plan: bool
    can_resolve_plan_response: bool
    can_execute_side_effects: bool
    waiting_for_user: bool


def plan_capabilities(
    state,
    *,
    has_user_continuation: bool = False,
) -> PlanCapabilities:
    if state is None or state.policy is PlanPolicy.OFF:
        return PlanCapabilities(False, False, False, True, False)
    if state.execution_path is ExecutionPath.DIRECT:
        return PlanCapabilities(False, False, False, True, False)

    waiting = state.phase is PlanPhase.AWAITING_APPROVAL
    return PlanCapabilities(
        can_select_execution_mode=(
            state.policy is PlanPolicy.AUTO
            and state.execution_path is ExecutionPath.UNDECIDED
            and state.phase is PlanPhase.INACTIVE
        ),
        can_update_plan=(
            state.execution_path is ExecutionPath.PLAN
            and state.phase in {PlanPhase.PLANNING, PlanPhase.EXECUTING}
        ),
        can_resolve_plan_response=waiting and has_user_continuation,
        can_execute_side_effects=state.phase is PlanPhase.EXECUTING,
        waiting_for_user=waiting,
    )


def context_plan_capabilities(context) -> PlanCapabilities:
    pending = getattr(context, "has_pending_user_continuation", None)
    has_continuation = bool(pending()) if callable(pending) else False
    return plan_capabilities(
        getattr(context, "plan_state", None),
        has_user_continuation=has_continuation,
    )
