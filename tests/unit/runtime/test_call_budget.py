from types import SimpleNamespace

from runtime.call_budget import PlanningCallBudget, TaskCallBudget
from runtime.config import RunConfig
from runtime.progress import PlanningProgress


def test_call_budget_reserves_verification_calls() -> None:
    context = SimpleNamespace(
        config=RunConfig(max_turns=40, verification_reserve_calls=4),
        task_model_calls=9,
    )

    budget = TaskCallBudget.from_context(context)

    assert budget.used_calls == 9
    assert budget.remaining_calls == 31
    assert budget.verification_reserve_calls == 4
    assert budget.work_calls_remaining == 27
    assert budget.reserve_active is False


def test_call_budget_scales_reserve_for_small_tasks() -> None:
    budget = TaskCallBudget.from_limits(
        max_calls=5,
        used_calls=1,
        configured_reserve=4,
    )

    assert budget.verification_reserve_calls == 1
    assert budget.remaining_calls == 4
    assert budget.work_calls_remaining == 3


def test_call_budget_marks_the_final_reserve() -> None:
    budget = TaskCallBudget.from_limits(
        max_calls=40,
        used_calls=36,
        configured_reserve=4,
    )

    assert budget.remaining_calls == 4
    assert budget.reserve_active is True
    assert budget.nearing_reserve is True


def test_planning_budget_is_phase_local_and_auto_derived() -> None:
    progress = PlanningProgress()
    progress.start_episode(
        model_call=3,
        include_task_history=False,
        plan_version=0,
        has_draft=False,
    )
    context = SimpleNamespace(
        config=RunConfig(max_turns=40, verification_reserve_calls=4),
        task_model_calls=9,
        planning_progress=progress,
    )

    budget = PlanningCallBudget.from_context(context)

    assert budget.used_calls == 6
    assert budget.soft_limit_calls == 6
    assert budget.hard_limit_calls == 8
    assert budget.calls_until_hard_limit == 2
    assert budget.soft_limit_reached is True
    assert budget.hard_limit_reached is False


def test_planning_budget_respects_explicit_limits_and_finalize_state() -> None:
    progress = PlanningProgress()
    progress.start_episode(
        model_call=1,
        include_task_history=False,
        plan_version=0,
        has_draft=False,
    )
    progress.require_finalization("draft_grace_exhausted")
    context = SimpleNamespace(
        config=RunConfig(
            max_turns=40,
            planning_soft_limit_calls=4,
            planning_hard_limit_calls=7,
        ),
        task_model_calls=5,
        planning_progress=progress,
    )

    budget = PlanningCallBudget.from_context(context)

    assert budget.used_calls == 4
    assert budget.soft_limit_calls == 4
    assert budget.hard_limit_calls == 7
    assert budget.finalize_required is True
