from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from runtime.context.budget import estimate_value_tokens
from runtime.observability.text_preview import head_tail_preview


PERSISTED_OUTPUT_PREFIX = "<persisted-output>"
SOURCE_OUTPUT_PREFIX = "[Source observation compacted;"


@dataclass(frozen=True)
class ToolResultProjection:
    count: int = 0
    saved_tokens: int = 0


class ToolResultAdmissionError(RuntimeError):
    """The first-visibility ToolResult batch cannot satisfy its hard budget."""

    def __init__(self, *, estimated_tokens: int, limit_tokens: int) -> None:
        self.estimated_tokens = int(estimated_tokens)
        self.limit_tokens = int(limit_tokens)
        super().__init__(
            "tool-result admission exceeds the hard round budget "
            f"({self.estimated_tokens} > {self.limit_tokens} tokens)"
        )


class ToolResultProjector:
    """Bound new ToolResults before their first provider visibility."""

    def admit_tool_results(
        self,
        context,
        tool_calls: list[Any],
        results: list[tuple[str, str, bool]],
    ) -> tuple[list[tuple[str, str, bool]], ToolResultProjection]:
        """Shape one new ToolResult batch before its first provider visibility.

        Existing messages and the Context generation are deliberately untouched
        (CMV3-ADM-001..009).
        """
        limit = int(context.config.max_tool_round_tokens)
        if not results:
            return list(results), ToolResultProjection()

        names = {
            str(getattr(tool_call, "id", "")): str(getattr(tool_call, "name", "unknown"))
            for tool_call in tool_calls
        }
        provenance = getattr(context, "tool_result_provenance", {})
        blocks = [
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
                **({"is_error": True} if is_error else {}),
            }
            for tool_use_id, content, is_error in results
        ]
        total = sum(estimate_value_tokens(block) for block in blocks)
        if total <= limit:
            return list(results), ToolResultProjection()

        replacement_count = 0
        saved_tokens = 0
        projected: list[tuple[dict, str, str, str | None]] = []
        projected_sources: list[tuple[str, dict[str, Any]]] = []
        ordered_candidates = sorted(
            blocks,
            key=lambda block: self._round_budget_sort_key(block, names),
        )
        source_candidates_started = False
        for block in ordered_candidates:
            if total <= limit:
                break
            tool_use_id = str(block.get("tool_use_id", ""))
            original = str(block.get("content", ""))
            before_tokens = estimate_value_tokens(block)
            tool_name = names.get(tool_use_id, "unknown")
            source = provenance.get(tool_use_id)
            metadata = self._result_metadata(context, tool_use_id)
            source_result = self._is_source_result(tool_name, metadata)
            if source_result and not source_candidates_started:
                total, extra_savings = self._minimize_persisted_previews(
                    projected,
                    total=total,
                    limit=limit,
                )
                saved_tokens += extra_savings
                source_candidates_started = True
                if total <= limit:
                    break

            if source_result:
                replacement = self._source_stub(
                    context,
                    metadata,
                    provenance=source,
                )
                block["content"] = replacement
                after_tokens = estimate_value_tokens(block)
                if after_tokens >= before_tokens:
                    block["content"] = original
                    continue
                projected_sources.append((tool_use_id, metadata))
            else:
                artifact_id = getattr(context, "tool_result_artifacts", {}).get(tool_use_id)
                if original.startswith(PERSISTED_OUTPUT_PREFIX) and not artifact_id:
                    continue
                if artifact_id is None:
                    placeholder = dict(block)
                    placeholder["content"] = self._persisted_stub(
                        "artifact_0000000000000000",
                        tool_name,
                        source,
                    )
                    if estimate_value_tokens(placeholder) >= before_tokens:
                        continue
                    artifact_id = self.persist_tool_result(
                        context,
                        tool_use_id,
                        original,
                    )
                if artifact_id is None:
                    continue
                replacement = (
                    self._persisted_stub(artifact_id, tool_name, source)
                    if original.startswith(PERSISTED_OUTPUT_PREFIX)
                    else self._persisted_preview(
                        original,
                        artifact_id=artifact_id,
                        tool_name=tool_name,
                        provenance=source,
                    )
                )
                block["content"] = replacement
                after_tokens = estimate_value_tokens(block)
                if after_tokens >= before_tokens:
                    block["content"] = self._persisted_stub(
                        artifact_id,
                        tool_name,
                        source,
                    )
                    after_tokens = estimate_value_tokens(block)
                if after_tokens >= before_tokens:
                    block["content"] = original
                    continue
                projected.append((block, artifact_id, tool_name, source))

            reduction = max(before_tokens - after_tokens, 0)
            total -= reduction
            saved_tokens += reduction
            replacement_count += 1

        if total > limit:
            total, extra_savings = self._minimize_persisted_previews(
                projected,
                total=total,
                limit=limit,
            )
            saved_tokens += extra_savings

        budget_satisfied = total <= limit
        if replacement_count:
            self.mark_projected_sources(context, projected_sources)
            event = {
                "type": "tool_result_budget",
                "replaced_results": replacement_count,
                "saved_tokens": saved_tokens,
                "round_limit_tokens": limit,
                "budget_satisfied": budget_satisfied,
            }
            context.trace.log(event)
            tracker = getattr(context, "cost_tracker", None)
            if tracker is not None and hasattr(tracker, "record_context_event"):
                tracker.record_context_event(event)
        if not budget_satisfied:
            raise ToolResultAdmissionError(
                estimated_tokens=total,
                limit_tokens=limit,
            )
        shaped = [
            (
                str(block.get("tool_use_id", "")),
                str(block.get("content", "")),
                bool(block.get("is_error", False)),
            )
            for block in blocks
        ]
        return shaped, ToolResultProjection(
            count=replacement_count,
            saved_tokens=saved_tokens,
        )

    def source_observations(
        self,
        context,
        messages: list[dict],
    ) -> tuple[tuple[str, dict[str, Any]], ...]:
        names = self._tool_names_by_id(context.messages)
        observations: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        for message in messages:
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                tool_use_id = str(block.get("tool_use_id", ""))
                if not tool_use_id or tool_use_id in seen:
                    continue
                metadata = self._result_metadata(context, tool_use_id)
                if self._is_source_result(names.get(tool_use_id, "unknown"), metadata):
                    observations.append((tool_use_id, metadata))
                    seen.add(tool_use_id)
        return tuple(observations)

    def mark_projected_sources(
        self,
        context,
        observations: list[tuple[str, dict[str, Any]]] | tuple[tuple[str, dict[str, Any]], ...],
    ) -> None:
        for tool_use_id, metadata in observations:
            self._mark_source_projected(context, tool_use_id, metadata)

    def persist_tool_result(self, context, tool_use_id: str, content: str) -> str | None:
        artifact_map = getattr(context, "tool_result_artifacts", None)
        if artifact_map is None:
            artifact_map = {}
            context.tool_result_artifacts = artifact_map
        existing = artifact_map.get(tool_use_id)
        if existing and context.artifacts.get(existing) is not None:
            return existing
        try:
            reference = context.artifacts.persist(
                tool_use_id,
                content,
                creation_reason="tool_result_budget",
            )
        except (OSError, UnicodeError) as exc:
            context.trace.log(
                {
                    "type": "artifact_persist_error",
                    "tool_call_id": tool_use_id,
                    "exception_type": exc.__class__.__name__,
                    "exception": str(exc)[:500],
                }
            )
            return None
        artifact_map[tool_use_id] = reference.artifact_id
        context.trace.log(
            {
                "type": "artifact_persisted",
                "tool_call_id": tool_use_id,
                "artifact_id": reference.artifact_id,
                "chars_persisted": reference.chars,
                "creation_reason": reference.creation_reason,
            }
        )
        return reference.artifact_id

    def _round_budget_sort_key(
        self,
        block: dict,
        names: dict[str, str],
    ) -> tuple[int, int]:
        tool_use_id = str(block.get("tool_use_id", ""))
        tool_name = names.get(tool_use_id, "unknown")
        content = str(block.get("content", ""))
        if content.startswith(PERSISTED_OUTPUT_PREFIX):
            priority = 0
        elif tool_name != "read_file":
            priority = 1
        else:
            priority = 2
        return priority, -estimate_value_tokens(block)

    def _minimize_persisted_previews(
        self,
        projected: list[tuple[dict, str, str, str | None]],
        *,
        total: int,
        limit: int,
    ) -> tuple[int, int]:
        saved_tokens = 0
        for block, artifact_id, tool_name, source in sorted(
            projected,
            key=lambda item: estimate_value_tokens(item[0]),
            reverse=True,
        ):
            if total <= limit:
                break
            before_tokens = estimate_value_tokens(block)
            block["content"] = self._persisted_stub(
                artifact_id,
                tool_name,
                source,
            )
            after_tokens = estimate_value_tokens(block)
            reduction = max(before_tokens - after_tokens, 0)
            total -= reduction
            saved_tokens += reduction
        return total, saved_tokens

    def _persisted_preview(
        self,
        content: str,
        *,
        artifact_id: str,
        tool_name: str,
        provenance: str | None = None,
    ) -> str:
        preview = self.head_tail(content, 600)
        source = f"source: {provenance}\n" if provenance else ""
        return (
            f"{PERSISTED_OUTPUT_PREFIX}\n"
            f"tool: {tool_name}\n"
            f"artifact_id: {artifact_id}\n"
            f"{source}"
            f"original_chars: {len(content)}\n"
            "Use read_artifact for additional slices.\n"
            "Preview:\n"
            f"{preview}\n"
            "</persisted-output>"
        )

    def _persisted_stub(
        self,
        artifact_id: str,
        tool_name: str,
        provenance: str | None = None,
    ) -> str:
        source = f"source: {provenance}\n" if provenance else ""
        return (
            f"{PERSISTED_OUTPUT_PREFIX}\n"
            f"tool: {tool_name}\n"
            f"artifact_id: {artifact_id}\n"
            f"{source}"
            "Preview omitted by the tool-result round budget; use read_artifact.\n"
            "</persisted-output>"
        )

    def _tool_names_by_id(self, messages: list[dict]) -> dict[str, str]:
        names: dict[str, str] = {}
        for message in messages:
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    names[str(block.get("id", ""))] = str(block.get("name", "unknown"))
        return names

    def head_tail(self, text: str, max_chars: int) -> str:
        return head_tail_preview(text, max_chars)

    def _result_metadata(self, context, tool_use_id: str) -> dict[str, Any]:
        values = getattr(context, "tool_result_metadata", {})
        value = values.get(tool_use_id, {})
        return value if isinstance(value, dict) else {}

    def _is_source_result(self, tool_name: str, metadata: dict[str, Any]) -> bool:
        return tool_name == "read_file" and metadata.get("projection_kind") != "source_notice"

    def _mark_source_projected(
        self,
        context,
        tool_use_id: str,
        metadata: dict[str, Any],
    ) -> None:
        marker = getattr(context, "mark_source_observation_projected", None)
        if callable(marker):
            marker(tool_use_id, metadata)

    def _source_stub(
        self,
        context,
        metadata: dict[str, Any],
        *,
        provenance: str | None = None,
    ) -> str:
        if not metadata:
            source = provenance or "source metadata unavailable"
            return (
                f"{SOURCE_OUTPUT_PREFIX} {source}; reopen=read_file with the recorded line cursor]"
            )
        path = str(metadata.get("source_path") or metadata.get("resolved_path") or "unknown")
        sha = str(metadata.get("source_sha256") or "")[:16] or "unknown"
        start = metadata.get("returned_line_start")
        end = metadata.get("returned_line_end")
        offset = max(int(start or 1) - 1, 0)
        resolved_path = str(metadata.get("resolved_path") or "")
        state = getattr(context, "read_file_segments", {}).get(resolved_path)
        unchanged = bool(state is not None and state.sha256 == metadata.get("source_sha256"))
        fully_scanned = bool(unchanged and state.fully_scanned)
        recovery = (
            f"reopen=read_file(path={path}, offset={offset})"
            if unchanged
            else "historical=true; current source version changed"
        )
        return (
            f"{SOURCE_OUTPUT_PREFIX} path={path}; sha={sha}; lines={start}-{end}; "
            f"fully_scanned={str(fully_scanned).lower()}; {recovery}]"
        )
