from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal


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


class ToolProgressPolicy:
    """Bound repeated deterministic protocol failures without directing strategy."""

    def evaluate(
        self,
        context,
        response,
        executions: list[tuple[Any, Any]],
        *,
        max_output_tokens: int,
    ) -> ProgressDecision:
        failures = self._deterministic_failures(executions)
        if not failures:
            self._reset_failures(context)
            return ProgressDecision()

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

        tools = tuple(call.name for call, _, _ in failures)
        errors = tuple(
            str(result.error or "deterministic tool failure") for _, result, _ in failures
        )
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
        repeated_failure = context.task_failure_repeat_count >= 3
        if repeated_saturated or saturation_exhausted or repeated_failure:
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
                    "The previous tool call reached the output budget and failed a "
                    "deterministic tool contract. Change or split the payload before retrying."
                ),
                **common,
            )

        if context.task_failure_repeat_count == 2:
            return ProgressDecision(
                action="retry",
                reason="repeated_invalid_tool_call",
                message=(
                    "The same invalid or unavailable tool call failed twice. Change its "
                    "arguments or use an available tool; do not repeat it unchanged."
                ),
                **common,
            )

        return ProgressDecision(**common)

    def _deterministic_failures(self, executions):
        failures = []
        for call, result in executions:
            if result.ok:
                continue
            category = self._failure_category(result)
            if category is not None:
                failures.append((call, result, category))
        return failures

    @staticmethod
    def _failure_category(result) -> str | None:
        metadata = result.metadata or {}
        if metadata.get("validation_error"):
            return "validation_error"
        if metadata.get("unknown_tool"):
            return "unknown_tool"
        if metadata.get("unavailable_tool") or metadata.get("model_contract_violation"):
            return "unavailable_tool"
        return None

    def _fingerprint(self, failures) -> str:
        value = [
            {
                "tool": call.name,
                "arguments": call.arguments,
                "category": category,
            }
            for call, _, category in failures
        ]
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(rendered.encode("utf-8")).hexdigest()[:16]

    def _reset_failures(self, context) -> None:
        context.task_failure_fingerprint = None
        context.task_failure_repeat_count = 0
        context.task_saturated_invalid_calls = 0
