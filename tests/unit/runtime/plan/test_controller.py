from __future__ import annotations

import pytest

from runtime.config import RunConfig
from runtime.plan import (
    ExecutionPath,
    PlanController,
    PlanPhase,
    PlanPolicy,
    PlanState,
    PlanStepStatus,
    PlanTransitionError,
    PlanValidationError,
)


def make_controller(policy: PlanPolicy = PlanPolicy.REQUIRED) -> PlanController:
    return PlanController(PlanState.initial(policy, "implement plan mode"))


@pytest.mark.parametrize(
    ("policy", "execution_path", "phase"),
    [
        (PlanPolicy.OFF, ExecutionPath.DIRECT, PlanPhase.INACTIVE),
        (PlanPolicy.AUTO, ExecutionPath.UNDECIDED, PlanPhase.INACTIVE),
        (PlanPolicy.REQUIRED, ExecutionPath.PLAN, PlanPhase.PLANNING),
    ],
)
def test_initial_state_matches_policy(policy, execution_path, phase) -> None:
    state = PlanState.initial(policy, "task")

    assert state.execution_path is execution_path
    assert state.phase is phase


def test_run_config_normalizes_and_rejects_plan_policy() -> None:
    assert RunConfig(plan_policy="auto").plan_policy is PlanPolicy.AUTO

    with pytest.raises(ValueError, match="plan_policy"):
        RunConfig(plan_policy="sometimes")


def test_auto_model_can_select_direct_only_once() -> None:
    controller = make_controller(PlanPolicy.AUTO)

    controller.select_execution_path(
        "direct",
        reason="one local low-risk change",
        has_mutations=False,
    )

    assert controller.state.execution_path is ExecutionPath.DIRECT
    assert controller.state.phase is PlanPhase.INACTIVE
    assert controller.state.steps == []
    with pytest.raises(PlanTransitionError, match="already"):
        controller.select_execution_path(
            "plan",
            reason="changed my mind",
            has_mutations=False,
        )


def test_auto_model_can_select_plan_and_auto_authorize_submission() -> None:
    controller = make_controller(PlanPolicy.AUTO)
    controller.select_execution_path(
        "plan",
        reason="cross-module runtime work",
        has_mutations=False,
    )
    controller.replace_plan(
        [{"id": "step-1", "description": "Implement the controller"}]
    )

    controller.submit_for_execution()

    assert controller.state.phase is PlanPhase.EXECUTING
    assert controller.state.approved_version == controller.state.version
    assert controller.state.approval_source == "auto_policy"


def test_required_plan_waits_for_user_approval() -> None:
    controller = make_controller()
    controller.replace_plan(
        [{"id": "step-1", "description": "Implement the controller"}]
    )

    controller.submit_for_execution()

    assert controller.state.phase is PlanPhase.AWAITING_APPROVAL
    assert controller.state.approved_version is None
    controller.approve()
    assert controller.state.phase is PlanPhase.EXECUTING
    assert controller.state.approved_version == controller.state.version
    assert controller.state.approval_source == "user"


def test_empty_or_duplicate_plan_is_rejected() -> None:
    controller = make_controller()

    with pytest.raises(PlanValidationError, match="at least one"):
        controller.replace_plan([])
    with pytest.raises(PlanValidationError, match="unique"):
        controller.replace_plan(
            [
                {"id": "same", "description": "first"},
                {"id": "same", "description": "second"},
            ]
        )


def test_plan_change_invalidates_old_approval() -> None:
    controller = make_controller()
    controller.replace_plan([{"id": "step-1", "description": "First version"}])
    controller.submit_for_execution()
    controller.approve()
    approved_version = controller.state.approved_version

    controller.request_replan("the API differs from the initial assumption")

    assert controller.state.phase is PlanPhase.PLANNING
    assert controller.state.approved_version is None
    assert controller.state.version > approved_version


def test_revision_returns_to_planning_and_requires_new_approval() -> None:
    controller = make_controller()
    controller.replace_plan([{"id": "step-1", "description": "Initial plan"}])
    controller.submit_for_execution()

    controller.revise("add a compatibility test")

    assert controller.state.phase is PlanPhase.PLANNING
    assert controller.state.approved_version is None
    assert controller.state.revision_feedback == "add a compatibility test"


def test_step_status_is_ordered_and_only_one_step_is_active() -> None:
    controller = make_controller()
    controller.replace_plan(
        [
            {"id": "step-1", "description": "First"},
            {"id": "step-2", "description": "Second"},
        ]
    )
    controller.submit_for_execution()
    controller.approve()

    controller.update_step("step-1", PlanStepStatus.IN_PROGRESS)
    with pytest.raises(PlanTransitionError, match="current step"):
        controller.update_step("step-2", PlanStepStatus.IN_PROGRESS)
    controller.update_step("step-1", PlanStepStatus.COMPLETED)
    controller.update_step("step-2", PlanStepStatus.COMPLETED)
    controller.complete()

    assert controller.state.phase is PlanPhase.COMPLETED


def test_unfinished_plan_cannot_complete() -> None:
    controller = make_controller()
    controller.replace_plan([{"id": "step-1", "description": "Still pending"}])
    controller.submit_for_execution()
    controller.approve()

    with pytest.raises(PlanTransitionError, match="unfinished"):
        controller.complete()


def test_execution_path_cannot_change_after_mutation() -> None:
    controller = make_controller(PlanPolicy.AUTO)

    with pytest.raises(PlanTransitionError, match="after repository mutations"):
        controller.select_execution_path(
            "plan",
            reason="late planning",
            has_mutations=True,
        )


def test_required_policy_cannot_select_direct() -> None:
    controller = make_controller(PlanPolicy.REQUIRED)

    with pytest.raises(PlanTransitionError, match="auto policy"):
        controller.select_execution_path(
            "direct",
            reason="not allowed",
            has_mutations=False,
        )
