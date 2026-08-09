from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from runtime.plan.models import (
    ExecutionPath,
    PlanApprovalPolicy,
    PlanPhase,
    PlanPolicy,
    PlanState,
    PlanStep,
    PlanStepStatus,
    PlanTransitionError,
    PlanValidationError,
    utc_now,
)


class PlanController:
    """The single authority for mutations to a task's PlanState."""

    def __init__(
        self,
        state: PlanState,
        *,
        store=None,
        trace=None,
        transition_listener=None,
    ) -> None:
        self.state = state
        self.store = store
        self.trace = trace
        self.transition_listener = transition_listener

    def set_transition_listener(self, listener) -> None:
        self.transition_listener = listener

    def record_initial_state(self) -> None:
        self.state.validate()
        if self.state.policy is PlanPolicy.OFF:
            return
        self._record("initialized", before=None)

    def reset(
        self,
        *,
        goal: str,
        policy: PlanPolicy | str,
        approval_policy: PlanApprovalPolicy | str = PlanApprovalPolicy.MANUAL,
    ) -> PlanState:
        before = self._snapshot()
        initial = PlanState.initial(policy, goal, approval_policy=approval_policy)
        for field_name in initial.__dataclass_fields__:
            setattr(self.state, field_name, deepcopy(getattr(initial, field_name)))
        if self.state.policy is PlanPolicy.OFF:
            if self.store is not None:
                self.store.clear()
            return self.state
        self._record("task_initialized", before=before)
        return self.state

    def select_execution_path(
        self,
        mode: ExecutionPath | str,
        *,
        reason: str,
        has_mutations: bool,
    ) -> PlanState:
        try:
            selected = ExecutionPath(mode)
        except ValueError as exc:
            raise PlanValidationError("mode must be direct or plan") from exc
        reason = self._required_text(reason, "selection reason")
        if selected not in {ExecutionPath.DIRECT, ExecutionPath.PLAN}:
            raise PlanValidationError("mode must be direct or plan")
        if self.state.policy is not PlanPolicy.AUTO:
            raise PlanTransitionError("execution mode can only be selected under auto policy")
        if self.state.execution_path is not ExecutionPath.UNDECIDED:
            raise PlanTransitionError("execution path has already been selected")
        if self.state.phase is not PlanPhase.INACTIVE:
            raise PlanTransitionError("execution path cannot be selected in the current phase")
        if has_mutations:
            raise PlanTransitionError("execution path cannot change after repository mutations")

        before = self._snapshot()
        self.state.execution_path = selected
        self.state.selection_reason = reason
        if selected is ExecutionPath.PLAN:
            self.state.phase = PlanPhase.PLANNING
        self._record(f"selected_{selected.value}", before=before)
        return self.state

    def force_plan(self, *, reason: str, has_mutations: bool) -> PlanState:
        if has_mutations:
            raise PlanTransitionError("plan mode cannot be forced after repository mutations")
        if self.state.phase in {PlanPhase.COMPLETED, PlanPhase.CANCELLED}:
            raise PlanTransitionError("a completed or cancelled plan cannot be restarted")
        if self.state.execution_path is ExecutionPath.PLAN:
            raise PlanTransitionError("the current task is already using plan mode")

        before = self._snapshot()
        self.state.policy = PlanPolicy.REQUIRED
        self.state.execution_path = ExecutionPath.PLAN
        self.state.phase = PlanPhase.PLANNING
        self.state.selection_reason = self._required_text(reason, "selection reason")
        self.state.steps.clear()
        self.state.version = 0
        self.state.approved_version = None
        self.state.approval_source = None
        self._record("plan_forced_by_user", before=before)
        return self.state

    def replace_plan(
        self,
        steps: Iterable[PlanStep | dict[str, Any]],
        *,
        explanation: str | None = None,
    ) -> PlanState:
        self._require_phase(PlanPhase.PLANNING)
        normalized = [PlanStep.from_value(step) for step in steps]
        if not normalized:
            raise PlanValidationError("plan must contain at least one step")
        if len(normalized) > 100:
            raise PlanValidationError("a plan may contain at most 100 steps")
        identifiers = [step.id for step in normalized]
        if len(identifiers) != len(set(identifiers)):
            raise PlanValidationError("plan step ids must be unique")
        if sum(step.status is PlanStepStatus.IN_PROGRESS for step in normalized) > 1:
            raise PlanValidationError("only one plan step may be in progress")
        normalized_explanation = self._optional_text(explanation)

        existing = [step.to_dict() for step in self.state.steps]
        incoming = [step.to_dict() for step in normalized]
        if existing == incoming and self.state.explanation == normalized_explanation:
            return self.state

        before = self._snapshot()
        self.state.steps = normalized
        self.state.explanation = normalized_explanation
        self.state.version += 1
        self.state.approved_version = None
        self.state.approval_source = None
        self._record("plan_replaced", before=before)
        return self.state

    def submit_for_execution(self) -> PlanState:
        self._require_phase(PlanPhase.PLANNING)
        if not self.state.steps:
            raise PlanValidationError("an empty plan cannot be submitted")
        if self.state.version <= 0:
            raise PlanValidationError("the plan must be created before submission")

        before = self._snapshot()
        if self.state.approval_policy is PlanApprovalPolicy.AUTO:
            self.state.approved_version = self.state.version
            self.state.approval_source = "auto_policy"
            self.state.phase = PlanPhase.EXECUTING
            action = "plan_auto_authorized"
        else:
            self.state.approved_version = None
            self.state.approval_source = None
            self.state.phase = PlanPhase.AWAITING_APPROVAL
            action = "plan_submitted"
        self._record(action, before=before)
        return self.state

    def approve(self) -> PlanState:
        self._require_phase(PlanPhase.AWAITING_APPROVAL)
        if not self.state.steps:
            raise PlanValidationError("an empty plan cannot be approved")

        before = self._snapshot()
        self.state.approved_version = self.state.version
        self.state.approval_source = "user"
        self.state.phase = PlanPhase.EXECUTING
        self._record("plan_approved", before=before)
        return self.state

    def revise(self, feedback: str) -> PlanState:
        self._require_phase(PlanPhase.AWAITING_APPROVAL)
        before = self._snapshot()
        self.state.phase = PlanPhase.PLANNING
        self.state.version += 1
        self.state.approved_version = None
        self.state.approval_source = None
        self.state.revision_feedback = self._required_text(feedback, "revision feedback")
        self._record("plan_revision_requested", before=before)
        return self.state

    def update_step(self, step_id: str, status: PlanStepStatus | str) -> PlanState:
        self._require_authorized_execution()
        identifier = self._required_text(step_id, "step id")
        try:
            target_status = PlanStepStatus(status)
        except ValueError as exc:
            raise PlanValidationError(f"invalid plan step status: {status}") from exc
        target = next((step for step in self.state.steps if step.id == identifier), None)
        if target is None:
            raise PlanValidationError(f"unknown plan step: {identifier}")
        if target.status is target_status:
            return self.state

        allowed = {
            PlanStepStatus.PENDING: {
                PlanStepStatus.IN_PROGRESS,
                PlanStepStatus.COMPLETED,
            },
            PlanStepStatus.IN_PROGRESS: {
                PlanStepStatus.PENDING,
                PlanStepStatus.COMPLETED,
            },
            PlanStepStatus.COMPLETED: set(),
        }
        if target_status not in allowed[target.status]:
            raise PlanTransitionError(
                f"cannot change step {identifier} from {target.status.value} "
                f"to {target_status.value}"
            )
        if target_status is PlanStepStatus.IN_PROGRESS and any(
            step.status is PlanStepStatus.IN_PROGRESS and step.id != identifier
            for step in self.state.steps
        ):
            raise PlanTransitionError("complete or pause the current step first")

        before = self._snapshot()
        target.status = target_status
        self._record("plan_step_updated", before=before, step_id=identifier)
        return self.state

    def request_replan(self, reason: str) -> PlanState:
        self._require_authorized_execution()
        before = self._snapshot()
        self.state.phase = PlanPhase.PLANNING
        self.state.version += 1
        self.state.approved_version = None
        self.state.approval_source = None
        self.state.revision_feedback = self._required_text(reason, "replan reason")
        for step in self.state.steps:
            if step.status is PlanStepStatus.IN_PROGRESS:
                step.status = PlanStepStatus.PENDING
        self._record("plan_replan_requested", before=before)
        return self.state

    def cancel(self, reason: str | None = None) -> PlanState:
        if self.state.phase not in {
            PlanPhase.PLANNING,
            PlanPhase.AWAITING_APPROVAL,
            PlanPhase.EXECUTING,
        }:
            raise PlanTransitionError("the current plan cannot be cancelled")
        before = self._snapshot()
        self.state.phase = PlanPhase.CANCELLED
        if reason is not None:
            self.state.revision_feedback = self._required_text(reason, "cancellation reason")
        self._record("plan_cancelled", before=before)
        return self.state

    def complete(self) -> PlanState:
        self._require_authorized_execution()
        incomplete = [
            step.id
            for step in self.state.steps
            if step.status is not PlanStepStatus.COMPLETED
        ]
        if incomplete:
            raise PlanTransitionError(
                f"cannot complete plan with unfinished steps: {', '.join(incomplete)}"
            )
        before = self._snapshot()
        self.state.phase = PlanPhase.COMPLETED
        self._record("plan_completed", before=before)
        return self.state

    def status_text(self) -> str:
        lines = [
            f"policy: {self.state.policy.value}",
            f"approval_policy: {self.state.approval_policy.value}",
            f"execution_path: {self.state.execution_path.value}",
            f"phase: {self.state.phase.value}",
            f"version: {self.state.version}",
            f"approved_version: {self.state.approved_version}",
        ]
        if self.state.selection_reason:
            lines.append(f"selection_reason: {self.state.selection_reason}")
        if self.state.explanation:
            lines.append(f"explanation: {self.state.explanation}")
        if self.state.steps:
            lines.append("steps:")
            lines.extend(
                f"- [{step.status.value}] {step.id}: {step.description}"
                for step in self.state.steps
            )
        return "\n".join(lines)

    def _require_phase(self, expected: PlanPhase) -> None:
        if self.state.execution_path is not ExecutionPath.PLAN:
            raise PlanTransitionError("the current task is not using plan mode")
        if self.state.phase is not expected:
            raise PlanTransitionError(
                f"operation requires {expected.value}, current phase is {self.state.phase.value}"
            )

    def _require_authorized_execution(self) -> None:
        self._require_phase(PlanPhase.EXECUTING)
        if self.state.approved_version != self.state.version:
            raise PlanTransitionError("the current plan version is not authorized")

    def _required_text(self, value: str, field_name: str) -> str:
        normalized = str(value).strip()
        if not normalized:
            raise PlanValidationError(f"{field_name} must not be empty")
        return normalized[:2000]

    def _optional_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        return normalized[:2000] or None

    def _snapshot(self) -> dict[str, Any]:
        return deepcopy(self.state.to_dict())

    def _record(
        self,
        action: str,
        *,
        before: dict[str, Any] | None,
        **details: Any,
    ) -> None:
        self.state.updated_at = utc_now()
        self.state.validate()
        after = self.state.to_dict()
        if self.transition_listener is not None:
            self.transition_listener(action, before, after)
        snapshot_written = True
        if self.store is not None:
            snapshot_written = self.store.save(self.state, task=self.state.goal)
        if self.trace is not None:
            self.trace.log(
                {
                    "type": "plan_transition",
                    "action": action,
                    "task": self.state.goal[:500],
                    "before": self._trace_snapshot(before),
                    "after": self._trace_snapshot(after),
                    "snapshot_written": snapshot_written,
                    **details,
                }
            )

    def _trace_snapshot(self, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        steps = value.get("steps") or []
        return {
            "policy": value.get("policy"),
            "approval_policy": value.get("approval_policy"),
            "execution_path": value.get("execution_path"),
            "phase": value.get("phase"),
            "version": value.get("version"),
            "approved_version": value.get("approved_version"),
            "approval_source": value.get("approval_source"),
            "selection_reason": str(value.get("selection_reason") or "")[:500] or None,
            "step_count": len(steps),
            "steps": [
                {
                    "id": str(step.get("id", ""))[:200],
                    "status": step.get("status"),
                }
                for step in steps[:50]
                if isinstance(step, dict)
            ],
            "steps_omitted": max(len(steps) - 50, 0),
        }
