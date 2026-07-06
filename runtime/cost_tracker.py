from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


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


def _empty_bucket(categories: tuple[str, ...]) -> dict[str, dict[str, int]]:
    return {
        category: {"chars": 0, "estimated_tokens": 0, "allocated_tokens": 0, "share": 0}
        for category in categories
    }


def _render(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0

    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, math.ceil((ascii_chars / 4) + (non_ascii_chars / 1.5)))


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
        self.turns: list[dict[str, Any]] = []

    def add_usage(self, usage) -> None:
        if not usage:
            return

        self.calls += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0

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
        input_breakdown = self._input_breakdown(system, messages, tools)
        output_breakdown = self._output_breakdown(response_message)

        _allocate_actual_tokens(input_breakdown, input_tokens)
        _allocate_actual_tokens(output_breakdown, output_tokens)

        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.turns.append(
            {
                "turn_id": turn_id,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "input_breakdown": input_breakdown,
                "output_breakdown": output_breakdown,
                "top_input_categories": self._top_categories(input_breakdown),
                "top_output_categories": self._top_categories(output_breakdown),
            }
        )

    def write(self, context=None) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "calls": self.calls,
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "total_tokens": self.input_tokens + self.output_tokens,
                    "estimated_cost_usd": None,
                    "token_breakdown": {
                        "note": (
                            "Breakdowns are local estimates for optimization. "
                            "API input_tokens/output_tokens remain the billing source of truth."
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
            category = "compacted_history" if content.startswith("[Compacted history]") else "user_messages"
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

        self._add_aggregate_shares(input_totals, self.input_tokens)
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
