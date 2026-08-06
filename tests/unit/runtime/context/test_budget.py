from __future__ import annotations

from runtime.context.budget import estimate_text_tokens, measure_context


def test_non_ascii_estimate_is_more_conservative_than_ascii() -> None:
    assert estimate_text_tokens("上下文管理能力") > estimate_text_tokens("context")


def test_measurement_includes_system_tools_and_output_reserve() -> None:
    measurement = measure_context(
        system="s" * 800,
        messages=[{"role": "user", "content": "m" * 800}],
        tools=[{"name": "read_file", "description": "t" * 800}],
        context_window_tokens=1000,
        max_output_tokens=100,
        safety_margin_tokens=100,
        soft_limit_ratio=0.5,
    )

    assert measurement.estimated_tokens >= 600
    assert measurement.hard_limit_tokens == 800
    assert measurement.soft_limit_tokens == 400
    assert measurement.trigger_reason == "token_budget"


def test_provider_usage_is_used_as_a_conservative_anchor() -> None:
    measurement = measure_context(
        system="system",
        messages=[{"role": "user", "content": "small"}],
        tools=[],
        context_window_tokens=2000,
        max_output_tokens=200,
        safety_margin_tokens=200,
        soft_limit_ratio=0.8,
        provider_context_tokens=1500,
    )

    assert measurement.source == "provider_usage"
    assert measurement.used_tokens == 1500
    assert measurement.trigger_reason == "token_budget"


def test_char_threshold_is_only_a_fallback_without_a_known_window() -> None:
    measurement = measure_context(
        system="system",
        messages=[{"role": "user", "content": "x" * 100}],
        tools=[],
        context_window_tokens=None,
        max_output_tokens=100,
        safety_margin_tokens=100,
        soft_limit_ratio=0.8,
        fallback_char_limit=50,
    )

    assert measurement.soft_limit_tokens is None
    assert measurement.trigger_reason == "char_fallback"


def test_absolute_target_limits_cost_before_capacity_pressure() -> None:
    measurement = measure_context(
        system="s" * 1200,
        messages=[{"role": "user", "content": "m" * 1200}],
        tools=[],
        context_window_tokens=200000,
        target_tokens=500,
        max_output_tokens=4096,
        safety_margin_tokens=4096,
        soft_limit_ratio=0.8,
    )

    assert measurement.hard_limit_tokens == 191808
    assert measurement.soft_limit_tokens == 500
    assert measurement.trigger_reason == "token_budget"
