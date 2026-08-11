from __future__ import annotations

import pytest

from agent.loop import AgentLoop
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.plan import PlanPhase, PlanPolicy
from runtime.task import TaskStatus, TaskTransitionError


def make_context(tmp_path, *, policy: PlanPolicy = PlanPolicy.REQUIRED):
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", plan_policy=policy),
    )
    return runner.start_interactive(), runner


def test_waiting_plan_is_not_archived_or_restarted(tmp_path) -> None:
    context, _ = make_context(tmp_path)
    context.begin_task("refactor the game")
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Refactor the game module"}]
    )
    context.plan_controller.submit_for_execution()

    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL
    assert context.task_status is TaskStatus.WAITING_USER
    assert context.archive_terminal_task() is False
    assert context.completed_tasks == []

    with pytest.raises(TaskTransitionError, match="still active"):
        context.begin_task("this must not replace the waiting task")


def test_terminal_task_is_archived_exactly_once(tmp_path) -> None:
    context, _ = make_context(tmp_path, policy=PlanPolicy.OFF)
    context.begin_task("answer a question")
    context.task_model_calls = 1
    context.transition_task(TaskStatus.COMPLETED, trigger="test_complete")

    assert context.archive_terminal_task() is True
    assert context.archive_terminal_task() is False
    assert [item["task_id"] for item in context.completed_tasks] == ["task-1"]
    assert context.completed_tasks[0]["status"] == "completed"


def test_user_continuation_is_auditable_and_consumed_once(tmp_path) -> None:
    context, _ = make_context(tmp_path)
    context.begin_task("refactor the game")
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Refactor the game module"}]
    )
    context.plan_controller.submit_for_execution()

    continuation_id = context.add_user_continuation("同意执行")

    assert context.task_status is TaskStatus.WAITING_USER
    assert context.has_pending_user_continuation() is True
    assert context.pending_user_continuation_id == continuation_id
    context.consume_user_continuation(continuation_id)
    assert context.has_pending_user_continuation() is False


def test_pending_user_continuation_cannot_be_silently_replaced(tmp_path) -> None:
    context, _ = make_context(tmp_path)
    context.begin_task("refactor the game")
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Refactor the game module"}]
    )
    context.plan_controller.submit_for_execution()
    continuation_id = context.add_user_continuation("review this response")

    with pytest.raises(TaskTransitionError, match="still pending"):
        context.add_user_continuation("replacement response")

    assert context.pending_user_continuation_id == continuation_id
    assert context.pending_user_continuation == "review this response"


def test_plan_and_task_waiting_state_cannot_split(tmp_path) -> None:
    context, _ = make_context(tmp_path)
    context.begin_task("refactor the game")
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Refactor the game module"}]
    )
    context.plan_controller.submit_for_execution()
    context.task_status = TaskStatus.RUNNING

    with pytest.raises(TaskTransitionError, match="requires task status waiting_user"):
        context.validate_lifecycle_invariants()


def test_cancelling_task_also_cancels_nonterminal_plan(tmp_path) -> None:
    context, runner = make_context(tmp_path)
    context.begin_task("refactor the game")
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Refactor the game module"}]
    )
    context.plan_controller.submit_for_execution()
    context.plan_controller.approve()

    runner.abort(
        context,
        reason="interrupted",
        message="Stopped: interrupted by user (Ctrl+C).",
        exc=KeyboardInterrupt(),
    )

    assert context.task_status is TaskStatus.CANCELLED
    assert context.plan_state.phase is PlanPhase.CANCELLED
    context.validate_lifecycle_invariants()


def test_source_working_set_is_task_local(tmp_path) -> None:
    source = tmp_path / "demo.py"
    source.write_text("one\ntwo\n", encoding="utf-8")
    context, _ = make_context(tmp_path, policy=PlanPolicy.OFF)
    context.begin_task("inspect source")
    context.source_read_state(
        source,
        requested_path="demo.py",
        sha256="a" * 64,
        total_lines=2,
    ).record_range(0, 2, observation_chars=8, turn_id=1)
    context.transition_task(TaskStatus.COMPLETED, trigger="test_complete")

    context.begin_task("new task")

    assert context.read_file_segments == {}
    assert context.source_read_metrics.read_file_calls == 0


def test_deterministic_failure_progress_is_task_local(tmp_path) -> None:
    context, _ = make_context(tmp_path, policy=PlanPolicy.OFF)
    context.begin_task("first task")
    context.task_failure_fingerprint = "old-fingerprint"
    context.task_failure_repeat_count = 2
    context.task_saturated_invalid_calls = 1
    context.transition_task(TaskStatus.COMPLETED, trigger="test_complete")

    context.begin_task("next task")

    assert context.task_failure_fingerprint is None
    assert context.task_failure_repeat_count == 0
    assert context.task_saturated_invalid_calls == 0
