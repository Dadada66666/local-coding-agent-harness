from __future__ import annotations

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
