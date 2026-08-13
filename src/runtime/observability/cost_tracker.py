from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from runtime.context.budget import estimate_text_tokens, render_for_tokens


INPUT_CATEGORIES = (
    "system_prompt",
    "tool_schemas",
    "user_messages",
    "assistant_messages",
    "assistant_tool_calls",
    "tool_results",
    "compacted_history",
    "other_messages",
)
OUTPUT_CATEGORIES = ("assistant_text", "tool_calls", "other")
USAGE_FIELDS = (
    "calls",
    "input_tokens",
    "logical_input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cache_deleted_input_tokens",
    "output_tokens",
)


def _empty_bucket(categories: tuple[str, ...]) -> dict[str, dict[str, int]]:
    return {
        category: {"chars": 0, "estimated_tokens": 0, "allocated_tokens": 0, "share": 0}
        for category in categories
    }


def _render(value: Any) -> str:
    return render_for_tokens(value)


def _estimate_tokens(text: str) -> int:
    return estimate_text_tokens(text)


def _add_text(bucket: dict[str, dict[str, int]], category: str, value: Any) -> None:
    text = _render(value)
    bucket[category]["chars"] += len(text)
    bucket[category]["estimated_tokens"] += _estimate_tokens(text)


def _allocate_actual_tokens(bucket: dict[str, dict[str, int]], actual_tokens: int) -> None:
    total_estimated = sum(item["estimated_tokens"] for item in bucket.values())
    remaining = max(actual_tokens, 0)
    categories = list(bucket)

    for index, category in enumerate(categories):
        item = bucket[category]
        if total_estimated <= 0:
            allocated = 0
        elif index == len(categories) - 1:
            allocated = remaining
        else:
            allocated = round(actual_tokens * item["estimated_tokens"] / total_estimated)
            allocated = min(allocated, remaining)

        item["allocated_tokens"] = allocated
        item["share"] = round(allocated / actual_tokens, 4) if actual_tokens else 0
        remaining -= allocated


def _merge_buckets(target: dict[str, dict[str, int]], source: dict[str, dict[str, int]]) -> None:
    for category, item in source.items():
        target.setdefault(category, {"chars": 0, "estimated_tokens": 0, "allocated_tokens": 0})
        target[category]["chars"] += item.get("chars", 0)
        target[category]["estimated_tokens"] += item.get("estimated_tokens", 0)
        target[category]["allocated_tokens"] += item.get("allocated_tokens", 0)


