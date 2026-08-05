from __future__ import annotations

import json
from pathlib import Path

from agent.messages import TokenUsage
from runtime.cost_tracker import CostTracker


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
    assert _allocated_total(turn["input_breakdown"]) == 100
    assert _allocated_total(turn["output_breakdown"]) == 20


def test_runtime_checkpoint_is_counted_as_compacted_history() -> None:
    tracker = CostTracker(Path("unused-run-dir"))

    breakdown = tracker._input_breakdown(
        "system",
        [{"role": "user", "content": "[Runtime checkpoint]\n{}"}],
        [],
    )

    assert breakdown["compacted_history"]["estimated_tokens"] > 0
    assert breakdown["user_messages"]["estimated_tokens"] == 0


def _allocated_total(breakdown: dict) -> int:
    return sum(item["allocated_tokens"] for item in breakdown.values())
