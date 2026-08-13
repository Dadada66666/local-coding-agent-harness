from __future__ import annotations

import json
from types import SimpleNamespace

from runtime.observability.readable_trace_writer import ReadableTraceWriter


def test_readable_trace_writer_renders_clean_user_and_assistant_messages(tmp_path) -> None:
    context = SimpleNamespace(
        run_dir=tmp_path,
        run_id="run-1",
        task="Create a sorter",
        messages=[
            {"role": "user", "content": "Create quick_sort.py"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will inspect the repo."},
                    {"type": "tool_use", "name": "list_dir", "input": {"path": "."}},
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "call_1",
                        "content": "tools/\nruntime/",
                    }
                ],
            },
            {
                "role": "user",
                "content": "The previous test run failed. Analyze the failure and fix the code.",
            },
        ],
    )

    path = ReadableTraceWriter().write(context)
    content = path.read_text(encoding="utf-8")

    assert "Create quick_sort.py" in content
    assert "I will inspect the repo." in content
    assert "tool_use `list_dir`" in content
    assert "tools/" not in content
    assert "The previous test run failed" not in content


def test_readable_trace_writer_renders_tool_failures_without_full_output(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    events = [
        {
            "type": "permission_decision",
            "tool_call_id": "call_1",
            "behavior": "allow",
            "risk": "file_write",
        },
        {
            "type": "tool_result",
            "tool_call_id": "call_1",
            "tool": "edit_file",
            "ok": False,
            "error": "old_text not found",
            "output_preview": "old_text not found",
        },
        {
            "type": "task_cancelled",
            "tool_call_id": "call_2",
            "decision": {"risk": "protected_write", "message": "Permission denied"},
        },
    ]
    trace_path.write_text(
        "\n".join(json.dumps(event, ensure_ascii=False) for event in events),
        encoding="utf-8",
    )
    context = SimpleNamespace(
        run_dir=tmp_path,
        run_id="run-1",
        task="Edit a file",
        trace=SimpleNamespace(path=trace_path),
        messages=[
            {"role": "user", "content": "Please edit the file"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "edit_file",
                        "input": {"path": "demo.py", "old_text": "x", "new_text": "y"},
                    }
                ],
            },
        ],
    )

    path = ReadableTraceWriter().write(context)
    content = path.read_text(encoding="utf-8")

    assert "result: failed - old_text not found" in content
    assert "task cancelled: protected_write - Permission denied" in content


def test_readable_trace_distinguishes_projection_compaction_and_artifacts(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    events = [
        {
            "type": "tool_result_budget",
            "replaced_results": 1,
            "saved_tokens": 100,
        },
        {
            "type": "context_tool_results_projected",
            "reason": "eager_tool_result_projection",
            "projected_results": 2,
            "saved_tokens": 200,
        },
        {
            "type": "context_compact",
            "mode": "full",
            "saved_tokens": 300,
        },
        {
            "type": "artifact_persisted",
            "creation_reason": "context_projection",
            "chars_persisted": 400,
        },
        {
            "type": "tool_result",
            "metadata": {
                "rehydration": True,
                "source_path": "game.js",
                "returned_line_start": 1,
                "returned_line_end": 350,
            },
        },
    ]
    trace_path.write_text(
        "\n".join(json.dumps(event) for event in events),
        encoding="utf-8",
    )
    context = SimpleNamespace(
        run_dir=tmp_path,
        run_id="run-1",
        task="inspect",
        trace=SimpleNamespace(path=trace_path),
        messages=[],
    )

    content = ReadableTraceWriter().write(context).read_text(encoding="utf-8")

    assert "context round-budget tool-results projected" in content
    assert "context tool-results projected (eager_tool_result_projection)" in content
    assert "context compacted (full)" in content
    assert "artifact persisted (context_projection)" in content
    assert "source rehydrated: game.js lines 1-350" in content
