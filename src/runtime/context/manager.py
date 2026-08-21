from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any

from runtime.context.budget import (
    ContextMeasurement,
    estimate_input_tokens,
    estimate_messages_tokens,
    estimate_text_tokens,
    normalize_provider_context_anchor,
    measure_context,
)
from runtime.context.checkpoint import (
    COMBINED_CHECKPOINT_MAX_TOKENS,
    AuthoritativeState,
    RemovedTrajectoryItem,
    RuntimeCheckpointBuilder,
)
from runtime.context.projection import ToolResultProjector


EMERGENCY_DETERMINISTIC_RESERVE = 4_096
EMERGENCY_SEMANTIC_PAYLOAD = (
    "Unavailable after hard-pressure compaction failure; recover removed "
    "trajectory using the authoritative History ranges."
)


@dataclass(frozen=True)
class ContextPreparation:
    measurement: ContextMeasurement
    compacted: bool = False
    saved_tokens: int = 0
    failure_reason: str | None = None

    @property
    def changed(self) -> bool:
        return self.compacted


@dataclass(frozen=True)
class MessageGroup:
    start: int
    end: int
    messages: list[dict[str, Any]]
    audit_ordinals: list[int | None]
    tokens: int
    complete: bool


@dataclass(frozen=True)
class ContextSnapshot:
    messages: list[dict[str, Any]]
    audit_ordinals: list[int | None]
    local_input_tokens: int
    hard_input_limit_tokens: int
    generation: int


@dataclass(frozen=True)
class FullRebaseCandidate:
    messages: list[dict[str, Any]]
    audit_ordinals: list[int | None]
    removed_messages: list[dict[str, Any]]
    final_raw_tokens: int
    final_raw_rounds: int
    deterministic_tokens: int
    wrapper_tokens: int
    semantic_tokens: int
    checkpoint_tokens: int
    local_input_tokens_after: int
    emergency_fallback: bool


class RebaseFailure(RuntimeError):
    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason


