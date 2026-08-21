from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ContextMeasurement:
    local_input_tokens: int
    provider_input_tokens: int | None
    pressure_input_tokens: int
    source: str
    request_chars: int
    context_window_tokens: int
    max_output_tokens: int
    safety_margin_tokens: int
    auto_compact_trigger_tokens: int
    hard_input_limit_tokens: int
    trigger_reason: str | None

    @property
    def should_rebase(self) -> bool:
        return self.trigger_reason is not None

    @property
    def hard_pressure(self) -> bool:
        return self.pressure_input_tokens >= self.hard_input_limit_tokens


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


def estimate_input_tokens(system: str, messages: list[dict], tools: list[dict]) -> int:
    """Estimate provider-visible input only (CMV3-TRG-001)."""
    return (
        estimate_text_tokens(system)
        + estimate_value_tokens(tools)
        + estimate_messages_tokens(messages)
    )


def normalize_provider_context_anchor(
    *,
    local_input_tokens: int,
    input_tokens: int,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
    assistant_response_tokens: int = 0,
    appended_input_tokens: int = 0,
) -> int:
    """Normalize supported cache conventions into next-request input usage."""
    input_tokens = max(int(input_tokens), 0)
    cache_tokens = max(int(cache_creation_input_tokens), 0) + max(
        int(cache_read_input_tokens),
        0,
    )
    visible_growth = max(int(assistant_response_tokens), 0) + max(
        int(appended_input_tokens),
        0,
    )
    candidates = {
        input_tokens + visible_growth,
        input_tokens + cache_tokens + visible_growth,
    }
    return min(
        candidates,
        key=lambda value: (abs(value - max(int(local_input_tokens), 0)), -value),
    )


def request_char_count(system: str, messages: list[dict], tools: list[dict]) -> int:
    return len(system) + len(render_for_tokens(tools)) + len(render_for_tokens(messages))


def measure_context(
    *,
    system: str,
    messages: list[dict],
    tools: list[dict],
    context_window_tokens: int,
    max_output_tokens: int,
    safety_margin_tokens: int,
    auto_compact_ratio: float,
    provider_input_tokens: int | None = None,
) -> ContextMeasurement:
    """Measure CMV3 pressure using input-only quantities."""
    local_input_tokens = estimate_input_tokens(system, messages, tools)
    request_chars = request_char_count(system, messages, tools)
    normalized_provider_input = (
        max(int(provider_input_tokens), 0) if provider_input_tokens is not None else None
    )
    pressure_input_tokens = max(local_input_tokens, normalized_provider_input or 0)
    source = (
        "provider_usage"
        if normalized_provider_input is not None and normalized_provider_input >= local_input_tokens
        else "estimate"
    )

    window = max(int(context_window_tokens), 1)
    output = max(int(max_output_tokens), 0)
    safety = max(int(safety_margin_tokens), 0)
    hard_input_limit_tokens = max(window - output - safety, 1)
    auto_compact_trigger_tokens = min(
        max(math.floor(window * float(auto_compact_ratio)), 1),
        hard_input_limit_tokens,
    )

    if pressure_input_tokens >= hard_input_limit_tokens:
        trigger_reason = "hard_pressure"
    elif pressure_input_tokens >= auto_compact_trigger_tokens:
        trigger_reason = "auto_pressure"
    else:
        trigger_reason = None

    return ContextMeasurement(
        local_input_tokens=local_input_tokens,
        provider_input_tokens=normalized_provider_input,
        pressure_input_tokens=pressure_input_tokens,
        source=source,
        request_chars=request_chars,
        context_window_tokens=window,
        max_output_tokens=output,
        safety_margin_tokens=safety,
        auto_compact_trigger_tokens=auto_compact_trigger_tokens,
        hard_input_limit_tokens=hard_input_limit_tokens,
        trigger_reason=trigger_reason,
    )
