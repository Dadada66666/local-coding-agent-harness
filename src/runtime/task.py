from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    WAITING_USER = "waiting_user"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_TASK_STATUSES = frozenset({TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED})


class TaskTransitionError(ValueError):
    pass


def validate_task_transition(
    before: TaskStatus | str,
    after: TaskStatus | str,
    *,
    new_task: bool = False,
) -> tuple[TaskStatus, TaskStatus]:
    current = TaskStatus(before)
    target = TaskStatus(after)
    if current is target:
        return current, target

    allowed = {
        TaskStatus.IDLE: {TaskStatus.RUNNING},
        TaskStatus.RUNNING: {
            TaskStatus.WAITING_USER,
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.WAITING_USER: {
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        },
        TaskStatus.COMPLETED: {TaskStatus.RUNNING} if new_task else set(),
        TaskStatus.FAILED: {TaskStatus.RUNNING} if new_task else set(),
        TaskStatus.CANCELLED: {TaskStatus.RUNNING} if new_task else set(),
    }
    if target not in allowed[current]:
        raise TaskTransitionError(
            f"cannot change task status from {current.value} to {target.value}"
        )
    return current, target


def is_terminal_task_status(status: TaskStatus | str) -> bool:
    return TaskStatus(status) in TERMINAL_TASK_STATUSES
