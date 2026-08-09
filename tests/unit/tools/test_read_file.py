from __future__ import annotations

from pathlib import Path

from agent.messages import ToolCall
from runtime.config import RunConfig
from agent.loop import AgentLoop
from runtime.bootstrap import build_runtime
from tools.read_file import ReadFileTool


def make_context(tmp_path: Path):
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )
    return runner.create_context("read file", include_initial_message=True)


def test_read_file_returns_tool_failure_for_non_utf8_file(tmp_path: Path) -> None:
    path = tmp_path / "data.bin"
    path.write_bytes(b"\xff\xfe\x00")
    context = make_context(tmp_path)

    result = ReadFileTool().call({"path": "data.bin"}, context)

    assert result.ok is False
    assert result.error == "decode error"
    assert "not valid UTF-8" in result.content
    assert str(path) not in context.read_file_state


def test_read_file_returns_explicit_line_cursor_metadata(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    context = make_context(tmp_path)

    result = ReadFileTool().call({"path": "demo.py", "offset": 1, "limit": 2}, context)

    assert result.ok is True
    assert result.metadata["pagination"] == "lines"
    assert result.metadata["returned_line_start"] == 2
    assert result.metadata["returned_line_end"] == 3
    assert result.metadata["next_offset"] == 3
    assert result.metadata["has_more"] is True
    assert "next_offset=3" in result.content


def test_repeated_source_segment_returns_a_non_blocking_hint(tmp_path: Path) -> None:
    (tmp_path / "demo.py").write_text("one\ntwo\nthree\n", encoding="utf-8")
    context = make_context(tmp_path)
    tool = ReadFileTool()

    first = tool.call({"path": "demo.py", "offset": 0, "limit": 2}, context)
    second = tool.call({"path": "demo.py", "offset": 0, "limit": 2}, context)

    assert first.metadata["repeated_segment"] is False
    assert second.metadata["repeated_segment"] is True
    assert "already returned unchanged" in second.content


def test_adjacent_pages_form_a_complete_edit_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "demo.py"
    path.write_text("one\ntwo\nthree\nfour\n", encoding="utf-8")
    context = make_context(tmp_path)
    tool = ReadFileTool()

    first = tool.call({"path": "demo.py", "offset": 0, "limit": 2}, context)
    second = tool.call({"path": "demo.py", "offset": 2, "limit": 2}, context)

    assert first.metadata["partial"] is True
    assert second.metadata["partial"] is False
    assert context.read_file_state[str(path)].partial is False


def test_large_source_page_stays_on_line_cursor_instead_of_artifact_cursor(tmp_path: Path) -> None:
    (tmp_path / "large.py").write_text(
        "\n".join(f"value_{index} = '{'x' * 50}'" for index in range(400)),
        encoding="utf-8",
    )
    context = make_context(tmp_path)
    runtime = build_runtime()

    result = runtime.executor.execute(
        ToolCall("read", "read_file", {"path": "large.py"}),
        context,
    )

    assert result.ok is True
    assert result.artifact_id is None
    assert len(result.content) <= context.config.max_tool_result_chars
    assert result.metadata["has_more"] is True
    assert result.metadata["next_offset"] == result.metadata["returned_lines"]
