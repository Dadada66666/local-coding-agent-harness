from types import SimpleNamespace

from agent.messages import TokenUsage
from runtime.config import RunConfig
from runtime.plan import PlanPhase, PlanStep, PlanStepStatus
from runtime.progress import PlanningProgress, PlanExecutionProgress, ToolProgressPolicy


class RecordingTrace:
    def __init__(self) -> None:
        self.events = []

    def log(self, event) -> None:
        self.events.append(event)


def make_context(*, max_turns: int = 100, stall_calls: int = 3):
    step = PlanStep("step-1", "Implement the focused change", PlanStepStatus.IN_PROGRESS)
    return SimpleNamespace(
        config=RunConfig(
            max_turns=max_turns,
            plan_step_stall_calls=stall_calls,
        ),
        plan_state=SimpleNamespace(phase=PlanPhase.EXECUTING, steps=[step]),
        plan_execution_progress=PlanExecutionProgress(),
        task_model_calls=1,
        task_failure_fingerprint=None,
        task_failure_repeat_count=0,
        task_saturated_invalid_calls=0,
        mutation_version=0,
        task_verification_version=None,
        source_read_metrics=SimpleNamespace(unique_source_lines_returned=0),
        has_task_mutations=lambda: False,
    )


def successful_status_result():
    call = SimpleNamespace(name="update_plan", arguments={"action": "update_step"})
    result = SimpleNamespace(ok=True, content="status unchanged", metadata={})
    return [(call, result)]


def response():
    return SimpleNamespace(usage=TokenUsage())


def planning_context(*, hard_limit: int = 8, grace_calls: int = 2):
    progress = PlanningProgress()
    progress.start_episode(
        model_call=0,
        include_task_history=True,
        plan_version=0,
        has_draft=False,
    )
    return SimpleNamespace(
        config=RunConfig(
            max_turns=40,
            planning_soft_limit_calls=2,
            planning_hard_limit_calls=hard_limit,
            plan_draft_grace_calls=grace_calls,
        ),
        plan_state=SimpleNamespace(
            phase=PlanPhase.PLANNING,
            version=0,
            steps=[],
        ),
        planning_progress=progress,
        task_model_calls=0,
        current_turn_id="turn-1",
        trace=RecordingTrace(),
    )


def test_plan_step_stall_emits_a_bounded_nudge() -> None:
    context = make_context(stall_calls=3)
    policy = ToolProgressPolicy()

    assert (
        policy.evaluate(
            context,
            response(),
            successful_status_result(),
            max_output_tokens=4096,
        ).action
        == "continue"
    )
    context.task_model_calls = 4

    decision = policy.evaluate(
        context,
        response(),
        successful_status_result(),
        max_output_tokens=4096,
    )

    assert decision.action == "retry"
    assert decision.reason == "plan_step_stalled"
    assert decision.current_step_id == "step-1"
    assert decision.calls_without_progress == 3


def test_mutation_resets_plan_step_stall_counter() -> None:
    context = make_context(stall_calls=3)
    policy = ToolProgressPolicy()
    policy.evaluate(
        context,
        response(),
        successful_status_result(),
        max_output_tokens=4096,
    )
    context.task_model_calls = 3
    context.mutation_version = 1

    decision = policy.evaluate(
        context,
        response(),
        successful_status_result(),
        max_output_tokens=4096,
    )

    assert decision.action == "continue"
    assert context.plan_execution_progress.last_progress_call == 3


def test_verification_reserve_nudges_without_stopping_execution() -> None:
    context = make_context(max_turns=40)
    context.task_model_calls = 36
    context.mutation_version = 1
    context.has_task_mutations = lambda: True
    policy = ToolProgressPolicy()

    decision = policy.evaluate(
        context,
        response(),
        successful_status_result(),
        max_output_tokens=4096,
    )

    assert decision.action == "retry"
    assert decision.reason == "verification_budget_reserve"
    assert decision.remaining_model_calls == 4
    assert "Run the smallest relevant verification now" in decision.message

    context.task_model_calls = 37
    repeated = policy.evaluate(
        context,
        response(),
        successful_status_result(),
        max_output_tokens=4096,
    )
    assert repeated.reason != "verification_budget_reserve"


def test_existing_draft_enters_finalize_only_after_local_grace() -> None:
    context = planning_context(hard_limit=8, grace_calls=2)
    context.plan_state.steps = [
        PlanStep("step-1", "Implement the focused change", PlanStepStatus.PENDING)
    ]
    context.plan_state.version = 1
    context.task_model_calls = 1
    context.planning_progress.record_plan_change(model_call=1, version=1)
    policy = ToolProgressPolicy()

    context.task_model_calls = 2
    assert policy.prepare_turn(context).finalize_required is False

    context.task_model_calls = 3
    assert policy.prepare_turn(context).finalize_required is False

    context.task_model_calls = 4
    budget = policy.prepare_turn(context)

    assert budget.finalize_required is True
    assert context.planning_progress.finalize_reason == "plan_draft_grace_exhausted"
    assert context.trace.events[-1]["type"] == "planning_finalize_required"


def test_draft_revision_does_not_extend_planning_hard_deadline() -> None:
    context = planning_context(hard_limit=4, grace_calls=3)
    context.plan_state.steps = [
        PlanStep("step-1", "Implement the focused change", PlanStepStatus.PENDING)
    ]
    context.task_model_calls = 1
    context.plan_state.version = 1
    context.planning_progress.record_plan_change(model_call=1, version=1)

    context.task_model_calls = 3
    context.plan_state.version = 2
    context.planning_progress.record_plan_change(model_call=3, version=2)
    context.task_model_calls = 4
    budget = ToolProgressPolicy().prepare_turn(context)

    assert context.planning_progress.calls_since_plan_change(4) == 1
    assert budget.hard_limit_reached is True
    assert budget.finalize_required is True
    assert context.planning_progress.finalize_reason == "planning_hard_limit"
