from __future__ import annotations

import json
from types import SimpleNamespace

from runtime.readable_trace_writer import ReadableTraceWriter


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
