from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass

from runtime.context.budget import (
    ContextMeasurement,
    estimate_messages_tokens,
    estimate_request_tokens,
    measure_context,
    normalize_provider_context_anchor,
)
from runtime.context.checkpoint import RUNTIME_CHECKPOINT_PREFIX, RuntimeCheckpointBuilder
from runtime.context.projection import (
    COMPACTABLE_TOOL_RESULTS,
    PERSISTED_OUTPUT_PREFIX,
    ToolResultProjection,
    ToolResultProjector,
    ToolResultRebaseCandidate,
)


__all__ = [
    "COMPACTABLE_TOOL_RESULTS",
    "ContextManager",
    "ContextPreparation",
    "MessageGroup",
    "PERSISTED_OUTPUT_PREFIX",
    "RUNTIME_CHECKPOINT_PREFIX",
    "ToolResultProjection",
]

# CMV2-TRG-03 calibration hypotheses. They are intentionally not RunConfig fields.
POST_REBASE_CEILING_RATIO = 0.65
MINIMUM_REBASE_GAIN_RATIO = 0.50


@dataclass(frozen=True)
class ContextPreparation:
    measurement: ContextMeasurement
    compacted: bool = False
    microcompacted: bool = False
    tool_results_projected: int = 0
    saved_tokens: int = 0

    @property
    def changed(self) -> bool:
        return self.compacted or self.microcompacted or self.tool_results_projected > 0


@dataclass(frozen=True)
class MessageGroup:
    start: int
    end: int
    messages: list[dict]
    tokens: int


@dataclass(frozen=True)
class HistoryRebaseCandidate:
    messages: list[dict]
    old_messages: list[dict]
    recent_group_count: int
    recent_tokens: int


