from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class PlanPolicy(StrEnum):
    OFF = "off"
    AUTO = "auto"
    REQUIRED = "required"


class PlanApprovalPolicy(StrEnum):
    MANUAL = "manual"
    AUTO = "auto"


class ExecutionPath(StrEnum):
    UNDECIDED = "undecided"
    DIRECT = "direct"
    PLAN = "plan"


class PlanPhase(StrEnum):
    INACTIVE = "inactive"
    PLANNING = "planning"
    AWAITING_APPROVAL = "awaiting_approval"
    EXECUTING = "executing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class PlanStepStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class PlanError(ValueError):
    """Base error for invalid plan input or state transitions."""


class PlanValidationError(PlanError):
    pass


class PlanTransitionError(PlanError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class PlanStep:
    id: str
    description: str
    status: PlanStepStatus = PlanStepStatus.PENDING

    def __post_init__(self) -> None:
        if not isinstance(self.id, str):
            raise PlanValidationError("plan step id must be a string")
        if not isinstance(self.description, str):
            raise PlanValidationError("plan step description must be a string")
        self.id = self.id.strip()
        self.description = self.description.strip()
        try:
            self.status = PlanStepStatus(self.status)
        except ValueError as exc:
            raise PlanValidationError(f"invalid plan step status: {self.status}") from exc
        if not self.id:
            raise PlanValidationError("plan step id must not be empty")
        if not self.description:
            raise PlanValidationError("plan step description must not be empty")
        if len(self.id) > 200:
            raise PlanValidationError("plan step id must be at most 200 characters")
        if len(self.description) > 1000:
            raise PlanValidationError(
                "plan step description must be at most 1000 characters"
            )

    @classmethod
    def from_value(cls, value: PlanStep | dict[str, Any]) -> PlanStep:
        if isinstance(value, cls):
            return cls(value.id, value.description, value.status)
        if not isinstance(value, dict):
            raise PlanValidationError("each plan step must be an object")
        unknown = set(value) - {"id", "description", "status"}
        if unknown:
            raise PlanValidationError(
                f"unknown plan step fields: {', '.join(sorted(unknown))}"
            )
        if "id" not in value or "description" not in value:
            raise PlanValidationError("each plan step requires id and description")
        return cls(
            id=value["id"],
            description=value["description"],
            status=value.get("status", PlanStepStatus.PENDING),
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "description": self.description,
            "status": self.status.value,
        }


@dataclass(slots=True)
class PlanState:
    policy: PlanPolicy
    execution_path: ExecutionPath
    phase: PlanPhase
    goal: str
    approval_policy: PlanApprovalPolicy = PlanApprovalPolicy.MANUAL
    steps: list[PlanStep] = field(default_factory=list)
    version: int = 0
    approved_version: int | None = None
    explanation: str | None = None
    selection_reason: str | None = None
    revision_feedback: str | None = None
    approval_source: str | None = None
    updated_at: str = field(default_factory=utc_now)

    @classmethod
    def initial(
        cls,
        policy: PlanPolicy | str,
        goal: str,
        *,
        approval_policy: PlanApprovalPolicy | str = PlanApprovalPolicy.MANUAL,
    ) -> PlanState:
        try:
            normalized_policy = PlanPolicy(policy)
        except ValueError as exc:
            raise PlanValidationError(f"invalid plan policy: {policy}") from exc
        try:
            normalized_approval_policy = PlanApprovalPolicy(approval_policy)
        except ValueError as exc:
            raise PlanValidationError(
                f"invalid plan approval policy: {approval_policy}"
            ) from exc

        if normalized_policy is PlanPolicy.OFF:
            execution_path = ExecutionPath.DIRECT
            phase = PlanPhase.INACTIVE
        elif normalized_policy is PlanPolicy.AUTO:
            execution_path = ExecutionPath.UNDECIDED
            phase = PlanPhase.INACTIVE
        else:
            execution_path = ExecutionPath.PLAN
            phase = PlanPhase.PLANNING

        state = cls(
            policy=normalized_policy,
            execution_path=execution_path,
            phase=phase,
            goal=str(goal).strip(),
            approval_policy=normalized_approval_policy,
        )
        state.validate()
        return state

    def validate(self) -> None:
        try:
            self.policy = PlanPolicy(self.policy)
            self.approval_policy = PlanApprovalPolicy(self.approval_policy)
            self.execution_path = ExecutionPath(self.execution_path)
            self.phase = PlanPhase(self.phase)
        except ValueError as exc:
            raise PlanValidationError(f"invalid plan state enum: {exc}") from exc

        if self.version < 0:
            raise PlanValidationError("plan version must be >= 0")
        if self.approved_version is not None:
            if self.approved_version < 0 or self.approved_version > self.version:
                raise PlanValidationError("approved plan version is invalid")

        identifiers = [step.id for step in self.steps]
        if len(identifiers) != len(set(identifiers)):
            raise PlanValidationError("plan step ids must be unique")
        if sum(step.status is PlanStepStatus.IN_PROGRESS for step in self.steps) > 1:
            raise PlanValidationError("only one plan step may be in progress")

        if self.policy is PlanPolicy.OFF:
            if self.execution_path is not ExecutionPath.DIRECT:
                raise PlanValidationError("off policy must use the direct execution path")
            if self.phase is not PlanPhase.INACTIVE or self.steps:
                raise PlanValidationError("off policy must remain inactive without plan steps")

        if (
            self.policy is PlanPolicy.REQUIRED
            and self.execution_path is not ExecutionPath.PLAN
        ):
            raise PlanValidationError("required policy must use the plan execution path")

        if self.execution_path is ExecutionPath.UNDECIDED:
            if self.policy is not PlanPolicy.AUTO or self.phase is not PlanPhase.INACTIVE:
                raise PlanValidationError("only auto policy may have an undecided inactive path")
            if self.steps:
                raise PlanValidationError("undecided execution cannot contain plan steps")

        if self.execution_path is ExecutionPath.DIRECT:
            if self.phase is not PlanPhase.INACTIVE or self.steps:
                raise PlanValidationError("direct execution cannot contain a plan lifecycle")
            if self.approved_version is not None or self.approval_source is not None:
                raise PlanValidationError("direct execution cannot contain plan authorization")

        if self.execution_path is ExecutionPath.PLAN:
            if self.phase is PlanPhase.INACTIVE:
                raise PlanValidationError("plan execution path cannot be inactive")
            if self.phase in {
                PlanPhase.AWAITING_APPROVAL,
                PlanPhase.EXECUTING,
                PlanPhase.COMPLETED,
            } and not self.steps:
                raise PlanValidationError("an active or completed plan must contain steps")

        if (
            self.phase is PlanPhase.AWAITING_APPROVAL
            and self.approval_policy is not PlanApprovalPolicy.MANUAL
        ):
            raise PlanValidationError("only manual approval policy waits for user approval")
        if self.phase in {PlanPhase.PLANNING, PlanPhase.AWAITING_APPROVAL}:
            if self.approved_version is not None or self.approval_source is not None:
                raise PlanValidationError("planning phases cannot retain plan authorization")
        if self.phase is PlanPhase.EXECUTING and self.approved_version != self.version:
            raise PlanValidationError("executing requires the current plan version to be authorized")
        if self.phase is PlanPhase.EXECUTING:
            expected_source = (
                "auto_policy"
                if self.approval_policy is PlanApprovalPolicy.AUTO
                else "user"
            )
            if self.approval_source != expected_source:
                raise PlanValidationError(
                    "executing under "
                    f"{self.approval_policy.value} approval requires "
                    f"{expected_source} authorization"
                )
        if self.phase is PlanPhase.COMPLETED:
            if self.approved_version != self.version:
                raise PlanValidationError("completed plan version must be authorized")
            if any(step.status is not PlanStepStatus.COMPLETED for step in self.steps):
                raise PlanValidationError("all plan steps must be completed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy": self.policy.value,
            "approval_policy": self.approval_policy.value,
            "execution_path": self.execution_path.value,
            "phase": self.phase.value,
            "goal": self.goal,
            "steps": [step.to_dict() for step in self.steps],
            "version": self.version,
            "approved_version": self.approved_version,
            "explanation": self.explanation,
            "selection_reason": self.selection_reason,
            "revision_feedback": self.revision_feedback,
            "approval_source": self.approval_source,
            "updated_at": self.updated_at,
        }

    def checkpoint_summary(self, *, pending_limit: int = 10) -> dict[str, Any] | None:
        if self.policy is PlanPolicy.OFF:
            return None
        current = next(
            (step for step in self.steps if step.status is PlanStepStatus.IN_PROGRESS),
            None,
        )
        pending = [
            {"id": step.id, "description": step.description[:300]}
            for step in self.steps
            if step.status is PlanStepStatus.PENDING
        ][:pending_limit]
        return {
            "policy": self.policy.value,
            "approval_policy": self.approval_policy.value,
            "execution_path": self.execution_path.value,
            "phase": self.phase.value,
            "version": self.version,
            "approved_version": self.approved_version,
            "current_step": current.to_dict() if current else None,
            "pending_steps": pending,
        }
