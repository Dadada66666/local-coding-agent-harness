from types import SimpleNamespace

from runtime.call_budget import TaskCallBudget
from runtime.config import RunConfig


def test_call_budget_reports_global_usage() -> None:
    context = SimpleNamespace(
        config=RunConfig(max_turns=40),
        task_model_calls=9,
    )

    budget = TaskCallBudget.from_context(context)

    assert budget.max_calls == 40
    assert budget.used_calls == 9
    assert budget.remaining_calls == 31
    assert budget.approaching_limit is False


def test_call_budget_clamps_usage_and_marks_global_limit_pressure() -> None:
    budget = TaskCallBudget.from_limits(max_calls=5, used_calls=99)

    assert budget.max_calls == 5
    assert budget.used_calls == 5
    assert budget.remaining_calls == 0
    assert budget.approaching_limit is True


def test_call_budget_only_advises_near_the_global_limit() -> None:
    assert TaskCallBudget.from_limits(max_calls=40, used_calls=34).approaching_limit is False
    assert TaskCallBudget.from_limits(max_calls=40, used_calls=35).approaching_limit is True
