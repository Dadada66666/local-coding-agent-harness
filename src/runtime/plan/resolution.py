from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.plan.models import PlanValidationError


EXACT_APPROVAL_RESPONSES = frozenset(
    {
        "同意",
        "同意执行",
        "批准",
        "批准执行",
        "approve",
        "approved",
    }
)

PLAN_APPROVAL_CHOICES = """Plan action:
  1) Approve and execute
  2) Revise the plan
  3) Reject and cancel
Commands: /approve | /revise <feedback> | /cancel-plan"""


@dataclass(frozen=True, slots=True)
class PlanResponseResolution:
    action: str
    continuation_id: int | None
    state: Any


def deterministic_plan_response(text: str) -> str | None:
    """Recognize only zero-ambiguity approval commands."""
    normalized = str(text).strip()
    if normalized.isascii():
        normalized = normalized.lower()
    return "approve" if normalized in EXACT_APPROVAL_RESPONSES else None


def apply_plan_response(
    context,
    action: str,
    *,
    feedback: str | None = None,
    reason: str | None = None,
    source: str,
    require_continuation: bool = False,
) -> PlanResponseResolution:
    """Apply one control-plane response and consume its continuation exactly once."""
    pending = getattr(context, "has_pending_user_continuation", None)
    has_continuation = bool(pending()) if callable(pending) else False
    continuation_id = (
        getattr(context, "pending_user_continuation_id", None) if has_continuation else None
    )
    if require_continuation and continuation_id is None:
        raise PlanValidationError("a fresh user continuation is required")

    if action == "approve":
        state = context.plan_controller.approve()
    elif action == "revise":
        state = context.plan_controller.revise(feedback or "")
    elif action == "cancel":
        state = context.plan_controller.cancel(reason or "Cancelled by the user.")
    else:
        raise PlanValidationError(f"unsupported plan response action: {action}")

    if continuation_id is not None:
        context.consume_user_continuation(continuation_id)

    trace = getattr(context, "trace", None)
    if trace is not None and hasattr(trace, "log"):
        trace.log(
            {
                "type": "plan_response_resolved",
                "task_id": getattr(context, "task_id", None),
                "continuation_id": continuation_id,
                "action": action,
                "source": source,
                "plan_phase": state.phase.value,
                "plan_version": state.version,
            }
        )
    return PlanResponseResolution(action, continuation_id, state)
