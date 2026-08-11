from __future__ import annotations

from dataclasses import dataclass

from runtime.plan.models import ExecutionPath, PlanPhase, PlanPolicy


READ_ONLY_INSPECTION_TOOLS = frozenset(
    {"list_dir", "grep", "read_file", "read_artifact", "view_diff"}
)
PLANNING_ACTIONS = frozenset({"replace_plan", "submit", "cancel"})
EXECUTING_ACTIONS = frozenset(
    {"update_step", "request_replan", "cancel", "complete"}
)


@dataclass(frozen=True, slots=True)
class PlanCapabilities:
    can_select_execution_mode: bool
    can_update_plan: bool
    can_resolve_plan_response: bool
    can_execute_side_effects: bool
    waiting_for_user: bool
    visible_tool_names: frozenset[str] | None
    update_plan_actions: frozenset[str]
    planning_finalize_required: bool = False
    replace_plan_must_submit: bool = False

    def tool_is_visible(self, name: str) -> bool:
        return self.visible_tool_names is None or name in self.visible_tool_names


def plan_capabilities(
    state,
    *,
    has_user_continuation: bool = False,
    planning_finalize_required: bool = False,
) -> PlanCapabilities:
    if state is None or state.policy is PlanPolicy.OFF:
        return PlanCapabilities(
            False,
            False,
            False,
            True,
            False,
            None,
            frozenset(),
        )
    if state.execution_path is ExecutionPath.DIRECT:
        return PlanCapabilities(
            False,
            False,
            False,
            True,
            False,
            None,
            frozenset(),
        )

    waiting = state.phase is PlanPhase.AWAITING_APPROVAL
    can_select = (
        state.policy is PlanPolicy.AUTO
        and state.execution_path is ExecutionPath.UNDECIDED
        and state.phase is PlanPhase.INACTIVE
    )
    finalize_planning = bool(
        state.phase is PlanPhase.PLANNING and planning_finalize_required
    )
    plan_actions = (
        (
            frozenset({"replace_plan", "cancel"})
            | (frozenset({"submit"}) if state.steps else frozenset())
        )
        if finalize_planning
        else PLANNING_ACTIONS
        if state.phase is PlanPhase.PLANNING
        else EXECUTING_ACTIONS
        if state.phase is PlanPhase.EXECUTING
        else frozenset()
    )
    if can_select:
        visible_tools = READ_ONLY_INSPECTION_TOOLS | {"select_execution_mode"}
    elif finalize_planning:
        visible_tools = frozenset({"update_plan"})
    elif state.phase is PlanPhase.PLANNING:
        visible_tools = READ_ONLY_INSPECTION_TOOLS | {"update_plan"}
    elif waiting:
        visible_tools = (
            frozenset({"resolve_plan_response"})
            if has_user_continuation
            else frozenset()
        )
    elif state.phase is PlanPhase.EXECUTING:
        visible_tools = None
    else:
        visible_tools = frozenset()
    return PlanCapabilities(
        can_select_execution_mode=can_select,
        can_update_plan=bool(plan_actions),
        can_resolve_plan_response=waiting and has_user_continuation,
        can_execute_side_effects=state.phase is PlanPhase.EXECUTING,
        waiting_for_user=waiting,
        visible_tool_names=visible_tools,
        update_plan_actions=plan_actions,
        planning_finalize_required=finalize_planning,
        replace_plan_must_submit=finalize_planning,
    )


def context_plan_capabilities(context) -> PlanCapabilities:
    pending = getattr(context, "has_pending_user_continuation", None)
    has_continuation = bool(pending()) if callable(pending) else False
    progress = getattr(context, "planning_progress", None)
    return plan_capabilities(
        getattr(context, "plan_state", None),
        has_user_continuation=has_continuation,
        planning_finalize_required=bool(
            progress is not None and progress.finalize_required
        ),
    )


def context_tool_is_visible(context, tool_name: str) -> bool:
    if context is None:
        return True
    return context_plan_capabilities(context).tool_is_visible(tool_name)
