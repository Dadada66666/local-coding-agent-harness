from types import SimpleNamespace

from runtime.call_budget import TaskCallBudget
from runtime.config import RunConfig


def test_call_budget_reserves_verification_and_derives_plan_guidance() -> None:
    context = SimpleNamespace(
        config=RunConfig(max_turns=40, verification_reserve_calls=4),
        task_model_calls=9,
    )

    budget = TaskCallBudget.from_context(context)

    assert budget.used_calls == 9
    assert budget.remaining_calls == 31
    assert budget.verification_reserve_calls == 4
    assert budget.work_calls_remaining == 27
    assert budget.plan_detail_warning_steps == 9
    assert budget.planning_pressure == "normal"
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
    assert budget.plan_detail_warning_steps == 3
    assert budget.planning_pressure == "tight"


def test_call_budget_marks_the_final_reserve() -> None:
    budget = TaskCallBudget.from_limits(
        max_calls=40,
        used_calls=36,
        configured_reserve=4,
    )

    assert budget.remaining_calls == 4
    assert budget.reserve_active is True
    assert budget.nearing_reserve is True
