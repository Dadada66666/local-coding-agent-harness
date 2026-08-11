from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskCallBudget:
    """A derived view of the current task's bounded model-call budget."""

    max_calls: int
    used_calls: int
    remaining_calls: int
    verification_reserve_calls: int
    work_calls_remaining: int

    @classmethod
    def from_context(cls, context) -> TaskCallBudget:
        config = context.config
        return cls.from_limits(
            max_calls=int(config.max_turns),
            used_calls=int(getattr(context, "task_model_calls", 0)),
            configured_reserve=int(getattr(config, "verification_reserve_calls", 4)),
        )

    @classmethod
    def from_limits(
        cls,
        *,
        max_calls: int,
        used_calls: int,
        configured_reserve: int,
    ) -> TaskCallBudget:
        bounded_max = max(int(max_calls), 1)
        bounded_used = min(max(int(used_calls), 0), bounded_max)
        remaining = bounded_max - bounded_used

        # Small task budgets cannot afford the same fixed reserve as long tasks.
        reserve = min(max(int(configured_reserve), 0), bounded_max // 4)
        work_calls = max(remaining - reserve, 0)
        return cls(
            max_calls=bounded_max,
            used_calls=bounded_used,
            remaining_calls=remaining,
            verification_reserve_calls=reserve,
            work_calls_remaining=work_calls,
        )

    @property
    def reserve_active(self) -> bool:
        return (
            self.verification_reserve_calls > 0
            and self.remaining_calls <= self.verification_reserve_calls
        )

    @property
    def nearing_reserve(self) -> bool:
        return (
            self.verification_reserve_calls > 0
            and self.remaining_calls <= self.verification_reserve_calls + 3
        )

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "max_calls": self.max_calls,
            "used_calls": self.used_calls,
            "remaining_calls": self.remaining_calls,
            "verification_reserve_calls": self.verification_reserve_calls,
            "work_calls_remaining": self.work_calls_remaining,
            "reserve_active": self.reserve_active,
        }


@dataclass(frozen=True, slots=True)
class PlanningCallBudget:
    used_calls: int
    soft_limit_calls: int
    hard_limit_calls: int
    calls_until_hard_limit: int
    soft_limit_reached: bool
    hard_limit_reached: bool
    finalize_required: bool

    @classmethod
    def from_context(cls, context) -> PlanningCallBudget:
        config = context.config
        task_budget = TaskCallBudget.from_context(context)
        available = max(task_budget.max_calls - task_budget.verification_reserve_calls, 1)
        automatic_soft = max(2, math.ceil(task_budget.max_calls * 0.15))
        requested_soft = int(
            getattr(config, "planning_soft_limit_calls", None) or automatic_soft
        )
        automatic_hard = max(
            requested_soft + 2,
            math.ceil(task_budget.max_calls * 0.20),
        )
        hard = min(
            int(getattr(config, "planning_hard_limit_calls", None) or automatic_hard),
            available,
        )
        hard = max(hard, 1)
        soft = min(max(requested_soft, 1), max(hard - 1, 1))

        progress = getattr(context, "planning_progress", None)
        model_calls = int(getattr(context, "task_model_calls", 0))
        if progress is None:
            used = model_calls
            finalize_required = False
        else:
            used = progress.calls_used(model_calls)
            finalize_required = bool(progress.finalize_required)
        return cls(
            used_calls=used,
            soft_limit_calls=soft,
            hard_limit_calls=hard,
            calls_until_hard_limit=max(hard - used, 0),
            soft_limit_reached=used >= soft,
            hard_limit_reached=used >= hard,
            finalize_required=finalize_required,
        )

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "used_calls": self.used_calls,
            "soft_limit_calls": self.soft_limit_calls,
            "hard_limit_calls": self.hard_limit_calls,
            "calls_until_hard_limit": self.calls_until_hard_limit,
            "soft_limit_reached": self.soft_limit_reached,
            "hard_limit_reached": self.hard_limit_reached,
            "finalize_required": self.finalize_required,
        }
