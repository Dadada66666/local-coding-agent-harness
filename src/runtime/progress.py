from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from runtime.call_budget import PlanningCallBudget, TaskCallBudget
from runtime.plan import PlanPhase, PlanStepStatus


EVIDENCE_TOOLS = frozenset({"grep", "list_dir", "read_artifact", "view_diff"})
MAX_EVIDENCE_FINGERPRINTS = 64


@dataclass
class PlanningProgress:
    active: bool = False
    episode_started_call: int = 0
    last_episode_calls: int = 0
    first_draft_call: int | None = None
    last_plan_change_call: int | None = None
    last_plan_version: int = 0
    draft_revision_count: int = 0
    reads_after_draft: int = 0
    finalize_required: bool = False
    finalize_reason: str | None = None

    def reset(self) -> None:
        self.active = False
        self.episode_started_call = 0
        self.last_episode_calls = 0
        self.first_draft_call = None
        self.last_plan_change_call = None
        self.last_plan_version = 0
        self.draft_revision_count = 0
        self.reads_after_draft = 0
        self.finalize_required = False
        self.finalize_reason = None

    def start_episode(
        self,
        *,
        model_call: int,
        include_task_history: bool,
        plan_version: int,
        has_draft: bool,
    ) -> None:
        self.active = True
        self.episode_started_call = 0 if include_task_history else max(model_call, 0)
        self.last_episode_calls = 0
        self.first_draft_call = model_call if has_draft else None
        self.last_plan_change_call = model_call if has_draft else None
        self.last_plan_version = max(plan_version, 0)
        self.draft_revision_count = 0
        self.reads_after_draft = 0
        self.finalize_required = False
        self.finalize_reason = None

    def finish_episode(self, model_call: int) -> None:
        if self.active:
            self.last_episode_calls = self.calls_used(model_call)
        self.active = False
        self.finalize_required = False

    def record_plan_change(self, *, model_call: int, version: int) -> None:
        if not self.active or version == self.last_plan_version:
            return
        if self.first_draft_call is None:
            self.first_draft_call = model_call
        else:
            self.draft_revision_count += 1
        self.last_plan_change_call = model_call
        self.last_plan_version = version

    def calls_used(self, model_call: int) -> int:
        if not self.active:
            return self.last_episode_calls
        return max(int(model_call) - self.episode_started_call, 0)

    def calls_since_plan_change(self, model_call: int) -> int:
        if self.last_plan_change_call is None:
            return 0
        return max(int(model_call) - self.last_plan_change_call, 0)

    def require_finalization(self, reason: str) -> bool:
        if self.finalize_required:
            return False
        self.finalize_required = True
        self.finalize_reason = reason
        return True


@dataclass
class PlanExecutionProgress:
    step_id: str | None = None
    step_status: str | None = None
    last_progress_call: int = 0
    last_nudge_call: int = 0
    mutation_version: int = 0
    verification_version: int | None = None
    unique_source_lines: int = 0
    reserve_nudge_emitted: bool = False
    evidence_fingerprints: list[str] = field(default_factory=list)

    def reset(self) -> None:
        self.step_id = None
        self.step_status = None
        self.last_progress_call = 0
        self.last_nudge_call = 0
        self.mutation_version = 0
        self.verification_version = None
        self.unique_source_lines = 0
        self.reserve_nudge_emitted = False
        self.evidence_fingerprints.clear()

    def start_step(
        self,
        *,
        step_id: str,
        step_status: str,
        model_call: int,
        mutation_version: int,
        verification_version: int | None,
        unique_source_lines: int,
    ) -> None:
        reserve_nudge_emitted = self.reserve_nudge_emitted
        self.reset()
        self.reserve_nudge_emitted = reserve_nudge_emitted
        self.step_id = step_id
        self.step_status = step_status
        self.last_progress_call = model_call
        self.mutation_version = mutation_version
        self.verification_version = verification_version
        self.unique_source_lines = unique_source_lines


@dataclass(frozen=True)
class ProgressDecision:
    action: Literal["continue", "retry", "stop"] = "continue"
    reason: str | None = None
    message: str | None = None
    fingerprint: str | None = None
    repeat_count: int = 0
    saturated_invalid_calls: int = 0
    output_budget_saturated: bool = False
    tools: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    current_step_id: str | None = None
    calls_without_progress: int = 0
    remaining_model_calls: int = 0
    verification_reserve_calls: int = 0


