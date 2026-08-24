from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent.messages import TokenUsage
from runtime.context.budget import measure_context
from runtime.observability.cost_tracker import CostTracker
from runtime.observability.trace_logger import TraceLogger
from tools.mcp_tool import MCPToolCall, MCPToolSearch


def test_cost_tracker_writes_per_turn_token_breakdown(monkeypatch) -> None:
    captured: dict[str, str] = {}

    monkeypatch.setattr(Path, "mkdir", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda self, text, *args, **kwargs: captured.setdefault("text", text),
    )
    tracker = CostTracker(Path("unused-run-dir"))

    tracker.record_model_call(
        turn_id=1,
        system="You are a coding agent.",
        messages=[
            {"role": "user", "content": "Create a file."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "tool_1",
                        "name": "write_file",
                        "input": {"path": "demo.py", "content": "print('hello')\n"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool_1",
                        "content": "created demo.py",
                    }
                ],
            },
        ],
        tools=[
            {
                "name": "write_file",
                "input_schema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            }
        ],
        response_message={"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        usage=TokenUsage(
            input_tokens=80_000,
            output_tokens=20,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=79_000,
        ),
        context_generation=3,
        plan_phase="executing",
    )

    path = tracker.write()
    data = json.loads(captured["text"])
    turn = data["token_breakdown"]["turns"][0]

    assert path == Path("unused-run-dir") / "cost.json"
    assert data["calls"] == 1
    assert data["input_tokens"] == 80_000
    assert data["cache_creation_input_tokens"] == 0
    assert data["cache_read_input_tokens"] == 79_000
    assert data["output_tokens"] == 20
    assert data["total_tokens"] == 80_020
    assert "logical_input_tokens" not in data
    assert "logical_total_tokens" not in data
    assert turn["turn_id"] == 1
    assert turn["input_breakdown"]["system_prompt"]["estimated_tokens"] > 0
    assert turn["input_breakdown"]["tool_schemas"]["estimated_tokens"] > 0
    assert turn["input_breakdown"]["assistant_tool_calls"]["estimated_tokens"] > 0
    assert turn["input_breakdown"]["tool_results"]["estimated_tokens"] > 0
    assert turn["output_breakdown"]["assistant_text"]["estimated_tokens"] > 0
    assert turn["total_tokens"] == 80_020
    assert "logical_input_tokens" not in turn
    assert "logical_total_tokens" not in turn
    assert turn["request_prefix"]["previous_messages_preserved"] is None
    assert turn["request_prefix"]["context_generation"] == 3
    assert turn["request_prefix"]["plan_phase"] == "executing"
    assert len(turn["request_prefix"]["system_hash"]) == 64
    assert len(turn["request_prefix"]["tools_hash"]) == 64
    assert all(
        set(item) == {"chars", "estimated_tokens", "estimated_share"}
        for item in turn["input_breakdown"].values()
    )
    assert 0.999 <= _share_total(turn["input_breakdown"], "estimated_share") <= 1.001
    assert set(turn["top_input_categories"][0]) == {
        "category",
        "estimated_tokens",
        "estimated_share",
    }
    assert _allocated_total(turn["output_breakdown"]) == 20
    assert all(
        "allocated_tokens" not in item
        for item in data["token_breakdown"]["aggregate"]["input"].values()
    )


def test_request_prefix_detects_append_only_and_rewritten_history() -> None:
    tracker = CostTracker(Path("unused-run-dir"))
    usage = TokenUsage(input_tokens=10, output_tokens=2)
    initial = [{"role": "user", "content": "inspect secret-marker"}]

    tracker.record_model_call(
        turn_id=1,
        system="system",
        messages=initial,
        tools=[],
        response_message={"role": "assistant", "content": "working"},
        usage=usage,
    )
    tracker.record_model_call(
        turn_id=2,
        system="system",
        messages=[*initial, {"role": "assistant", "content": "working"}],
        tools=[],
        response_message={"role": "assistant", "content": "continue"},
        usage=usage,
    )
    tracker.record_model_call(
        turn_id=3,
        system="system",
        messages=[{"role": "user", "content": "[Context checkpoint v3]\n{}"}],
        tools=[],
        response_message={"role": "assistant", "content": "continue"},
        usage=usage,
        context_generation=1,
    )

    assert tracker.turns[1]["request_prefix"]["previous_messages_preserved"] is True
    assert tracker.turns[2]["request_prefix"]["previous_messages_preserved"] is False
    assert "secret-marker" not in json.dumps(
        tracker.turns[0]["request_prefix"],
        ensure_ascii=False,
    )


def test_cost_tracker_preserves_no_cache_usage_without_derived_total() -> None:
    tracker = CostTracker(Path("unused-run-dir"))

    tracker.record_model_call(
        turn_id=1,
        system="system",
        messages=[],
        tools=[],
        response_message={"role": "assistant", "content": "done"},
        usage=TokenUsage(input_tokens=123, output_tokens=7),
    )

    turn = tracker.turns[0]
    assert turn["input_tokens"] == 123
    assert turn["cache_creation_input_tokens"] == 0
    assert turn["cache_read_input_tokens"] == 0
    assert turn["output_tokens"] == 7
    assert "logical_input_tokens" not in turn
    assert "logical_total_tokens" not in turn