class ContextManager:
    """CMV3 admission and one pressure-driven atomic Full Rebase policy."""

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
        max_output_tokens: int = 16_000,
        force: bool = False,
        reason: str | None = None,
        model_client=None,
    ) -> ContextPreparation:
        system = system if system is not None else getattr(context, "system_prompt", "")
        tools = tools or []
        before = self._measure(context, system, tools, max_output_tokens)
        self._log_measurement(context, before, phase="before")
        if not force and not before.should_rebase:
            return ContextPreparation(measurement=before)

        overflow = reason in {"context_overflow", "provider_overflow"}
        hard_pressure = overflow or before.hard_pressure
        explicit = force and not overflow and reason not in {"hard", "hard_pressure"}
        event_reason = (
            "provider_overflow"
            if overflow
            else "hard"
            if hard_pressure
            else "explicit"
            if explicit
            else "auto"
        )
        generation = int(getattr(context, "context_generation", 0))
        if (
            not hard_pressure
            and not explicit
            and getattr(context, "last_auto_compaction_failed_generation", None) == generation
        ):
            return ContextPreparation(measurement=before)

        snapshot = self._snapshot(
            context,
            before.local_input_tokens,
            before.hard_input_limit_tokens,
        )
        failure: RebaseFailure | None = None
        candidate: FullRebaseCandidate | None = None
        try:
            candidate = self._build_semantic_candidate(
                context,
                snapshot=snapshot,
                system=system,
                tools=tools,
                model_client=model_client,
            )
        except RebaseFailure as exc:
            failure = exc
        except Exception as exc:
            failure = RebaseFailure("checkpoint_construction", str(exc))

        if candidate is None and hard_pressure:
            self._log_failure(
                context,
                reason=event_reason,
                failure=failure or RebaseFailure("no_candidate", "no semantic candidate"),
                guard_bypassed=True,
                auto_suppressed=False,
            )
            try:
                candidate = self._build_emergency_candidate(
                    context,
                    snapshot=snapshot,
                    system=system,
                    tools=tools,
                )
            except RebaseFailure as exc:
                failure = exc
            except Exception as exc:
                failure = RebaseFailure("emergency_construction", str(exc))

        if candidate is None:
            if not hard_pressure and not explicit:
                context.last_auto_compaction_failed_generation = generation
            self._log_failure(
                context,
                reason=event_reason,
                failure=failure or RebaseFailure("no_candidate", "no rebase candidate"),
                guard_bypassed=hard_pressure or explicit,
                auto_suppressed=(not hard_pressure and not explicit),
            )
            return ContextPreparation(
                measurement=before,
                failure_reason=(failure.reason if failure is not None else "no_candidate"),
            )

        return self._commit_candidate(
            context,
            candidate,
            before=before,
            system=system,
            tools=tools,
            max_output_tokens=max_output_tokens,
            reason=event_reason,
            guard_bypassed=hard_pressure or explicit,
        )

    def _build_semantic_candidate(
        self,
        context,
        *,
        snapshot: ContextSnapshot,
        system: str,
        tools: list[dict],
        model_client,
    ) -> FullRebaseCandidate:
        target_generation = snapshot.generation + 1
        deterministic = self._authoritative_state(
            context,
            context_generation=target_generation,
            current_window_id=context.history_window_id(target_generation),
        )
        wrapper_tokens = self.checkpoint_builder.checkpoint_wrapper_tokens()
        semantic_actual_max = self.checkpoint_builder.semantic_actual_max(
            context,
            deterministic,
        )
        if semantic_actual_max < self.checkpoint_builder.mandatory_semantic_skeleton_tokens():
            raise RebaseFailure(
                "shared_checkpoint_overflow",
                "shared checkpoint budget cannot fit the mandatory semantic skeleton",
            )

        static_input_tokens = estimate_input_tokens(system, [], tools)
        raw_capacity = (
            int(context.config.context_post_rebase_ceiling_tokens)
            - static_input_tokens
            - deterministic.actual_tokens
            - wrapper_tokens
            - semantic_actual_max
        )
        raw_budget = max(
            0,
            min(int(context.config.context_recent_raw_tokens), raw_capacity),
        )
        final_groups, removed_items = self._select_final_raw(snapshot, raw_budget)
        self._require_meaningful_removal(removed_items)
        previous_handoff = next(
            (
                self.checkpoint_builder.extract_semantic_handoff(item.message)
                for item in removed_items
                if self.checkpoint_builder.is_checkpoint_message(item.message)
            ),
            None,
        )
        semantic_messages = self.checkpoint_builder.build_semantic_input(
            context,
            removed_items=removed_items,
            previous_semantic_handoff=previous_handoff,
            authoritative_state=deterministic,
        )
        semantic_system = self.checkpoint_builder.semantic_system_prompt()
        semantic_input_tokens = estimate_input_tokens(
            semantic_system,
            semantic_messages,
            [],
        )
        semantic_input_limit = (
            int(context.config.context_window_tokens)
            - semantic_actual_max
            - int(context.config.context_safety_margin_tokens)
        )
        if semantic_input_tokens > semantic_input_limit:
            raise RebaseFailure(
                "semantic_preflight",
                "semantic compaction input exceeds its safe input limit",
            )
        if model_client is None:
            raise RebaseFailure("provider_call", "semantic model client is unavailable")
        try:
            response = model_client.call(
                system=semantic_system,
                messages=semantic_messages,
                tools=[],
                max_tokens=semantic_actual_max,
            )
        except Exception as exc:
            raise RebaseFailure("provider_call", str(exc)) from exc
        self._record_semantic_usage(
            context,
            response,
            semantic_input_tokens=semantic_input_tokens,
            semantic_actual_max=semantic_actual_max,
        )
        try:
            semantic = self.checkpoint_builder.validate_semantic_output(
                getattr(response, "text", ""),
                max_tokens=semantic_actual_max,
            )
        except (TypeError, ValueError) as exc:
            raise RebaseFailure("malformed_output", str(exc)) from exc
        return self._assemble_candidate(
            context,
            snapshot=snapshot,
            system=system,
            tools=tools,
            deterministic=deterministic,
            semantic=semantic,
            final_groups=final_groups,
            removed_items=removed_items,
            wrapper_tokens=wrapper_tokens,
            emergency=False,
        )

    def _build_emergency_candidate(
        self,
        context,
        *,
        snapshot: ContextSnapshot,
        system: str,
        tools: list[dict],
    ) -> FullRebaseCandidate:
        static_input_tokens = estimate_input_tokens(system, [], tools)
        wrapper_tokens = self.checkpoint_builder.checkpoint_wrapper_tokens()
        fixed_payload_tokens = estimate_text_tokens(EMERGENCY_SEMANTIC_PAYLOAD)
        raw_capacity = (
            int(context.config.context_post_rebase_ceiling_tokens)
            - static_input_tokens
            - EMERGENCY_DETERMINISTIC_RESERVE
            - wrapper_tokens
            - fixed_payload_tokens
        )
        raw_budget = max(
            0,
            min(int(context.config.context_recent_raw_tokens), raw_capacity),
        )
        final_groups, removed_items = self._select_final_raw(snapshot, raw_budget)
        self._require_meaningful_removal(removed_items)
        removed_ordinals = [
            item.audit_ordinal for item in removed_items if item.audit_ordinal is not None
        ]
        ranges = context.history_ranges_for_ordinals(removed_ordinals)
        try:
            target_generation = snapshot.generation + 1
            deterministic = self.checkpoint_builder.build_authoritative_state(
                context,
                required_removed_history_ranges=ranges,
                context_generation=target_generation,
                current_window_id=context.history_window_id(target_generation),
                max_tokens=EMERGENCY_DETERMINISTIC_RESERVE,
            )
        except ValueError as exc:
            raise RebaseFailure("emergency_recovery_state", str(exc)) from exc
        if deterministic.actual_tokens > EMERGENCY_DETERMINISTIC_RESERVE:
            raise RebaseFailure(
                "emergency_recovery_state",
                "deterministic emergency state exceeds its reserve",
            )
        recorded = deterministic.payload.get("history_recovery", {}).get("removed_ranges")
        if recorded != ranges:
            raise RebaseFailure(
                "emergency_recovery_state",
                "deterministic emergency state does not cover exact removed ranges",
            )
        return self._assemble_candidate(
            context,
            snapshot=snapshot,
            system=system,
            tools=tools,
            deterministic=deterministic,
            semantic=EMERGENCY_SEMANTIC_PAYLOAD,
            final_groups=final_groups,
            removed_items=removed_items,
            wrapper_tokens=wrapper_tokens,
            emergency=True,
        )

    def _assemble_candidate(
        self,
        context,
        *,
        snapshot: ContextSnapshot,
        system: str,
        tools: list[dict],
        deterministic: AuthoritativeState,
        semantic: str,
        final_groups: list[MessageGroup],
        removed_items: list[RemovedTrajectoryItem],
        wrapper_tokens: int,
        emergency: bool,
    ) -> FullRebaseCandidate:
        try:
            checkpoint = self.checkpoint_builder.serialize_checkpoint(
                deterministic.serialized,
                semantic,
            )
        except Exception as exc:
            raise RebaseFailure("shared_checkpoint_overflow", str(exc)) from exc
        checkpoint_tokens = estimate_text_tokens(checkpoint)
        if checkpoint_tokens > COMBINED_CHECKPOINT_MAX_TOKENS:
            raise RebaseFailure(
                "shared_checkpoint_overflow",
                "serialized hybrid checkpoint exceeds 12288 tokens",
            )
        raw_messages = [deepcopy(message) for group in final_groups for message in group.messages]
        raw_ordinals = [ordinal for group in final_groups for ordinal in group.audit_ordinals]
        candidate_messages = [
            {"role": "user", "content": checkpoint},
            *raw_messages,
        ]
        candidate_tokens = estimate_input_tokens(system, candidate_messages, tools)
        if candidate_tokens > int(context.config.context_post_rebase_ceiling_tokens):
            raise RebaseFailure(
                "final_candidate_rejection",
                "candidate exceeds the post-rebase input ceiling",
            )
        if candidate_tokens >= snapshot.local_input_tokens:
            raise RebaseFailure(
                "final_candidate_rejection",
                "candidate does not strictly reduce local input tokens",
            )
        if candidate_tokens >= snapshot.hard_input_limit_tokens:
            raise RebaseFailure(
                "final_candidate_rejection",
                "candidate does not fit the applicable hard input limit",
            )
        if not self._protocol_is_complete(candidate_messages):
            raise RebaseFailure(
                "final_candidate_rejection",
                "candidate contains an incomplete tool protocol round",
            )
        return FullRebaseCandidate(
            messages=candidate_messages,
            audit_ordinals=[None, *raw_ordinals],
            removed_messages=[item.message for item in removed_items],
            final_raw_tokens=sum(group.tokens for group in final_groups),
            final_raw_rounds=len(final_groups),
            deterministic_tokens=deterministic.actual_tokens,
            wrapper_tokens=wrapper_tokens,
            semantic_tokens=estimate_text_tokens(semantic),
            checkpoint_tokens=checkpoint_tokens,
            local_input_tokens_after=candidate_tokens,
            emergency_fallback=emergency,
        )

    def _commit_candidate(
        self,
        context,
        candidate: FullRebaseCandidate,
        *,
        before: ContextMeasurement,
        system: str,
        tools: list[dict],
        max_output_tokens: int,
        reason: str,
        guard_bypassed: bool,
    ) -> ContextPreparation:
        generation_before = int(context.context_generation)
        observations = self.projector.source_observations(
            context,
            candidate.removed_messages,
        )
        context.messages = candidate.messages
        context.message_audit_ordinals = candidate.audit_ordinals
        self.projector.mark_projected_sources(context, observations)
        context.context_compactions += 1
        context.last_model_consumed_message_count = 0
        context.mark_context_changed()
        event = {
            "type": "context_rebase",
            "reason": reason,
            "generation_before": generation_before,
            "generation_after": context.context_generation,
            "local_input_tokens_before": before.local_input_tokens,
            "local_input_tokens_after": candidate.local_input_tokens_after,
            "saved_tokens": before.local_input_tokens - candidate.local_input_tokens_after,
            "final_raw_input_tokens": candidate.final_raw_tokens,
            "final_raw_rounds": candidate.final_raw_rounds,
            "deterministic_tokens": candidate.deterministic_tokens,
            "wrapper_tokens": candidate.wrapper_tokens,
            "semantic_tokens": candidate.semantic_tokens,
            "final_checkpoint_tokens": candidate.checkpoint_tokens,
            "emergency_fallback": candidate.emergency_fallback,
            "normal_guard_bypassed": guard_bypassed,
        }
        self._record_event(context, event)
        after = self._measure(context, system, tools, max_output_tokens)
        self._log_measurement(context, after, phase="after")
        return ContextPreparation(
            measurement=after,
            compacted=True,
            saved_tokens=event["saved_tokens"],
        )

    def _snapshot(
        self,
        context,
        local_input_tokens: int,
        hard_input_limit_tokens: int,
    ) -> ContextSnapshot:
        messages = deepcopy(context.messages)
        ordinals = list(getattr(context, "message_audit_ordinals", []))
        if len(ordinals) != len(messages):
            ordinals = [None] * len(messages)
        return ContextSnapshot(
            messages=messages,
            audit_ordinals=ordinals,
            local_input_tokens=local_input_tokens,
            hard_input_limit_tokens=hard_input_limit_tokens,
            generation=int(getattr(context, "context_generation", 0)),
        )

    def _select_final_raw(
        self,
        snapshot: ContextSnapshot,
        raw_budget: int,
    ) -> tuple[list[MessageGroup], list[RemovedTrajectoryItem]]:
        groups = self._group_messages_by_api_round(
            snapshot.messages,
            snapshot.audit_ordinals,
        )
        selected: list[MessageGroup] = []
        used = 0
        for group in reversed(groups):
            if any(
                self.checkpoint_builder.is_checkpoint_message(message) for message in group.messages
            ):
                break
            if not group.complete or used + group.tokens > raw_budget:
                break
            selected.append(group)
            used += group.tokens
        selected.reverse()
        retained_indices = {index for group in selected for index in range(group.start, group.end)}
        removed = [
            RemovedTrajectoryItem(
                message=message,
                trajectory_index=index,
                audit_ordinal=snapshot.audit_ordinals[index],
            )
            for index, message in enumerate(snapshot.messages)
            if index not in retained_indices
        ]
        return selected, removed

    def _group_messages_by_api_round(
        self,
        messages: list[dict[str, Any]],
        ordinals: list[int | None],
    ) -> list[MessageGroup]:
        groups: list[MessageGroup] = []
        index = 0
        while index < len(messages):
            start = index
            message = messages[index]
            tool_ids = self._tool_use_ids(message)
            complete = True
            if tool_ids:
                expected = set(tool_ids)
                seen: set[str] = set()
                complete = len(expected) == len(tool_ids)
                index += 1
                while index < len(messages) and seen != expected:
                    result_ids = self._tool_result_ids(messages[index])
                    if not result_ids:
                        complete = False
                        break
                    for tool_use_id in result_ids:
                        if tool_use_id not in expected or tool_use_id in seen:
                            complete = False
                        seen.add(tool_use_id)
                    index += 1
                complete = complete and seen == expected
            else:
                complete = not self._tool_result_ids(message)
                index += 1
            group_messages = messages[start:index]
            groups.append(
                MessageGroup(
                    start=start,
                    end=index,
                    messages=group_messages,
                    audit_ordinals=ordinals[start:index],
                    tokens=estimate_messages_tokens(group_messages),
                    complete=complete,
                )
            )
        return groups

    def _protocol_is_complete(self, messages: list[dict[str, Any]]) -> bool:
        groups = self._group_messages_by_api_round(messages, [None] * len(messages))
        return all(group.complete for group in groups)

    def _tool_use_ids(self, message: dict[str, Any]) -> list[str]:
        content = message.get("content")
        if message.get("role") != "assistant" or not isinstance(content, list):
            return []
        return [
            str(block.get("id"))
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]

    def _tool_result_ids(self, message: dict[str, Any]) -> list[str]:
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            return []
        return [
            str(block.get("tool_use_id"))
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result"
        ]

    def _require_meaningful_removal(
        self,
        removed_items: list[RemovedTrajectoryItem],
    ) -> None:
        if not removed_items or all(
            self.checkpoint_builder.is_checkpoint_message(item.message) for item in removed_items
        ):
            raise RebaseFailure(
                "no_candidate",
                "removed trajectory contains no new trajectory beyond the current checkpoint",
            )

    def _authoritative_state(
        self,
        context,
        *,
        context_generation: int,
        current_window_id: str,
    ) -> AuthoritativeState:
        try:
            return self.checkpoint_builder.build_authoritative_state(
                context,
                context_generation=context_generation,
                current_window_id=current_window_id,
            )
        except ValueError as exc:
            raise RebaseFailure("checkpoint_construction", str(exc)) from exc

    def _measure(
        self,
        context,
        system: str,
        tools: list[dict],
        max_output_tokens: int,
    ) -> ContextMeasurement:
        local_input_tokens = estimate_input_tokens(system, context.messages, tools)
        return measure_context(
            system=system,
            messages=context.messages,
            tools=tools,
            context_window_tokens=context.config.context_window_tokens,
            max_output_tokens=max_output_tokens,
            safety_margin_tokens=context.config.context_safety_margin_tokens,
            auto_compact_ratio=context.config.context_auto_compact_ratio,
            provider_input_tokens=self._provider_context_anchor(
                context,
                current_local_input_tokens=local_input_tokens,
            ),
        )

    def _provider_context_anchor(
        self,
        context,
        *,
        current_local_input_tokens: int,
    ) -> int | None:
        usage = getattr(context, "last_model_usage", None)
        response_index = getattr(context, "last_model_usage_message_index", None)
        generation = getattr(context, "last_model_usage_generation", None)
        matching_local = getattr(
            context,
            "last_model_usage_local_input_tokens",
            None,
        )
        if usage is None or response_index is None or matching_local is None:
            return None
        if generation != getattr(context, "context_generation", None):
            return None
        if response_index < 0 or response_index >= len(context.messages):
            return None
        appended = context.messages[response_index + 1 :]
        normalized = normalize_provider_context_anchor(
            local_input_tokens=matching_local,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            assistant_response_tokens=getattr(usage, "output_tokens", 0) or 0,
            appended_input_tokens=(estimate_messages_tokens(appended) if appended else 0),
        )
        return max(normalized, min(current_local_input_tokens, matching_local))

    def _log_measurement(
        self,
        context,
        measurement: ContextMeasurement,
        *,
        phase: str,
    ) -> None:
        context.trace.log(
            {
                "type": "context_measurement",
                "phase": phase,
                **asdict(measurement),
                "message_count": len(context.messages),
                "context_generation": context.context_generation,
            }
        )

    def _log_failure(
        self,
        context,
        *,
        reason: str,
        failure: RebaseFailure,
        guard_bypassed: bool,
        auto_suppressed: bool,
    ) -> None:
        event = {
            "type": "context_rebase_failure",
            "reason": reason,
            "failure_reason": failure.reason,
            "detail": str(failure)[:500],
            "generation": context.context_generation,
            "auto_suppressed_for_generation": auto_suppressed,
            "normal_guard_bypassed": guard_bypassed,
        }
        self._record_event(context, event)

    def _record_semantic_usage(
        self,
        context,
        response,
        *,
        semantic_input_tokens: int,
        semantic_actual_max: int,
    ) -> None:
        usage = getattr(response, "usage", None)
        context.trace.log(
            {
                "type": "context_semantic_usage",
                "semantic_input_estimate": semantic_input_tokens,
                "semantic_max_output_tokens": semantic_actual_max,
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", None),
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", None),
            }
        )
        recorder = getattr(context.cost_tracker, "record_compaction_call", None)
        if callable(recorder):
            recorder(usage)

    def _record_event(self, context, event: dict[str, Any]) -> None:
        context.trace.log(event)
        tracker = getattr(context, "cost_tracker", None)
        if tracker is not None and hasattr(tracker, "record_context_event"):
            tracker.record_context_event(event)
