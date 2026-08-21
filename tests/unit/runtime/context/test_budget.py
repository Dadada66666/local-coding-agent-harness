from __future__ import annotations

from runtime.context.budget import (
    estimate_input_tokens,
    estimate_text_tokens,
    measure_context,
    normalize_provider_context_anchor,
)


def test_non_ascii_estimate_is_more_conservative_than_ascii() -> None:
    assert estimate_text_tokens("上下文管理能力") > estimate_text_tokens("context")


def test_main_input_accounting_excludes_output_reservation() -> None:
    system = "s" * ((236_000 - 2) * 4)
    messages = []
    tools = []
    local_input_tokens = estimate_input_tokens(system, messages, tools)

    measurement = measure_context(
        system=system,
        messages=messages,
        tools=tools,
        context_window_tokens=272_000,
        max_output_tokens=16_000,
        safety_margin_tokens=4_096,
        auto_compact_ratio=0.90,
    )

    assert local_input_tokens == 236_000
    assert measurement.local_input_tokens == 236_000
    assert measurement.hard_input_limit_tokens == 251_904
    assert measurement.auto_compact_trigger_tokens == 244_800
    assert measurement.trigger_reason is None


def test_auto_trigger_uses_input_only_boundary() -> None:
    below = measure_context(
        system="s" * ((244_799 - 2) * 4),
        messages=[],
        tools=[],
        context_window_tokens=272_000,
        max_output_tokens=16_000,
        safety_margin_tokens=4_096,
        auto_compact_ratio=0.90,
    )
    at = measure_context(
        system="s" * ((244_800 - 2) * 4),
        messages=[],
        tools=[],
        context_window_tokens=272_000,
        max_output_tokens=16_000,
        safety_margin_tokens=4_096,
        auto_compact_ratio=0.90,
    )

    assert below.local_input_tokens == 244_799
    assert below.trigger_reason is None
    assert at.local_input_tokens == 244_800
    assert at.trigger_reason == "auto_pressure"


def test_hard_limit_uses_input_only_boundary() -> None:
    below = measure_context(
        system="s" * ((251_903 - 2) * 4),
        messages=[],
        tools=[],
        context_window_tokens=272_000,
        max_output_tokens=16_000,
        safety_margin_tokens=4_096,
        auto_compact_ratio=0.90,
    )
    at = measure_context(
        system="s" * ((251_904 - 2) * 4),
        messages=[],
        tools=[],
        context_window_tokens=272_000,
        max_output_tokens=16_000,
        safety_margin_tokens=4_096,
        auto_compact_ratio=0.90,
    )

    assert below.hard_pressure is False
    assert at.hard_pressure is True
    assert at.trigger_reason == "hard_pressure"


def test_explicit_output_override_changes_only_hard_limit() -> None:
    kwargs = {
        "system": "system",
        "messages": [{"role": "user", "content": "inspect"}],
        "tools": [],
        "context_window_tokens": 272_000,
        "safety_margin_tokens": 4_096,
        "auto_compact_ratio": 0.90,
    }
    default = measure_context(max_output_tokens=16_000, **kwargs)
    overridden = measure_context(max_output_tokens=8_000, **kwargs)

    assert overridden.local_input_tokens == default.local_input_tokens
    assert default.hard_input_limit_tokens == 251_904
    assert overridden.hard_input_limit_tokens == 259_904
    assert overridden.auto_compact_trigger_tokens == 244_800


def test_provider_anchor_supports_exclusive_cache_accounting() -> None:
    assert (
        normalize_provider_context_anchor(
            local_input_tokens=25_000,
            input_tokens=1_000,
            cache_read_input_tokens=24_000,
        )
        == 25_000
    )


def test_provider_anchor_avoids_duplicate_inclusive_cache_accounting() -> None:
    assert (
        normalize_provider_context_anchor(
            local_input_tokens=25_000,
            input_tokens=25_000,
            cache_read_input_tokens=24_000,
        )
        == 25_000
    )


def test_provider_anchor_without_cache_uses_reported_input() -> None:
    assert (
        normalize_provider_context_anchor(
            local_input_tokens=24_000,
            input_tokens=25_000,
        )
        == 25_000
    )


def test_provider_anchor_adds_only_visible_tail_growth() -> None:
    assert (
        normalize_provider_context_anchor(
            local_input_tokens=26_000,
            input_tokens=20_000,
            assistant_response_tokens=4_000,
            appended_input_tokens=2_000,
        )
        == 26_000
    )


def test_provider_anchor_selects_cache_interpretation_before_visible_growth() -> None:
    assert (
        normalize_provider_context_anchor(
            local_input_tokens=27_000,
            input_tokens=20_000,
            cache_read_input_tokens=10_000,
            assistant_response_tokens=6_000,
            appended_input_tokens=4_000,
        )
        == 40_000
    )


def test_known_smaller_window_limits_default_capacity() -> None:
    measurement = measure_context(
        system="system",
        messages=[{"role": "user", "content": "x" * 80_000}],
        tools=[],
        context_window_tokens=32_000,
        max_output_tokens=8_000,
        safety_margin_tokens=4_096,
        auto_compact_ratio=0.90,
    )

    assert measurement.hard_input_limit_tokens == 19_904
    assert measurement.auto_compact_trigger_tokens == 19_904
    assert measurement.trigger_reason == "hard_pressure"