def test_trace_preserves_raw_usage_without_logical_total(tmp_path: Path) -> None:
    trace = TraceLogger(tmp_path, "run-1")

    trace.log_model_usage(
        TokenUsage(
            input_tokens=80_000,
            output_tokens=17,
            cache_creation_input_tokens=3,
            cache_read_input_tokens=79_000,
            cache_deleted_input_tokens=5,
        ),
        turn_id=4,
    )

    event = json.loads(trace.path.read_text(encoding="utf-8"))
    assert event["input_tokens"] == 80_000
    assert event["cache_creation_input_tokens"] == 3
    assert event["cache_read_input_tokens"] == 79_000
    assert event["cache_deleted_input_tokens"] == 5
    assert event["output_tokens"] == 17
    assert "logical_input_tokens" not in event


def test_cost_recording_does_not_change_context_pressure() -> None:
    kwargs = {
        "system": "system",
        "messages": [{"role": "user", "content": "inspect"}],
        "tools": [{"name": "read_file"}],
        "context_window_tokens": 272_000,
        "max_output_tokens": 16_000,
        "safety_margin_tokens": 4_096,
        "auto_compact_ratio": 0.90,
    }
    before = measure_context(**kwargs)
    tracker = CostTracker(Path("unused-run-dir"))

    tracker.record_model_call(
        turn_id=1,
        system=kwargs["system"],
        messages=kwargs["messages"],
        tools=kwargs["tools"],
        response_message={"role": "assistant", "content": "done"},
        usage=TokenUsage(
            input_tokens=80_000,
            cache_read_input_tokens=79_000,
            output_tokens=5,
        ),
    )
    after = measure_context(**kwargs)

    assert after == before


def test_request_prefix_hashes_identify_static_prefix_changes() -> None:
    tracker = CostTracker(Path("unused-run-dir"))
    usage = TokenUsage(input_tokens=10, output_tokens=2)
    messages = [{"role": "user", "content": "inspect"}]

    for turn_id, system, tools in (
        (1, "system-a", [{"name": "read_file"}]),
        (2, "system-b", [{"name": "read_file"}]),
        (3, "system-b", [{"name": "read_file"}, {"name": "edit_file"}]),
    ):
        tracker.record_model_call(
            turn_id=turn_id,
            system=system,
            messages=messages,
            tools=tools,
            response_message={"role": "assistant", "content": "continue"},
            usage=usage,
        )

    first, second, third = [turn["request_prefix"] for turn in tracker.turns]
    assert first["system_hash"] != second["system_hash"]
    assert second["system_hash"] == third["system_hash"]
    assert first["tools_hash"] == second["tools_hash"]
    assert second["tools_hash"] != third["tools_hash"]


def test_mcp_gateway_tools_hash_is_stable_across_search_and_call_continuations() -> None:
    tracker = CostTracker(Path("unused-run-dir"))
    runtime = SimpleNamespace()
    tools = [MCPToolSearch(runtime).schema(), MCPToolCall(runtime).schema()]
    usage = TokenUsage(input_tokens=10, output_tokens=2)

    for turn_id, response in (
        (1, {"role": "assistant", "content": "search"}),
        (2, {"role": "assistant", "content": "call"}),
        (3, {"role": "assistant", "content": "done"}),
    ):
        tracker.record_model_call(
            turn_id=turn_id,
            system="system",
            messages=[{"role": "user", "content": "inspect MCP"}],
            tools=tools,
            response_message=response,
            usage=usage,
            plan_phase="executing",
        )

    hashes = [turn["request_prefix"]["tools_hash"] for turn in tracker.turns]
    assert hashes[0] == hashes[1] == hashes[2]


def test_v3_checkpoint_is_counted_as_compacted_history() -> None:
    tracker = CostTracker(Path("unused-run-dir"))

    breakdown = tracker._input_breakdown(
        "system",
        [{"role": "user", "content": "[Context checkpoint v3]\n{}"}],
        [],
    )

    assert breakdown["compacted_history"]["estimated_tokens"] > 0
    assert breakdown["user_messages"]["estimated_tokens"] == 0


def test_cost_tracker_writes_context_and_artifact_summaries(monkeypatch) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(Path, "mkdir", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(
        Path,
        "write_text",
        lambda self, text, *args, **kwargs: captured.setdefault("text", text),
    )
    tracker = CostTracker(Path("unused-run-dir"))
    tracker.record_context_event(
        {
            "type": "tool_result_budget",
            "replaced_results": 1,
            "saved_tokens": 100,
        }
    )
    tracker.record_context_event(
        {
            "type": "context_rebase",
            "reason": "auto",
            "saved_tokens": 200,
        }
    )
    context = SimpleNamespace(
        context_compactions=1,
        completed_tasks=[],
        artifacts=SimpleNamespace(
            snapshot=lambda: {
                "created": 3,
                "chars_persisted": 900,
                "large_output_artifacts": 1,
            }
        ),
    )

    tracker.write(context)
    data = json.loads(captured["text"])

    management = data["context_management"]
    assert management["full_rebase_events"] == 1
    assert management["round_budget_projection_events"] == 1
    assert management["round_budget_results_projected"] == 1
    assert data["artifacts"]["chars_persisted"] == 900


def _allocated_total(breakdown: dict) -> int:
    return sum(item["allocated_tokens"] for item in breakdown.values())


def _share_total(breakdown: dict, field: str) -> float:
    return sum(item[field] for item in breakdown.values())
