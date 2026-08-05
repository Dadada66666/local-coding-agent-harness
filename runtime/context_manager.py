from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass

from runtime.context_budget import (
    ContextMeasurement,
    estimate_messages_tokens,
    measure_context,
)
from runtime.context_checkpoint import RUNTIME_CHECKPOINT_PREFIX, RuntimeCheckpointBuilder
from runtime.tool_result_projection import (
    COMPACTABLE_TOOL_RESULTS,
    PERSISTED_OUTPUT_PREFIX,
    ToolResultProjection,
    ToolResultProjector,
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
PROVIDER_SAVINGS_DISCOUNT = 0.8


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


class ContextManager:
    """Prepare a provider request without mutating the append-only conversation audit."""

    def __init__(
        self,
        summarizer=None,
        *,
        checkpoint_builder: RuntimeCheckpointBuilder | None = None,
        projector: ToolResultProjector | None = None,
    ) -> None:
        self.checkpoint_builder = checkpoint_builder or RuntimeCheckpointBuilder(summarizer)
        self.projector = projector or ToolResultProjector()

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
        system = system if system is not None else getattr(context, "system_prompt", "")
        tools = tools or []
        before = self._measure(context, system, tools, max_output_tokens)
        self._log_measurement(context, before, phase="before")
        projection = self._enforce_tool_round_budget(context)
        current = self._measure(
            context,
            system,
            tools,
            max_output_tokens,
            provider_context_tokens=self._adjusted_provider_anchor(before, projection),
        )

        if not force and not current.should_compact:
            if projection.count:
                self._log_measurement(context, current, phase="after")
            return ContextPreparation(
                measurement=current,
                tool_results_projected=projection.count,
                saved_tokens=max(
                    before.used_tokens - current.used_tokens,
                    projection.saved_tokens,
                ),
            )

        max_failures = int(context.config.max_context_compaction_failures)
        failures = int(getattr(context, "context_compaction_failures", 0))
        if failures >= max_failures:
            context.trace.log(
                {
                    "type": "context_compact_skipped",
                    "reason": "circuit_breaker",
                    "consecutive_failures": failures,
                }
            )
            return ContextPreparation(
                measurement=current,
                tool_results_projected=projection.count,
                saved_tokens=max(
                    before.used_tokens - current.used_tokens,
                    projection.saved_tokens,
                ),
            )

        microcompacted = False
        try:
            microprojection = self._project_consumed_results(context)
            microcompacted = microprojection.count > 0
            after_micro = self._measure(
                context,
                system,
                tools,
                max_output_tokens,
                provider_context_tokens=self._adjusted_provider_anchor(
                    current,
                    microprojection,
                ),
            )
            if not force and not after_micro.should_compact:
                context.context_compaction_failures = 0
                return self._finish_preparation(
                    context,
                    before,
                    current,
                    after_micro,
                    compacted=False,
                    microcompacted=microcompacted,
                    projection=projection,
                    reason=reason or current.trigger_reason or "automatic",
                )

            compacted = self._compact_history(context)
            after = self._measure(context, system, tools, max_output_tokens)
            if compacted or microcompacted:
                context.context_compaction_failures = 0
            elif force or after.should_compact:
                context.context_compaction_failures = failures + 1
            return self._finish_preparation(
                context,
                before,
                current,
                after,
                compacted=compacted,
                microcompacted=microcompacted,
                projection=projection,
                reason=reason or current.trigger_reason or "forced",
            )
        except Exception as exc:
            context.context_compaction_failures = failures + 1
            context.trace.log(
                {
                    "type": "context_compact_error",
                    "reason": reason or current.trigger_reason or "automatic",
                    "exception_type": exc.__class__.__name__,
                    "exception": str(exc)[:500],
                    "consecutive_failures": context.context_compaction_failures,
                }
            )
            current = self._measure(context, system, tools, max_output_tokens)
            return ContextPreparation(
                measurement=current,
                compacted=False,
                microcompacted=microcompacted,
                tool_results_projected=projection.count,
                saved_tokens=max(
                    before.used_tokens - current.used_tokens,
                    projection.saved_tokens,
                ),
            )

    def compact_task_boundary(self, context) -> bool:
        threshold = int(getattr(context.config, "context_task_boundary_tokens", 0))
        if threshold <= 0 or len(context.messages) <= 1:
            return False

        current_message = context.messages[-1]
        old_messages = context.messages[:-1]
        old_tokens = estimate_messages_tokens(old_messages)
        if old_tokens < threshold:
            return False

        checkpoint = self.checkpoint_builder.build(context, old_messages)
        projected = [{"role": "user", "content": checkpoint}, deepcopy(current_message)]
        before_tokens = estimate_messages_tokens(context.messages)
        after_tokens = estimate_messages_tokens(projected)
        if after_tokens >= before_tokens:
            return False

        context.messages = projected
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
            "total_saved_tokens": before_tokens - after_tokens,
            "message_count": len(context.messages),
            "context_generation": getattr(context, "context_generation", 0),
        }
        context.trace.log(event)
        tracker = getattr(context, "cost_tracker", None)
        if tracker is not None and hasattr(tracker, "record_context_event"):
            tracker.record_context_event(event)
        return True

    def _finish_preparation(
        self,
        context,
        before: ContextMeasurement,
        post_projection: ContextMeasurement,
        after: ContextMeasurement,
        *,
        compacted: bool,
        microcompacted: bool,
        projection: ToolResultProjection,
        reason: str,
    ) -> ContextPreparation:
        saved_tokens = max(
            before.used_tokens - after.used_tokens,
            projection.saved_tokens,
        )
        self._log_measurement(context, after, phase="after")
        if compacted or microcompacted:
            compaction_saved_tokens = max(
                post_projection.used_tokens - after.used_tokens,
                0,
            )
            event = {
                "type": "context_compact",
                "reason": reason,
                "mode": "full" if compacted else "tool_results",
                "before_tokens": before.used_tokens,
                "after_tokens": after.used_tokens,
                "saved_tokens": compaction_saved_tokens,
                "total_saved_tokens": saved_tokens,
                "message_count": len(context.messages),
                "context_generation": getattr(context, "context_generation", 0),
            }
            context.trace.log(event)
            tracker = getattr(context, "cost_tracker", None)
            if tracker is not None and hasattr(tracker, "record_context_event"):
                tracker.record_context_event(event)
        return ContextPreparation(
            measurement=after,
            compacted=compacted,
            microcompacted=microcompacted,
            tool_results_projected=projection.count,
            saved_tokens=saved_tokens,
        )

    def _measure(
        self,
        context,
        system: str,
        tools: list[dict],
        max_output_tokens: int,
        provider_context_tokens: int | None = None,
    ) -> ContextMeasurement:
        provider_anchor = provider_context_tokens
        if provider_anchor is None:
            provider_anchor = self._provider_context_anchor(context)
        return measure_context(
            system=system,
            messages=context.messages,
            tools=tools,
            context_window_tokens=context.config.context_window_tokens,
            target_tokens=context.config.context_target_tokens,
            max_output_tokens=max_output_tokens,
            safety_margin_tokens=context.config.context_safety_margin_tokens,
            soft_limit_ratio=context.config.context_soft_limit_ratio,
            provider_context_tokens=provider_anchor,
            fallback_char_limit=context.config.compact_threshold_chars,
        )

    def _adjusted_provider_anchor(
        self,
        measurement: ContextMeasurement,
        projection: ToolResultProjection,
    ) -> int | None:
        if measurement.provider_tokens is None or projection.count <= 0:
            return measurement.provider_tokens
        conservative_savings = int(projection.saved_tokens * PROVIDER_SAVINGS_DISCOUNT)
        return max(measurement.provider_tokens - conservative_savings, 0)

    def _provider_context_anchor(self, context) -> int | None:
        usage = getattr(context, "last_model_usage", None)
        response_index = getattr(context, "last_model_usage_message_index", None)
        generation = getattr(context, "last_model_usage_generation", None)
        if usage is None or response_index is None:
            return None
        if generation != getattr(context, "context_generation", None):
            return None
        if response_index < 0 or response_index >= len(context.messages):
            return None

        logical_input = (
            (getattr(usage, "input_tokens", 0) or 0)
            + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
            + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        )
        response_output = getattr(usage, "output_tokens", 0) or 0
        appended = context.messages[response_index + 1 :]
        return logical_input + response_output + estimate_messages_tokens(appended)

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

    def _compact_history(self, context) -> bool:
        groups = self._group_messages_by_api_round(context.messages)
        if len(groups) <= 1:
            context.trace.log(
                {
                    "type": "context_compact_skipped",
                    "reason": "no_safe_prefix",
                    "message_count": len(context.messages),
                }
            )
            return False

        recent_groups = self._select_recent_groups(groups, context.config)
        if not recent_groups:
            return False
        recent_start = recent_groups[0].start
        if recent_start <= 0:
            context.trace.log(
                {
                    "type": "context_compact_skipped",
                    "reason": "recent_suffix_covers_all_messages",
                    "message_count": len(context.messages),
                }
            )
            return False

        old_messages = context.messages[:recent_start]
        recent_messages = deepcopy(context.messages[recent_start:])
        checkpoint = self.checkpoint_builder.build(context, old_messages)
        context.messages = [
            {"role": "user", "content": checkpoint},
            *recent_messages,
        ]
        context.context_compactions = int(getattr(context, "context_compactions", 0)) + 1
        context.last_model_consumed_message_count = 0
        self._mark_context_changed(context)
        context.trace.log(
            {
                "type": "context_boundary",
                "compaction": context.context_compactions,
                "old_message_count": len(old_messages),
                "recent_message_count": len(recent_messages),
                "recent_round_count": len(recent_groups),
                "recent_tokens": sum(group.tokens for group in recent_groups),
            }
        )
        return True

    def _select_recent_groups(self, groups: list[MessageGroup], config) -> list[MessageGroup]:
        selected: list[MessageGroup] = []
        selected_tokens = 0
        for group in reversed(groups):
            minimum_met = len(selected) >= config.context_min_recent_rounds
            target_met = selected_tokens >= config.context_recent_target_tokens
            would_exceed_max = (
                selected
                and selected_tokens + group.tokens > config.context_recent_max_tokens
            )
            if minimum_met and (target_met or would_exceed_max):
                break
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

        groups = []
        for start, end in zip(boundaries, boundaries[1:]):
            grouped = messages[start:end]
            if grouped:
                groups.append(
                    MessageGroup(
                        start=start,
                        end=end,
                        messages=grouped,
                        tokens=estimate_messages_tokens(grouped),
                    )
                )
        return groups

    def _microcompact_consumed_results(self, context) -> bool:
        return self._project_consumed_results(context).count > 0

    def _project_consumed_results(self, context) -> ToolResultProjection:
        consumed_count = min(
            max(int(getattr(context, "last_model_consumed_message_count", 0)), 0),
            len(context.messages),
        )
        if consumed_count <= 0:
            return ToolResultProjection()

        groups = self._group_messages_by_api_round(context.messages)
        recent_groups = self._select_recent_groups(groups, context.config)
        recent_start = recent_groups[0].start if recent_groups else len(context.messages)
        compact_before = min(consumed_count, recent_start)
        return self.projector.compact_consumed_results(
            context,
            compact_before=compact_before,
        )

    # These small delegates preserve the existing ContextManager extension surface.
    def _enforce_tool_round_budget(self, context) -> ToolResultProjection:
        return self.projector.enforce_round_budget(context)

    def _persist_tool_result(self, context, tool_use_id: str, content: str) -> str | None:
        return self.projector.persist_tool_result(context, tool_use_id, content)

    def _split_leading_orphan_tool_results(
        self,
        messages: list[dict],
    ) -> tuple[list[dict], list[dict]]:
        remaining = list(messages)
        orphan_tool_results = []
        while remaining and self._is_tool_result_message(remaining[0]):
            orphan_tool_results.append(remaining.pop(0))
        return orphan_tool_results, remaining

    def _is_tool_result_message(self, message: dict) -> bool:
        content = message.get("content")
        return (
            isinstance(content, list)
            and bool(content)
            and all(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for block in content
            )
        )

    def _head_tail(self, text: str, max_chars: int) -> str:
        return self.projector.head_tail(text, max_chars)

    def _mark_context_changed(self, context) -> None:
        self.projector.mark_context_changed(context)
