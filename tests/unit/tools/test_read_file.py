from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.messages import ToolCall
from runtime.config import RunConfig
from agent.loop import AgentLoop
from runtime.bootstrap import build_runtime
from tools.base import ToolValidationError
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


def test_paginated_scan_merges_coverage_for_a_951_line_source(tmp_path: Path) -> None:
    path = tmp_path / "game.js"
    path.write_text(
        "\n".join(f"const line{index} = {index};" for index in range(951)),
        encoding="utf-8",
    )
    context = make_context(tmp_path)
    tool = ReadFileTool()

    offset = 0
    while offset < 951:
        result = tool.call({"path": "game.js", "offset": offset, "limit": 200}, context)
        offset = result.metadata["next_offset"] or 951

    state = context.read_file_segments[str(path)]
    assert state.covered_ranges == [(0, 951)]
    assert state.fully_scanned is True
    assert context.read_file_state[str(path)].partial is False


def test_read_file_reports_overlap_before_source_is_fully_scanned(tmp_path: Path) -> None:
    (tmp_path / "game.js").write_text(
        "\n".join(f"line {index}" for index in range(951)),
        encoding="utf-8",
    )
    context = make_context(tmp_path)
    tool = ReadFileTool()
    tool.call({"path": "game.js", "offset": 0, "limit": 200}, context)
    tool.call({"path": "game.js", "offset": 200, "limit": 200}, context)

    overlap = tool.call({"path": "game.js", "offset": 100, "limit": 200}, context)

    assert overlap.ok is True
    assert overlap.metadata["already_seen_lines"] == 200
    assert overlap.metadata["new_lines"] == 0
    assert overlap.metadata["overlap_ratio"] == 1.0


def test_unchanged_fully_scanned_source_uses_lightweight_reread_response(
    tmp_path: Path,
) -> None:
    (tmp_path / "game.js").write_text(
        "\n".join(f"line {index}" for index in range(951)),
        encoding="utf-8",
    )
    context = make_context(tmp_path)
    tool = ReadFileTool()
    for offset in range(0, 951, 200):
        tool.call({"path": "game.js", "offset": offset, "limit": 200}, context)

    redundant = tool.call({"path": "game.js", "offset": 0, "limit": 200}, context)

    assert redundant.ok is True
    assert redundant.metadata["redundant_source"] is True
    assert redundant.metadata["returned_lines"] == 0
    assert "already fully scanned" in redundant.content
    assert len(redundant.content) < 600
    assert "force" not in tool.input_schema["properties"]
    with pytest.raises(ToolValidationError, match="unknown read_file fields: force"):
        tool.validate(
            {"path": "game.js", "offset": 0, "limit": 200, "force": True},
            context,
        )


def test_source_mutation_invalidates_coverage(tmp_path: Path) -> None:
    path = tmp_path / "game.js"
    path.write_text("\n".join(f"line {index}" for index in range(400)), encoding="utf-8")
    context = make_context(tmp_path)
    tool = ReadFileTool()
    tool.call({"path": "game.js", "offset": 0, "limit": 200}, context)
    tool.call({"path": "game.js", "offset": 200, "limit": 200}, context)
    old_sha = context.read_file_segments[str(path)].sha256

    path.write_text("changed\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    result = tool.call({"path": "game.js", "offset": 0, "limit": 200}, context)

    state = context.read_file_segments[str(path)]
    assert state.sha256 != old_sha
    assert state.fully_scanned is False
    assert state.covered_ranges == [(0, 200)]
    assert result.metadata["overlap_ratio"] == 0.0
    assert result.metadata["redundant_source"] is False


def test_known_write_recomputes_hash_when_stat_signature_is_unchanged(
    tmp_path: Path,
) -> None:
    path = tmp_path / "game.js"
    path.write_text("one\n", encoding="utf-8")
    context = make_context(tmp_path)
    original_stat = path.stat()
    before = context.record_file_snapshot(path, path.read_bytes(), partial=False)

    path.write_text("two\n", encoding="utf-8")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    after = context.record_file_snapshot(path, path.read_bytes(), partial=False)

    assert path.stat().st_size == original_stat.st_size
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert after.sha256 != before.sha256


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
