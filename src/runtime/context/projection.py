from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from runtime.context.budget import estimate_value_tokens
from runtime.observability.text_preview import head_tail_preview


COMPACTABLE_TOOL_RESULTS = {
    "bash",
    "grep",
    "list_dir",
    "read_artifact",
    "read_file",
    "view_diff",
}
PERSISTED_OUTPUT_PREFIX = "<persisted-output>"
CLEARED_OUTPUT_PREFIX = "[Old tool observation cleared;"
MIN_PROJECTION_SAVINGS_TOKENS = 32


@dataclass(frozen=True)
class ToolResultProjection:
    count: int = 0
    saved_tokens: int = 0


class ToolResultProjector:
    """Replace expensive observations with bounded, recoverable projections."""

    def enforce_round_budget(self, context) -> ToolResultProjection:
        limit = int(context.config.max_tool_round_tokens)
        if limit <= 0 or not hasattr(context, "artifacts"):
            return ToolResultProjection()

        names = self._tool_names_by_id(context.messages)
        provenance = getattr(context, "tool_result_provenance", {})
        messages = deepcopy(context.messages)
        replacement_count = 0
        saved_tokens = 0
        for message in messages:
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, list):
                continue
            candidates = [
                block
                for block in content
                if isinstance(block, dict)
                and block.get("type") == "tool_result"
                and isinstance(block.get("content"), str)
                and not block["content"].startswith(CLEARED_OUTPUT_PREFIX)
            ]
            total = sum(estimate_value_tokens(block) for block in candidates)
            if total <= limit:
                continue

            projected: list[tuple[dict, str, str, str | None]] = []
            for block in sorted(candidates, key=estimate_value_tokens, reverse=True):
                if total <= limit:
                    break
                tool_use_id = str(block.get("tool_use_id", ""))
                original = str(block.get("content", ""))
                before_tokens = estimate_value_tokens(block)
                tool_name = names.get(tool_use_id, "unknown")
                source = provenance.get(tool_use_id)
                minimum_candidate = dict(block)
                minimum_candidate["content"] = self._persisted_stub(
                    "artifact_0000000000000000",
                    tool_name,
                    source,
                )
                if estimate_value_tokens(minimum_candidate) >= before_tokens:
                    continue
                artifact_id = self.persist_tool_result(context, tool_use_id, original)
                if artifact_id is None:
                    continue
                if original.startswith(PERSISTED_OUTPUT_PREFIX):
                    replacement = self._persisted_stub(
                        artifact_id,
                        tool_name,
                        source,
                    )
                else:
                    replacement = self._persisted_preview(
                        original,
                        artifact_id=artifact_id,
                        tool_name=tool_name,
                        provenance=source,
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
                reduction = max(before_tokens - after_tokens, 0)
                total -= reduction
                saved_tokens += reduction
                replacement_count += 1
                projected.append((block, artifact_id, tool_name, source))

            if total > limit:
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

        if replacement_count:
            context.messages = messages
            self.mark_context_changed(context)
            event = {
                "type": "tool_result_budget",
                "replaced_results": replacement_count,
                "saved_tokens": saved_tokens,
                "round_limit_tokens": limit,
                "budget_satisfied": all(
                    self._message_tool_result_tokens(message) <= limit
                    for message in messages
                ),
            }
            context.trace.log(event)
            tracker = getattr(context, "cost_tracker", None)
            if tracker is not None and hasattr(tracker, "record_context_event"):
                tracker.record_context_event(event)
        return ToolResultProjection(
            count=replacement_count,
            saved_tokens=saved_tokens,
        )

    def compact_consumed_results(
        self,
        context,
        *,
        compact_before: int,
    ) -> ToolResultProjection:
        if compact_before <= 0:
            return ToolResultProjection()

        names = self._tool_names_by_id(context.messages)
        provenance = getattr(context, "tool_result_provenance", {})
        compacted_ids: list[str] = []
        saved_tokens = 0
        messages = deepcopy(context.messages)
        for message in messages[:compact_before]:
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if not self._is_compactable_result(block, names):
                    continue
                tool_use_id = str(block["tool_use_id"])
                tool_name = names.get(tool_use_id, "unknown")
                source = provenance.get(tool_use_id)
                before_tokens = estimate_value_tokens(block)
                preview_block = deepcopy(block)
                preview_block["content"] = self._cleared_output(
                    tool_name,
                    "artifact_0000000000000000",
                    source,
                )
                if (
                    before_tokens - estimate_value_tokens(preview_block)
                    < MIN_PROJECTION_SAVINGS_TOKENS
                ):
                    continue
                artifact_id = self.persist_tool_result(context, tool_use_id, block["content"])
                if artifact_id is None:
                    continue
                block["content"] = self._cleared_output(
                    tool_name,
                    artifact_id,
                    source,
                )
                reduction = max(before_tokens - estimate_value_tokens(block), 0)
                if reduction < MIN_PROJECTION_SAVINGS_TOKENS:
                    continue
                saved_tokens += reduction
                compacted_ids.append(tool_use_id)

        if not compacted_ids:
            return ToolResultProjection()
        context.messages = messages
        self.mark_context_changed(context)
        context.trace.log(
            {
                "type": "context_microcompact",
                "compacted_tool_result_count": len(compacted_ids),
                "compacted_tool_ids": compacted_ids,
                "saved_tokens": saved_tokens,
            }
        )
        return ToolResultProjection(
            count=len(compacted_ids),
            saved_tokens=saved_tokens,
        )

    def persist_tool_result(self, context, tool_use_id: str, content: str) -> str | None:
        artifact_map = getattr(context, "tool_result_artifacts", None)
        if artifact_map is None:
            artifact_map = {}
            context.tool_result_artifacts = artifact_map
        existing = artifact_map.get(tool_use_id)
        if existing and context.artifacts.get(existing) is not None:
            return existing
        try:
            reference = context.artifacts.persist(tool_use_id, content)
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
        return reference.artifact_id

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

    def _cleared_output(
        self,
        tool_name: str,
        artifact_id: str,
        provenance: str | None = None,
    ) -> str:
        source = f"; source={provenance}" if provenance else ""
        return (
            f"{CLEARED_OUTPUT_PREFIX} "
            f"tool={tool_name}; "
            f"artifact_id={artifact_id}{source}]"
        )

    def _message_tool_result_tokens(self, message: dict) -> int:
        content = message.get("content")
        if message.get("role") != "user" or not isinstance(content, list):
            return 0
        return sum(
            estimate_value_tokens(block)
            for block in content
            if isinstance(block, dict) and block.get("type") == "tool_result"
        )

    def _is_compactable_result(self, block: Any, names: dict[str, str]) -> bool:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            return False
        if block.get("is_error"):
            return False
        content = block.get("content")
        if not isinstance(content, str) or not content:
            return False
        if content.startswith(CLEARED_OUTPUT_PREFIX):
            return False
        tool_use_id = str(block.get("tool_use_id", ""))
        return names.get(tool_use_id) in COMPACTABLE_TOOL_RESULTS

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

    def mark_context_changed(self, context) -> None:
        marker = getattr(context, "mark_context_changed", None)
        if callable(marker):
            marker()
            return
        context.context_generation = int(getattr(context, "context_generation", 0)) + 1
        context.last_model_usage = None
        context.last_model_usage_message_index = None
        context.last_model_usage_generation = None
