from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskCallBudget:
    """A derived view of the current task's bounded model-call budget."""

    max_calls: int
    used_calls: int
    remaining_calls: int
    verification_reserve_calls: int
    work_calls_remaining: int
    plan_detail_warning_steps: int

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
        detail_warning_steps = min(12, max(3, (work_calls + 2) // 3))
        return cls(
            max_calls=bounded_max,
            used_calls=bounded_used,
            remaining_calls=remaining,
            verification_reserve_calls=reserve,
            work_calls_remaining=work_calls,
            plan_detail_warning_steps=detail_warning_steps,
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

    @property
    def planning_pressure(self) -> str:
        if self.work_calls_remaining <= 4:
            return "tight"
        if self.work_calls_remaining <= 12:
            return "constrained"
        return "normal"

    def to_dict(self) -> dict[str, int | bool | str]:
        return {
            "max_calls": self.max_calls,
            "used_calls": self.used_calls,
            "remaining_calls": self.remaining_calls,
            "verification_reserve_calls": self.verification_reserve_calls,
            "work_calls_remaining": self.work_calls_remaining,
            "plan_detail_warning_steps": self.plan_detail_warning_steps,
            "planning_pressure": self.planning_pressure,
            "reserve_active": self.reserve_active,
        }