class ToolProgressPolicy:
    """Detect bounded, deterministic tool loops without directing task strategy."""

    def prepare_turn(self, context) -> PlanningCallBudget | None:
        state = getattr(context, "plan_state", None)
        progress = getattr(context, "planning_progress", None)
        if (
            state is None
            or state.phase is not PlanPhase.PLANNING
            or progress is None
        ):
            return None
        model_call = int(getattr(context, "task_model_calls", 0))
        if not progress.active:
            progress.start_episode(
                model_call=0,
                include_task_history=True,
                plan_version=int(getattr(state, "version", 0)),
                has_draft=bool(getattr(state, "steps", ())),
            )
        budget = PlanningCallBudget.from_context(context)
        grace = int(getattr(context.config, "plan_draft_grace_calls", 2))
        grace_expired = bool(
            state.steps
            and progress.last_plan_change_call is not None
            and progress.calls_since_plan_change(model_call) > grace
        )
        reason = None
        if budget.hard_limit_reached:
            reason = "planning_hard_limit"
        elif grace_expired:
            reason = "plan_draft_grace_exhausted"
        if reason is not None and progress.require_finalization(reason):
            context.trace.log(
                {
                    "type": "planning_finalize_required",
                    "turn_id": getattr(context, "current_turn_id", None),
                    "task_model_call": model_call,
                    "reason": reason,
                    "planning_calls": budget.used_calls,
                    "planning_soft_limit": budget.soft_limit_calls,
                    "planning_hard_limit": budget.hard_limit_calls,
                    "plan_version": state.version,
                    "has_draft": bool(state.steps),
                    "draft_revision_count": progress.draft_revision_count,
                }
            )
            budget = PlanningCallBudget.from_context(context)
        return budget

    def evaluate(
        self,
        context,
        response,
        executions: list[tuple[Any, Any]],
        *,
        max_output_tokens: int,
    ) -> ProgressDecision:
        failures = self._deterministic_failures(executions)
        if failures is None:
            self._reset_failures(context)
            self._record_planning_activity(context, executions)
            return self._evaluate_plan_execution(context, executions)

        fingerprint = self._fingerprint(failures)
        if fingerprint == context.task_failure_fingerprint:
            context.task_failure_repeat_count += 1
        else:
            context.task_failure_fingerprint = fingerprint
            context.task_failure_repeat_count = 1

        output_tokens = int(getattr(response.usage, "output_tokens", 0) or 0)
        saturated = max_output_tokens > 0 and output_tokens >= max_output_tokens
        if saturated:
            context.task_saturated_invalid_calls += 1

        tools = tuple(call.name for call, _ in failures)
        errors = tuple(str(result.error or "tool validation failed") for _, result in failures)
        common = {
            "fingerprint": fingerprint,
            "repeat_count": context.task_failure_repeat_count,
            "saturated_invalid_calls": context.task_saturated_invalid_calls,
            "output_budget_saturated": saturated,
            "tools": tools,
            "errors": errors,
        }

        repeated_saturated = saturated and context.task_failure_repeat_count >= 2
        saturation_exhausted = context.task_saturated_invalid_calls >= 3
        repeated_invalid = context.task_failure_repeat_count >= 3
        if repeated_saturated or saturation_exhausted or repeated_invalid:
            return ProgressDecision(
                action="stop",
                reason=(
                    "output_budget_saturated"
                    if repeated_saturated or saturation_exhausted
                    else "repeated_invalid_tool_call"
                ),
                **common,
            )

        if saturated:
            return ProgressDecision(
                action="retry",
                reason="output_budget_saturated",
                message=(
                    "The previous tool call reached the output budget and had invalid arguments. "
                    "Do not repeat the same payload; split the operation into smaller valid tool calls."
                ),
                **common,
            )

        if context.task_failure_repeat_count == 2:
            return ProgressDecision(
                action="retry",
                reason="repeated_invalid_tool_call",
                message=(
                    "The same invalid tool call failed twice. Change its arguments or use a different "
                    "tool; do not repeat it unchanged."
                ),
                **common,
            )

        return ProgressDecision(**common)

    def _record_planning_activity(self, context, executions) -> None:
        state = getattr(context, "plan_state", None)
        progress = getattr(context, "planning_progress", None)
        if (
            state is None
            or state.phase is not PlanPhase.PLANNING
            or progress is None
            or not state.steps
        ):
            return
        progress.reads_after_draft += sum(
            result.ok and call.name in EVIDENCE_TOOLS | {"read_file"}
            for call, result in executions
        )

    def _evaluate_plan_execution(self, context, executions) -> ProgressDecision:
        state = getattr(context, "plan_state", None)
        progress = getattr(context, "plan_execution_progress", None)
        if progress is None:
            return ProgressDecision()
        if state is None or state.phase is not PlanPhase.EXECUTING:
            progress.reset()
            return ProgressDecision()

        current = next(
            (step for step in state.steps if step.status is PlanStepStatus.IN_PROGRESS),
            None,
        )
        if current is None:
            current = next(
                (step for step in state.steps if step.status is PlanStepStatus.PENDING),
                None,
            )
        if current is None:
            progress.reset()
            return ProgressDecision()

        model_call = int(getattr(context, "task_model_calls", 0))
        mutation_version = int(getattr(context, "mutation_version", 0))
        verification_version = getattr(context, "task_verification_version", None)
        source_metrics = getattr(context, "source_read_metrics", None)
        unique_source_lines = int(getattr(source_metrics, "unique_source_lines_returned", 0))
        current_status = current.status.value

        if progress.step_id != current.id:
            progress.start_step(
                step_id=current.id,
                step_status=current_status,
                model_call=model_call,
                mutation_version=mutation_version,
                verification_version=verification_version,
                unique_source_lines=unique_source_lines,
            )
            self._record_new_evidence(progress, executions)
            return self._reserve_decision(context, progress, current.id) or ProgressDecision()

        new_evidence = self._record_new_evidence(progress, executions)
        made_progress = any(
            (
                current_status != progress.step_status,
                mutation_version != progress.mutation_version,
                verification_version != progress.verification_version,
                unique_source_lines > progress.unique_source_lines,
                new_evidence,
            )
        )
        progress.step_status = current_status
        progress.mutation_version = mutation_version
        progress.verification_version = verification_version
        progress.unique_source_lines = unique_source_lines
        if made_progress:
            progress.last_progress_call = model_call

        reserve_decision = self._reserve_decision(context, progress, current.id)
        if reserve_decision is not None:
            return reserve_decision

        calls_without_progress = max(model_call - progress.last_progress_call, 0)
        threshold = int(getattr(context.config, "plan_step_stall_calls", 4))
        if (
            calls_without_progress >= threshold
            and model_call - progress.last_nudge_call >= threshold
        ):
            progress.last_nudge_call = model_call
            budget = TaskCallBudget.from_context(context)
            return ProgressDecision(
                action="retry",
                reason="plan_step_stalled",
                message=(
                    f"Plan step {current.id} has used {calls_without_progress} model calls "
                    "without a mutation, verification, step transition, or new repository "
                    "evidence. Stop broad exploration. Perform the next concrete action, or "
                    "request replanning if the approved step is blocked. Keep routine plan "
                    "status updates in the same response as substantive tool work."
                ),
                current_step_id=current.id,
                calls_without_progress=calls_without_progress,
                remaining_model_calls=budget.remaining_calls,
                verification_reserve_calls=budget.verification_reserve_calls,
            )
        return ProgressDecision()

    def _reserve_decision(
        self,
        context,
        progress: PlanExecutionProgress,
        step_id: str,
    ) -> ProgressDecision | None:
        budget = TaskCallBudget.from_context(context)
        if not budget.reserve_active or progress.reserve_nudge_emitted:
            return None
        progress.reserve_nudge_emitted = True
        has_mutations = getattr(context, "has_task_mutations", lambda: False)()
        verification_current = getattr(context, "task_verification_version", None) == getattr(
            context, "mutation_version", 0
        )
        if has_mutations and not verification_current:
            instruction = (
                "Run the smallest relevant verification now. Make only corrective edits "
                "required by that check; do not start optional work."
            )
        else:
            instruction = (
                "Stop broad exploration and finalize the highest-value committed work. "
                "Do not start optional plan scope."
            )
        return ProgressDecision(
            action="retry",
            reason="verification_budget_reserve",
            message=(
                "The reserved model-call budget for verification and finalization is now "
                f"active with {budget.remaining_calls} calls remaining. {instruction}"
            ),
            current_step_id=step_id,
            remaining_model_calls=budget.remaining_calls,
            verification_reserve_calls=budget.verification_reserve_calls,
        )

    def _record_new_evidence(self, progress, executions) -> bool:
        found = False
        for call, result in executions:
            if not result.ok or call.name not in EVIDENCE_TOOLS:
                continue
            rendered = json.dumps(
                {
                    "tool": call.name,
                    "arguments": call.arguments,
                    "content": result.content,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            fingerprint = hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]
            if fingerprint in progress.evidence_fingerprints:
                continue
            progress.evidence_fingerprints.append(fingerprint)
            if len(progress.evidence_fingerprints) > MAX_EVIDENCE_FINGERPRINTS:
                progress.evidence_fingerprints.pop(0)
            found = True
        return found

    def _deterministic_failures(self, executions):
        if not executions:
            return None
        if any(result.ok for _, result in executions):
            return None
        if not all(
            result.metadata.get("validation_error") or result.metadata.get("unknown_tool")
            for _, result in executions
        ):
            return None
        return executions

    def _fingerprint(self, failures) -> str:
        value = [
            {
                "tool": call.name,
                "arguments": call.arguments,
                "error": result.error,
            }
            for call, result in failures
        ]
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]

    def _reset_failures(self, context) -> None:
        context.task_failure_fingerprint = None
        context.task_failure_repeat_count = 0
        context.task_saturated_invalid_calls = 0