class CostTracker:
    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "cost.json"
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0
        self.cache_deleted_input_tokens = 0
        self.logical_input_tokens = 0
        self.turns: list[dict[str, Any]] = []
        self.context_events: list[dict[str, Any]] = []

    def add_usage(self, usage) -> None:
        if not usage:
            return

        self.calls += 1
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_deleted = getattr(usage, "cache_deleted_input_tokens", 0) or 0
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_creation_input_tokens += cache_creation
        self.cache_read_input_tokens += cache_read
        self.cache_deleted_input_tokens += cache_deleted
        self.logical_input_tokens += input_tokens + cache_creation + cache_read

    def record_model_call(
        self,
        *,
        turn_id: int,
        system: str,
        messages: list[dict],
        tools: list[dict],
        response_message: dict,
        usage,
    ) -> None:
        if not usage:
            return

        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_deleted = getattr(usage, "cache_deleted_input_tokens", 0) or 0
        logical_input_tokens = input_tokens + cache_creation + cache_read
        input_breakdown = self._input_breakdown(system, messages, tools)
        output_breakdown = self._output_breakdown(response_message)

        _allocate_actual_tokens(input_breakdown, logical_input_tokens)
        _allocate_actual_tokens(output_breakdown, output_tokens)

        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cache_creation_input_tokens += cache_creation
        self.cache_read_input_tokens += cache_read
        self.cache_deleted_input_tokens += cache_deleted
        self.logical_input_tokens += logical_input_tokens
        self.turns.append(
            {
                "turn_id": turn_id,
                "input_tokens": input_tokens,
                "logical_input_tokens": logical_input_tokens,
                "cache_creation_input_tokens": cache_creation,
                "cache_read_input_tokens": cache_read,
                "cache_deleted_input_tokens": cache_deleted,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "logical_total_tokens": logical_input_tokens + output_tokens,
                "input_breakdown": input_breakdown,
                "output_breakdown": output_breakdown,
                "top_input_categories": self._top_categories(input_breakdown),
                "top_output_categories": self._top_categories(output_breakdown),
            }
        )

    def record_context_event(self, event: dict[str, Any]) -> None:
        self.context_events.append(dict(event))

    def snapshot(self) -> dict[str, int]:
        return self._with_totals(
            {field: int(getattr(self, field, 0)) for field in USAGE_FIELDS}
        )

    def delta(self, baseline: dict[str, int] | None = None) -> dict[str, int]:
        baseline = baseline or {}
        return self._with_totals(
            {
                field: max(int(getattr(self, field, 0)) - int(baseline.get(field, 0)), 0)
                for field in USAGE_FIELDS
            }
        )

    def write(self, context=None) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "calls": self.calls,
                    "input_tokens": self.input_tokens,
                    "logical_input_tokens": self.logical_input_tokens,
                    "cache_creation_input_tokens": self.cache_creation_input_tokens,
                    "cache_read_input_tokens": self.cache_read_input_tokens,
                    "cache_deleted_input_tokens": self.cache_deleted_input_tokens,
                    "output_tokens": self.output_tokens,
                    "total_tokens": self.input_tokens + self.output_tokens,
                    "logical_total_tokens": self.logical_input_tokens + self.output_tokens,
                    "estimated_cost_usd": None,
                    "current_task": self._current_task_cost(context),
                    "completed_tasks": list(getattr(context, "completed_tasks", [])),
                    "context_management": {
                        "events": self.context_events,
                        **self._context_event_summary(context),
                        "estimated_tokens_saved": sum(
                            max(int(event.get("saved_tokens", 0)), 0)
                            for event in self.context_events
                        ),
                        "source_working_set": self._source_working_set(context),
                    },
                    "artifacts": self._artifact_summary(context),
                    "source_read_efficiency": self._source_efficiency(context),
                    "token_breakdown": {
                        "note": (
                            "Breakdowns are local estimates for optimization. "
                            "Provider usage fields remain the billing source of truth. "
                            "logical_input_tokens includes cache creation and cache reads; "
                            "logical_total_tokens adds model output to that logical input."
                        ),
                        "aggregate": self._aggregate_breakdown(),
                        "turns": self.turns,
                    },
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return self.path

    def _input_breakdown(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
    ) -> dict[str, dict[str, int]]:
        bucket = _empty_bucket(INPUT_CATEGORIES)
        _add_text(bucket, "system_prompt", system)
        _add_text(bucket, "tool_schemas", tools)

        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if role == "user":
                self._add_user_content(bucket, content)
            elif role == "assistant":
                self._add_assistant_content(bucket, content)
            else:
                _add_text(bucket, "other_messages", message)

        return bucket

    def _add_user_content(self, bucket: dict[str, dict[str, int]], content: Any) -> None:
        if isinstance(content, str):
            compacted_prefixes = ("[Compacted history]", "[Runtime checkpoint]")
            category = (
                "compacted_history"
                if content.startswith(compacted_prefixes)
                else "user_messages"
            )
            _add_text(bucket, category, content)
            return

        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    _add_text(bucket, "tool_results", block)
                else:
                    _add_text(bucket, "user_messages", block)
            return

        _add_text(bucket, "user_messages", content)

    def _add_assistant_content(self, bucket: dict[str, dict[str, int]], content: Any) -> None:
        if isinstance(content, str):
            _add_text(bucket, "assistant_messages", content)
            return

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    _add_text(bucket, "assistant_messages", block)
                elif block.get("type") == "tool_use":
                    _add_text(bucket, "assistant_tool_calls", block)
                elif block.get("type") == "text":
                    _add_text(bucket, "assistant_messages", block)
                else:
                    _add_text(bucket, "other_messages", block)
            return

        _add_text(bucket, "assistant_messages", content)

    def _output_breakdown(self, response_message: dict) -> dict[str, dict[str, int]]:
        bucket = _empty_bucket(OUTPUT_CATEGORIES)
        content = response_message.get("content")

        if isinstance(content, str):
            _add_text(bucket, "assistant_text", content)
            return bucket

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    _add_text(bucket, "assistant_text", block)
                elif block.get("type") == "tool_use":
                    _add_text(bucket, "tool_calls", block)
                elif block.get("type") == "text":
                    _add_text(bucket, "assistant_text", block)
                else:
                    _add_text(bucket, "other", block)
            return bucket

        _add_text(bucket, "other", response_message)
        return bucket

    def _aggregate_breakdown(self) -> dict[str, dict[str, dict[str, int]]]:
        input_totals = _empty_bucket(INPUT_CATEGORIES)
        output_totals = _empty_bucket(OUTPUT_CATEGORIES)

        for turn in self.turns:
            _merge_buckets(input_totals, turn["input_breakdown"])
            _merge_buckets(output_totals, turn["output_breakdown"])

        self._add_aggregate_shares(input_totals, self.logical_input_tokens)
        self._add_aggregate_shares(output_totals, self.output_tokens)
        return {"input": input_totals, "output": output_totals}

    def _add_aggregate_shares(self, bucket: dict[str, dict[str, int]], total_tokens: int) -> None:
        for item in bucket.values():
            allocated = item.get("allocated_tokens", 0)
            item["share"] = round(allocated / total_tokens, 4) if total_tokens else 0

    def _top_categories(self, bucket: dict[str, dict[str, int]], limit: int = 3) -> list[dict[str, Any]]:
        ranked = sorted(
            (
                {
                    "category": category,
                    "allocated_tokens": item.get("allocated_tokens", 0),
                    "share": item.get("share", 0),
                }
                for category, item in bucket.items()
            ),
            key=lambda item: item["allocated_tokens"],
            reverse=True,
        )
        return [item for item in ranked[:limit] if item["allocated_tokens"] > 0]

    def _current_task_cost(self, context) -> dict[str, Any] | None:
        if context is None:
            return None
        plan_state = getattr(context, "plan_state", None)
        return {
            "task_id": getattr(context, "task_id", None),
            "task": getattr(context, "task", ""),
            "status": getattr(
                getattr(context, "task_status", None),
                "value",
                None,
            ),
            "waiting_reason": getattr(context, "task_waiting_reason", None),
            "plan_phase": getattr(
                getattr(plan_state, "phase", None),
                "value",
                None,
            ),
            **self.delta(getattr(context, "task_cost_start", None)),
        }

    def _source_efficiency(self, context) -> dict[str, Any]:
        snapshot = getattr(context, "source_efficiency_snapshot", None)
        return snapshot() if callable(snapshot) else {}

    def _source_working_set(self, context) -> dict[str, Any]:
        snapshot = getattr(context, "source_working_set_snapshot", None)
        return snapshot() if callable(snapshot) else {}

    def _context_event_summary(self, context) -> dict[str, int]:
        projection_events = [
            event
            for event in self.context_events
            if event.get("type") == "context_tool_results_projected"
        ]
        round_budget_events = [
            event
            for event in self.context_events
            if event.get("type") == "tool_result_budget"
        ]
        return {
            "full_history_compactions": int(
                getattr(context, "context_compactions", 0)
            ),
            "tool_result_projection_events": len(projection_events),
            "tool_results_projected": sum(
                max(int(event.get("projected_results", 0)), 0)
                for event in projection_events
            ),
            "round_budget_projection_events": len(round_budget_events),
            "round_budget_results_projected": sum(
                max(int(event.get("replaced_results", 0)), 0)
                for event in round_budget_events
            ),
            "eager_projection_events": sum(
                event.get("reason") == "eager_tool_result_projection"
                for event in projection_events
            ),
        }

    def _artifact_summary(self, context) -> dict[str, int]:
        artifacts = getattr(context, "artifacts", None)
        snapshot = getattr(artifacts, "snapshot", None)
        return snapshot() if callable(snapshot) else {}

    def _with_totals(self, values: dict[str, int]) -> dict[str, int]:
        return {
            **values,
            "total_tokens": values["input_tokens"] + values["output_tokens"],
            "logical_total_tokens": (
                values["logical_input_tokens"] + values["output_tokens"]
            ),
        }
