import builtins

import pytest

from benchmarks.runner import (
    BENCHMARK_VERSION,
    build_payload,
    classify_failure,
    reject_interactive_input,
    render_markdown,
)


def _result(**overrides):
    result = {
        "case": "required_plan",
        "title": "Required Plan bug fix",
        "difficulty": "smoke",
        "category": "planning",
        "pass": True,
        "end_to_end_pass": True,
        "task_correct": True,
        "oracle_pass": True,
        "runtime_success": True,
        "runtime_oracle_agreement": True,
        "failure_category": "passed",
        "model": "test-model",
        "model_calls": 10,
        "input_tokens": 63662,
        "output_tokens": 1200,
        "cache_read_input_tokens": 39680,
        "tool_failures": 0,
        "repair_attempts": 0,
        "verification_status": "passed",
        "oracle_changed_paths": ["pricing.py"],
        "unauthorized_mutations": [],
        "runtime_error": None,
        "external_pytest_output": "4 passed in 0.10s",
    }
    result.update(overrides)
    return result


@pytest.mark.parametrize(
    ("expected", "values"),
    [
        ("passed", {}),
        ("execution_error", {"execution_error": "boom"}),
        ("unauthorized_mutation", {"unauthorized_mutations": ("tests.py",)}),
        ("plan_contract_failure", {"case_invariants_passed": False}),
        ("runtime_failure", {"runtime_success": False}),
        ("oracle_failure", {"task_correct": False}),
    ],
)
def test_classify_failure(expected, values) -> None:
    arguments = {
        "runtime_success": True,
        "task_correct": True,
        "execution_error": None,
        "unauthorized_mutations": (),
        "case_invariants_passed": True,
    }
    arguments.update(values)

    assert classify_failure(**arguments) == expected


def test_build_payload_records_identity_and_explicit_totals(monkeypatch) -> None:
    monkeypatch.setattr("benchmarks.runner.resolve_git_sha", lambda: "abc123")

    payload = build_payload([_result()])

    assert payload["benchmark_version"] == BENCHMARK_VERSION
    assert payload["git_sha"] == "abc123"
    assert payload["model"] == "test-model"
    assert payload["python_version"]
    assert payload["platform"]
    assert payload["summary"] == {
        "end_to_end_pass": "1/1",
        "task_correct": "1/1",
        "runtime_oracle_agreement": "1/1",
        "unauthorized_mutations": 0,
        "total_model_calls": 10,
        "total_input_tokens": 63662,
        "total_cache_read_input_tokens": 39680,
    }


def test_render_markdown_is_presentation_ready(monkeypatch) -> None:
    monkeypatch.setattr("benchmarks.runner.resolve_git_sha", lambda: "abc123")
    markdown = render_markdown(build_payload([_result()]))

    assert "# Agent Evaluation Benchmark" in markdown
    assert "- Benchmark version: `1.0`" in markdown
    assert "- Commit: `abc123`" in markdown
    assert "- End-to-end pass rate: **1/1**" in markdown
    assert "| required_plan | PASS | PASS | PASS | 10 | 63,662 | 39,680 |" in markdown
    assert "Difficulty: `smoke`, Category: `planning`" in markdown


def test_reject_interactive_input_fails_and_restores_input() -> None:
    original = builtins.input

    with reject_interactive_input(), pytest.raises(AssertionError, match="interactive input"):
        builtins.input()

    assert builtins.input is original