class ContextManager:
    """Apply CMV2 admission shaping and pressure-driven atomic history rebases."""

    def __init__(
        self,
        *,
        checkpoint_builder: RuntimeCheckpointBuilder | None = None,
        projector: ToolResultProjector | None = None,
    ) -> None:
        self.checkpoint_builder = checkpoint_builder or RuntimeCheckpointBuilder()
        self.projector = projector or ToolResultProjector()

    def admit_tool_results(
        self,
        context,
        tool_calls: list,
        results: list[tuple[str, str, bool]],
    ) -> list[tuple[str, str, bool]]:
        """Bound a new result batch before it first enters Hot Context (CMV2-ADM)."""
        admitted, _projection = self.projector.admit_tool_results(
            context,
            tool_calls,
            results,
        )
        return admitted

    def prepare_context(
        self,
        context,
        *,
        system: str | None = None,
        tools: list[dict] | None = None,
        max_output_tokens: int = 4096,
        force: bool = False,
        reason: str | None = None,
    ) -> ContextPreparation:
        """Prepare one request while preserving append-only normal epochs.

        Below real pressure this method is observational only. At pressure it
        preflights projection and full-rebase candidates from the same original
        history, then commits at most one accepted candidate (CMV2-INV-01/03/05).
        """
        system = system if system is not None else getattr(context, "system_prompt", "")
        tools = tools or []
        before = self._measure(context, system, tools, max_output_tokens)
        self._log_measurement(context, before, phase="before")
        if not force and not before.should_compact:
            return ContextPreparation(measurement=before)

        failures = int(getattr(context, "context_compaction_failures", 0))
        max_failures = int(context.config.max_context_compaction_failures)
        if failures >= max_failures:
            context.trace.log(
                {
                    "type": "context_compact_skipped",
                    "reason": "circuit_breaker",
                    "consecutive_failures": failures,
                }
            )
            return ContextPreparation(measurement=before)

        pressure_reason = reason or before.trigger_reason or ("forced" if force else "automatic")
        hard_pressure = force or (
            before.hard_limit_tokens is not None and before.used_tokens >= before.hard_limit_tokens
        )
        local_before = before.estimated_tokens
        ceiling = None
        minimum_gain = None
        if not hard_pressure:
            pressure_limit = before.soft_limit_tokens or local_before
            ceiling = int(pressure_limit * POST_REBASE_CEILING_RATIO)
            minimum_gain = int(local_before * MINIMUM_REBASE_GAIN_RATIO)

        try:
            if not hard_pressure:
                projected = self._build_projection_candidate(
                    context,
                    system=system,
                    tools=tools,
                    ceiling_tokens=ceiling,
                    minimum_gain_tokens=minimum_gain,
                )
                if projected is not None:
                    return self._commit_projection(
                        context,
                        projected,
                        before=before,
                        system=system,
                        tools=tools,
                        max_output_tokens=max_output_tokens,
                        reason=pressure_reason,
                    )

            full = self._build_history_rebase_candidate(context)
            if full is not None and self._candidate_is_acceptable(
                system,
                tools,
                local_before=local_before,
                candidate_messages=full.messages,
                ceiling_tokens=ceiling,
                minimum_gain_tokens=minimum_gain,
                hard_pressure=hard_pressure,
            ):
                return self._commit_history_rebase(
                    context,
                    full,
                    before=before,
                    system=system,
                    tools=tools,
                    max_output_tokens=max_output_tokens,
                    reason=pressure_reason,
                )

            context.context_compaction_failures = failures + 1
            context.trace.log(
                {
                    "type": "context_compact_skipped",
                    "reason": "no_acceptable_rebase",
                    "before_tokens": local_before,
                    "hard_pressure": hard_pressure,
                }
            )
        except Exception as exc:
            context.context_compaction_failures = failures + 1
            context.trace.log(
                {
                    "type": "context_compact_error",
                    "reason": pressure_reason,
                    "exception_type": exc.__class__.__name__,
                    "exception": str(exc)[:500],
                    "consecutive_failures": context.context_compaction_failures,
                }
            )

        after = self._measure(context, system, tools, max_output_tokens)
        return ContextPreparation(measurement=after)

    def compact_task_boundary(self, context) -> bool:
        """Keep the existing task-boundary checkpoint separate from pressure policy."""
        threshold = int(getattr(context.config, "context_task_boundary_tokens", 0))
        if threshold <= 0 or len(context.messages) <= 1:
            return False
        current_message = context.messages[-1]
        old_messages = context.messages[:-1]
        if estimate_messages_tokens(old_messages) < threshold:
            return False

        projected = [
            {"role": "user", "content": self.checkpoint_builder.build(context, old_messages)},
            deepcopy(current_message),
        ]
        before_tokens = estimate_messages_tokens(context.messages)
        after_tokens = estimate_messages_tokens(projected)
        if after_tokens >= before_tokens:
            return False

        observations = self.projector.source_observations(context, old_messages)
        context.messages = projected
        self.projector.mark_projected_sources(context, observations)
        context.context_compactions = int(getattr(context, "context_compactions", 0)) + 1
        context.last_model_consumed_message_count = 0
        self._mark_context_changed(context)
        event = {
            "type": "context_compact",
            "reason": "task_boundary",
            "mode": "task_boundary",
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "saved_tokens": before_tokens - after_tokens,
            "message_count": len(context.messages),
            "context_generation": getattr(context, "context_generation", 0),
        }
        self._record_event(context, event)
        return True

    def _build_projection_candidate(
        self,
        context,
        *,
        system: str,
        tools: list[dict],
        ceiling_tokens: int | None,
        minimum_gain_tokens: int | None,
    ) -> ToolResultRebaseCandidate | None:
        if ceiling_tokens is None or minimum_gain_tokens is None:
            return None
        consumed_count = min(
            max(int(getattr(context, "last_model_consumed_message_count", 0)), 0),
            len(context.messages),
        )
        if consumed_count <= 0:
            return None

        groups = self._group_messages_by_api_round(context.messages)
        recent = self._select_recent_groups(groups, context.config)
        recent_start = recent[0].start if recent else len(context.messages)
        eligible_end = min(consumed_count, recent_start)
        eligible = [group for group in groups if group.end <= eligible_end]
        if not eligible:
            return None

        static_tokens = estimate_request_tokens(system, [], tools)
        local_before = estimate_request_tokens(system, context.messages, tools)
        request_target = min(ceiling_tokens, local_before - minimum_gain_tokens)
        message_target = max(request_target - static_tokens, 0)
        return self.projector.build_consumed_rebase_candidate(
            context,
            group_ranges=[(group.start, group.end) for group in reversed(eligible)],
            target_message_tokens=message_target,
        )

    def _candidate_is_acceptable(
        self,
        system: str,
        tools: list[dict],
        *,
        local_before: int,
        candidate_messages: list[dict],
        ceiling_tokens: int | None,
        minimum_gain_tokens: int | None,
        hard_pressure: bool,
    ) -> bool:
        local_after = estimate_request_tokens(system, candidate_messages, tools)
        if local_after >= local_before:
            return False
        if hard_pressure:
            return True
        return bool(
            ceiling_tokens is not None
            and minimum_gain_tokens is not None
            and local_after <= ceiling_tokens
            and local_before - local_after >= minimum_gain_tokens
        )

    def _commit_projection(
        self,
        context,
        candidate: ToolResultRebaseCandidate,
        *,
        before: ContextMeasurement,
        system: str,
        tools: list[dict],
        max_output_tokens: int,
        reason: str,
    ) -> ContextPreparation:
        local_before = estimate_request_tokens(system, context.messages, tools)
        local_after = estimate_request_tokens(system, candidate.messages, tools)
        if local_after >= local_before:
            return ContextPreparation(measurement=before)
        context.messages = candidate.messages
        self.projector.mark_projected_sources(context, candidate.projected_sources)
        self._mark_context_changed(context)
        context.context_compaction_failures = 0
        event = {
            "type": "context_tool_results_projected",
            "reason": reason,
            "mode": "tool_results",
            "projected_results": candidate.projection.count,
            "before_tokens": local_before,
            "after_tokens": local_after,
            "saved_tokens": local_before - local_after,
            "message_count": len(context.messages),
            "context_generation": getattr(context, "context_generation", 0),
        }
        self._record_event(context, event)
        after = self._measure(context, system, tools, max_output_tokens)
        self._log_measurement(context, after, phase="after")
        return ContextPreparation(
            measurement=after,
            microcompacted=True,
            tool_results_projected=candidate.projection.count,
            saved_tokens=local_before - local_after,
        )

    def _build_history_rebase_candidate(self, context) -> HistoryRebaseCandidate | None:
        groups = self._group_messages_by_api_round(context.messages)
        if len(groups) <= 1:
            return None
        recent = self._select_recent_groups(groups, context.config)
        if not recent or recent[0].start <= 0:
            return None
        old_messages = context.messages[: recent[0].start]
        if len(old_messages) == 1 and self._is_runtime_checkpoint_message(old_messages[0]):
            return None
        recent_messages = deepcopy(context.messages[recent[0].start :])
        checkpoint = self.checkpoint_builder.build(context, old_messages)
        return HistoryRebaseCandidate(
            messages=[{"role": "user", "content": checkpoint}, *recent_messages],
            old_messages=old_messages,
            recent_group_count=len(recent),
            recent_tokens=sum(group.tokens for group in recent),
        )

    def _commit_history_rebase(
        self,
        context,
        candidate: HistoryRebaseCandidate,
        *,
        before: ContextMeasurement,
        system: str,
        tools: list[dict],
        max_output_tokens: int,
        reason: str,
    ) -> ContextPreparation:
        local_before = estimate_request_tokens(system, context.messages, tools)
        local_after = estimate_request_tokens(system, candidate.messages, tools)
        if local_after >= local_before:
            return ContextPreparation(measurement=before)
        observations = self.projector.source_observations(context, candidate.old_messages)
        context.messages = candidate.messages
        self.projector.mark_projected_sources(context, observations)
        context.context_compactions = int(getattr(context, "context_compactions", 0)) + 1
        context.last_model_consumed_message_count = 0
        self._mark_context_changed(context)
        context.context_compaction_failures = 0
        context.trace.log(
            {
                "type": "context_boundary",
                "compaction": context.context_compactions,
                "old_message_count": len(candidate.old_messages),
                "recent_message_count": len(context.messages) - 1,
                "recent_round_count": candidate.recent_group_count,
                "recent_tokens": candidate.recent_tokens,
            }
        )
        event = {
            "type": "context_compact",
            "reason": reason,
            "mode": "full",
            "before_tokens": local_before,
            "after_tokens": local_after,
            "saved_tokens": local_before - local_after,
            "message_count": len(context.messages),
            "context_generation": getattr(context, "context_generation", 0),
        }
        self._record_event(context, event)
        after = self._measure(context, system, tools, max_output_tokens)
        self._log_measurement(context, after, phase="after")
        return ContextPreparation(
            measurement=after,
            compacted=True,
            saved_tokens=local_before - local_after,
        )

    def _measure(
        self,
        context,
        system: str,
        tools: list[dict],
        max_output_tokens: int,
    ) -> ContextMeasurement:
        local_estimate = estimate_request_tokens(system, context.messages, tools)
        return measure_context(
            system=system,
            messages=context.messages,
            tools=tools,
            context_window_tokens=context.config.context_window_tokens,
            target_tokens=context.config.context_target_tokens,
            max_output_tokens=max_output_tokens,
            safety_margin_tokens=context.config.context_safety_margin_tokens,
            soft_limit_ratio=context.config.context_soft_limit_ratio,
            provider_context_tokens=self._provider_context_anchor(
                context,
                local_estimate=local_estimate,
            ),
            fallback_char_limit=context.config.compact_threshold_chars,
        )

    def _provider_context_anchor(self, context, *, local_estimate: int) -> int | None:
        usage = getattr(context, "last_model_usage", None)
        response_index = getattr(context, "last_model_usage_message_index", None)
        generation = getattr(context, "last_model_usage_generation", None)
        if usage is None or response_index is None:
            return None
        if generation != getattr(context, "context_generation", None):
            return None
        if response_index < 0 or response_index >= len(context.messages):
            return None
        appended = context.messages[response_index + 1 :]
        return normalize_provider_context_anchor(
            local_estimate=local_estimate,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            appended_tokens=estimate_messages_tokens(appended) if appended else 0,
        )

    def _select_recent_groups(self, groups: list[MessageGroup], config) -> list[MessageGroup]:
        """Select the minimum raw tail satisfying rounds and target, never filler."""
        selected: list[MessageGroup] = []
        selected_tokens = 0
        target = int(config.context_recent_target_tokens)
        maximum = int(config.context_recent_max_tokens)
        minimum_rounds = int(config.context_min_recent_rounds)
        for group in reversed(groups):
            if len(selected) >= minimum_rounds and selected_tokens >= target:
                break
            if selected_tokens + group.tokens > maximum:
                return []
            selected.append(group)
            selected_tokens += group.tokens
        selected.reverse()
        return selected

    def _group_messages_by_api_round(self, messages: list[dict]) -> list[MessageGroup]:
        if not messages:
            return []
        boundaries = [0]
        for index, message in enumerate(messages):
            if index > 0 and message.get("role") == "assistant":
                boundaries.append(index)
        boundaries.append(len(messages))
        return [
            MessageGroup(
                start=start,
                end=end,
                messages=messages[start:end],
                tokens=estimate_messages_tokens(messages[start:end]),
            )
            for start, end in zip(boundaries, boundaries[1:])
            if messages[start:end]
        ]

    def _is_runtime_checkpoint_message(self, message: dict) -> bool:
        content = message.get("content")
        return bool(
            message.get("role") == "user"
            and isinstance(content, str)
            and content.startswith(RUNTIME_CHECKPOINT_PREFIX)
        )

    def _log_measurement(self, context, measurement: ContextMeasurement, *, phase: str) -> None:
        context.trace.log(
            {
                "type": "context_measurement",
                "phase": phase,
                **asdict(measurement),
                "message_count": len(context.messages),
                "context_generation": getattr(context, "context_generation", 0),
            }
        )

    def _record_event(self, context, event: dict) -> None:
        context.trace.log(event)
        tracker = getattr(context, "cost_tracker", None)
        if tracker is not None and hasattr(tracker, "record_context_event"):
            tracker.record_context_event(event)

    def _persist_tool_result(self, context, tool_use_id: str, content: str) -> str | None:
        return self.projector.persist_tool_result(context, tool_use_id, content)

    def _head_tail(self, text: str, max_chars: int) -> str:
        return self.projector.head_tail(text, max_chars)

    def _mark_context_changed(self, context) -> None:
        self.projector.mark_context_changed(context)
