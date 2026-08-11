from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskCallBudget:
    """A read-only view of the task's global model-call limit."""

    max_calls: int
    used_calls: int
    remaining_calls: int

    @classmethod
    def from_context(cls, context) -> TaskCallBudget:
        return cls.from_limits(
            max_calls=int(context.config.max_turns),
            used_calls=int(getattr(context, "task_model_calls", 0)),
        )

    @classmethod
    def from_limits(cls, *, max_calls: int, used_calls: int) -> TaskCallBudget:
        bounded_max = max(int(max_calls), 1)
        bounded_used = min(max(int(used_calls), 0), bounded_max)
        return cls(
            max_calls=bounded_max,
            used_calls=bounded_used,
            remaining_calls=bounded_max - bounded_used,
        )

    @property
    def approaching_limit(self) -> bool:
        return self.remaining_calls <= 5

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "max_calls": self.max_calls,
            "used_calls": self.used_calls,
            "remaining_calls": self.remaining_calls,
            "approaching_limit": self.approaching_limit,
        }
