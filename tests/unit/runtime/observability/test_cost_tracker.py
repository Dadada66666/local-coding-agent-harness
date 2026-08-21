from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent.messages import TokenUsage
from runtime.observability.cost_tracker import CostTracker


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
            input_tokens=40,
            output_tokens=20,
            cache_creation_input_tokens=20,
            cache_read_input_tokens=40,
        ),
        context_generation=3,
        plan_phase="executing",
    )

    path = tracker.write()
    data = json.loads(captured["text"])
    turn = data["token_breakdown"]["turns"][0]

    assert path == Path("unused-run-dir") / "cost.json"
    assert data["calls"] == 1
    assert data["input_tokens"] == 40
    assert data["logical_input_tokens"] == 100
    assert data["cache_creation_input_tokens"] == 20
    assert data["cache_read_input_tokens"] == 40
    assert data["output_tokens"] == 20
    assert data["total_tokens"] == 60
    assert data["logical_total_tokens"] == 120
    assert turn["turn_id"] == 1
    assert turn["input_breakdown"]["system_prompt"]["estimated_tokens"] > 0
    assert turn["input_breakdown"]["tool_schemas"]["estimated_tokens"] > 0
    assert turn["input_breakdown"]["assistant_tool_calls"]["estimated_tokens"] > 0
    assert turn["input_breakdown"]["tool_results"]["estimated_tokens"] > 0
    assert turn["output_breakdown"]["assistant_text"]["estimated_tokens"] > 0
    assert turn["logical_input_tokens"] == 100
    assert turn["total_tokens"] == 60
    assert turn["logical_total_tokens"] == 120
    assert turn["request_prefix"]["previous_messages_preserved"] is None
    assert turn["request_prefix"]["context_generation"] == 3
    assert turn["request_prefix"]["plan_phase"] == "executing"
    assert len(turn["request_prefix"]["system_hash"]) == 64
    assert len(turn["request_prefix"]["tools_hash"]) == 64
    assert _allocated_total(turn["input_breakdown"]) == 100
    assert _allocated_total(turn["output_breakdown"]) == 20


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
