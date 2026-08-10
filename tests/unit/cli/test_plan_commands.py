from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer

from cli.app import resolve_plan_approval_policy, resolve_plan_policy
from cli.interactive import handle_interactive_command
from runtime.config import RunConfig
from runtime.plan import (
    PlanApprovalPolicy,
    PlanController,
    PlanPhase,
    PlanPolicy,
    PlanState,
)


class FakeRunner:
    def __init__(self) -> None:
        self.resume_calls = []

    def resume(self, context, message: str):
        self.resume_calls.append(message)
        context.finished = True
        return context

    def resume_runtime(self, context, message: str):
        return self.resume(context, message)


def make_context(policy: PlanPolicy = PlanPolicy.REQUIRED):
    state = PlanState.initial(policy, "current task")
    controller = PlanController(state)
    context = SimpleNamespace(
        task_id="task-1",
        task_sequence=1,
        config=RunConfig(plan_policy=policy),
        plan_state=state,
        plan_controller=controller,
        mutation_version=0,
        task_start_mutation_version=0,
        finished=True,
        success=False,
        final_text="",
        abort_reason=None,
        has_task_mutations=lambda: False,
        pending_user_continuation_id=None,
        pending_user_continuation=None,
    )
    context.has_pending_user_continuation = lambda: bool(
        context.pending_user_continuation_id is not None
        and context.pending_user_continuation
    )

    def consume_user_continuation(continuation_id=None):
        if continuation_id is not None:
            assert continuation_id == context.pending_user_continuation_id
        context.pending_user_continuation_id = None
        context.pending_user_continuation = None

    context.consume_user_continuation = consume_user_continuation
    return context


@pytest.mark.parametrize(
    ("value", "force", "disabled", "expected"),
    [
        (None, False, False, PlanPolicy.OFF),
        ("auto", False, False, PlanPolicy.AUTO),
        ("required", False, False, PlanPolicy.REQUIRED),
        ("off", False, False, PlanPolicy.OFF),
        (None, True, False, PlanPolicy.REQUIRED),
        (None, False, True, PlanPolicy.OFF),
    ],
)
def test_resolve_plan_policy(value, force, disabled, expected) -> None:
    assert resolve_plan_policy(value, force, disabled) is expected


def test_plan_policy_alias_conflicts_are_rejected() -> None:
    with pytest.raises(typer.BadParameter, match="cannot be used together"):
        resolve_plan_policy(None, True, True)
    with pytest.raises(typer.BadParameter, match="cannot be combined"):
        resolve_plan_policy("required", True, False)
    with pytest.raises(typer.BadParameter, match="must be one of"):
        resolve_plan_policy("guess", False, False)


def test_resolve_plan_approval_policy() -> None:
    assert resolve_plan_approval_policy("manual") is PlanApprovalPolicy.MANUAL
    assert resolve_plan_approval_policy("AUTO") is PlanApprovalPolicy.AUTO
    with pytest.raises(typer.BadParameter, match="plan approval"):
        resolve_plan_approval_policy("sometimes")


def test_approve_resumes_same_task_without_beginning_another() -> None:
    context = make_context()
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    context.plan_controller.submit_for_execution()
    context.pending_user_continuation_id = 7
    context.pending_user_continuation = "unresolved approval"
    runner = FakeRunner()

    handled = handle_interactive_command("/approve", runner, context)

    assert handled is True
    assert context.plan_state.phase is PlanPhase.EXECUTING
    assert context.task_sequence == 1
    assert len(runner.resume_calls) == 1
    assert context.has_pending_user_continuation() is False


def test_numeric_approval_choice_uses_same_deterministic_path() -> None:
    context = make_context()
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    context.plan_controller.submit_for_execution()
    runner = FakeRunner()

    handled = handle_interactive_command("1", runner, context)

    assert handled is True
    assert context.plan_state.phase is PlanPhase.EXECUTING
    assert len(runner.resume_calls) == 1


def test_numeric_revision_choice_prompts_for_feedback(monkeypatch) -> None:
    context = make_context()
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    context.plan_controller.submit_for_execution()
    runner = FakeRunner()
    monkeypatch.setattr(typer, "prompt", lambda _: "preserve the public API")

    handled = handle_interactive_command("2", runner, context)

    assert handled is True
    assert context.plan_state.phase is PlanPhase.PLANNING
    assert context.plan_state.revision_feedback == "preserve the public API"


def test_numeric_rejection_choice_cancels_without_model_call() -> None:
    context = make_context()
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    context.plan_controller.submit_for_execution()
    runner = FakeRunner()

    handled = handle_interactive_command("3", runner, context)

    assert handled is True
    assert context.plan_state.phase is PlanPhase.CANCELLED
    assert context.abort_reason == "plan_cancelled"
    assert runner.resume_calls == []


def test_revise_invalidates_approval_and_resumes_planning() -> None:
    context = make_context()
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    context.plan_controller.submit_for_execution()
    runner = FakeRunner()

    handle_interactive_command("/revise preserve the old API", runner, context)

    assert context.plan_state.phase is PlanPhase.PLANNING
    assert context.plan_state.approved_version is None
    assert context.plan_state.revision_feedback == "preserve the old API"
    assert len(runner.resume_calls) == 1


def test_plan_mode_command_changes_future_policy_only() -> None:
    context = make_context(PlanPolicy.OFF)
    runner = FakeRunner()

    handle_interactive_command("/plan-mode auto", runner, context)

    assert context.config.plan_policy is PlanPolicy.AUTO
    assert context.plan_state.policy is PlanPolicy.OFF


def test_plan_approval_command_changes_future_policy_only() -> None:
    context = make_context(PlanPolicy.REQUIRED)
    runner = FakeRunner()

    handle_interactive_command("/plan-approval auto", runner, context)

    assert context.config.plan_approval_policy is PlanApprovalPolicy.AUTO
    assert context.plan_state.approval_policy is PlanApprovalPolicy.MANUAL


def test_cancel_plan_does_not_call_model() -> None:
    context = make_context()
    runner = FakeRunner()

    handle_interactive_command("/cancel-plan", runner, context)

    assert context.plan_state.phase is PlanPhase.CANCELLED
    assert context.abort_reason == "plan_cancelled"
    assert runner.resume_calls == []
