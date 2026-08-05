from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextMeasurement:
    estimated_tokens: int
    provider_tokens: int | None
    used_tokens: int
    source: str
    request_chars: int
    context_window_tokens: int | None
    target_tokens: int | None
    max_output_tokens: int
    safety_margin_tokens: int
    soft_limit_tokens: int | None
    hard_limit_tokens: int | None
    trigger_reason: str | None

    @property
    def should_compact(self) -> bool:
        return self.trigger_reason is not None


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0

    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, math.ceil((ascii_chars / 4) + (non_ascii_chars / 1.5)))


def render_for_tokens(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )


def estimate_value_tokens(value: Any) -> int:
    return estimate_text_tokens(render_for_tokens(value))


def estimate_messages_tokens(messages: list[dict]) -> int:
    return estimate_value_tokens(messages)


def estimate_request_tokens(system: str, messages: list[dict], tools: list[dict]) -> int:
    return (
        estimate_text_tokens(system)
        + estimate_value_tokens(tools)
        + estimate_messages_tokens(messages)
    )


def request_char_count(system: str, messages: list[dict], tools: list[dict]) -> int:
    return len(system) + len(render_for_tokens(tools)) + len(render_for_tokens(messages))


def measure_context(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    context_window_tokens: int | None,
    target_tokens: int | None = None,
    max_output_tokens: int,
    safety_margin_tokens: int,
    soft_limit_ratio: float,
    provider_context_tokens: int | None = None,
    fallback_char_limit: int | None = None,
) -> ContextMeasurement:
    estimated_tokens = estimate_request_tokens(system, messages, tools)
    request_chars = request_char_count(system, messages, tools)
    provider_tokens = (
        max(int(provider_context_tokens), 0)
        if provider_context_tokens is not None
        else None
    )
    used_tokens = max(estimated_tokens, provider_tokens or 0)
    source = (
        "provider_usage"
        if provider_tokens is not None and provider_tokens >= estimated_tokens
        else "estimate"
    )

    hard_limit_tokens = None
    soft_limit_candidates = []
    trigger_reason = None
    if context_window_tokens is not None:
        hard_limit_tokens = max(
            int(context_window_tokens) - max(int(max_output_tokens), 0) - max(int(safety_margin_tokens), 0),
            1,
        )
        soft_limit_candidates.append(max(1, int(hard_limit_tokens * soft_limit_ratio)))
    if target_tokens is not None:
        soft_limit_candidates.append(max(int(target_tokens), 1))

    soft_limit_tokens = min(soft_limit_candidates) if soft_limit_candidates else None
    if soft_limit_tokens is not None and used_tokens >= soft_limit_tokens:
        trigger_reason = "token_budget"
    elif fallback_char_limit is not None and request_chars >= fallback_char_limit:
        trigger_reason = "char_fallback"

    return ContextMeasurement(
        estimated_tokens=estimated_tokens,
        provider_tokens=provider_tokens,
        used_tokens=used_tokens,
        source=source,
        request_chars=request_chars,
        context_window_tokens=context_window_tokens,
        target_tokens=target_tokens,
        max_output_tokens=max_output_tokens,
        safety_margin_tokens=safety_margin_tokens,
        soft_limit_tokens=soft_limit_tokens,
        hard_limit_tokens=hard_limit_tokens,
        trigger_reason=trigger_reason,
    )
