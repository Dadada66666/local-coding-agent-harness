from __future__ import annotations

import os
from pathlib import Path

import pytest

from agent.messages import ToolCall
from runtime.config import RunConfig
from agent.loop import AgentLoop
from runtime.bootstrap import build_runtime
from tools.base import ToolValidationError
from tools.read_file import DEFAULT_LIMIT, ReadFileTool


def make_context(tmp_path: Path, **config_overrides):
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", **config_overrides),
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
    assert result.content.endswith(
        "[read_file: demo.py | lines 2-3 / 4 | next_offset=3]"
    )


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


def test_default_pagination_reduces_round_trips_for_a_1100_line_source(
    tmp_path: Path,
) -> None:
    source = "\n".join(
        f'const value_{index} = "component-{index}"; // update animation and collision state'
        for index in range(1100)
    )
    legacy_root = tmp_path / "legacy"
    optimized_root = tmp_path / "optimized"
    legacy_root.mkdir()
    optimized_root.mkdir()
    (legacy_root / "large_demo.js").write_text(source, encoding="utf-8")
    (optimized_root / "large_demo.js").write_text(source, encoding="utf-8")
    tool = ReadFileTool()

    legacy_context = make_context(legacy_root, max_tool_result_chars=8000)
    legacy_pages = _read_all_pages(
        tool,
        legacy_context,
        path="large_demo.js",
        limit=200,
    )
    optimized_context = make_context(optimized_root)
    optimized_pages = _read_all_pages(
        tool,
        optimized_context,
        path="large_demo.js",
    )

    assert DEFAULT_LIMIT == 350
    assert len(optimized_pages) * 10 <= len(legacy_pages) * 7
    assert sum(page.metadata["returned_lines"] for page in optimized_pages) == 1100
    assert optimized_context.source_read_metrics.unique_source_lines_returned == 1100
    assert optimized_context.source_read_metrics.duplicate_source_lines_returned == 0
    assert optimized_pages[-1].metadata["fully_scanned"] is True
    final_page = optimized_pages[-1]
    assert final_page.content.endswith(
        "[read_file: large_demo.js | lines "
        f"{final_page.metadata['returned_line_start']}-1100 / 1100 | complete]"
    )


def test_char_limited_pages_have_continuous_offsets_without_duplicate_lines(
    tmp_path: Path,
) -> None:
    path = tmp_path / "large_demo.js"
    path.write_text(
        "\n".join(f"const value_{index} = '{'x' * 80}';" for index in range(1100)),
        encoding="utf-8",
    )
    context = make_context(tmp_path)

    pages = _read_all_pages(ReadFileTool(), context, path="large_demo.js")

    expected_offset = 0
    for page in pages:
        assert page.metadata["offset"] == expected_offset
        assert len(page.content) <= context.config.max_tool_result_chars
        expected_offset += page.metadata["returned_lines"]
        assert page.metadata["next_offset"] in {expected_offset, None}
    assert expected_offset == 1100
    assert context.read_file_segments[str(path)].covered_ranges == [(0, 1100)]
    assert context.source_read_metrics.duplicate_source_lines_returned == 0


@pytest.mark.parametrize(
    "source",
    [
        "x" * 50_000,
        f'const payload = "{"x" * 50_000}";',
        "function bundled(){return 1;}" * 3000,
    ],
    ids=("plain-long-line", "long-string-source", "minified-source"),
)
def test_oversized_single_line_is_strictly_char_bounded(
    tmp_path: Path,
    source: str,
) -> None:
    (tmp_path / "minified.js").write_text(source, encoding="utf-8")
    context = make_context(tmp_path, max_tool_result_chars=512)

    result = ReadFileTool().call({"path": "minified.js"}, context)

    assert result.ok is True
    assert len(result.content) <= 512
    assert result.metadata["page_limited_by_chars"] is True
    assert result.metadata["source_line_truncated"] is True
    assert result.metadata["reconstructible"] is False
    assert result.metadata["fully_scanned"] is False
    assert result.metadata["new_lines"] == 0
    assert "line truncated" in result.content
    assert result.content.endswith("| line_truncated]")


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
    runtime = build_runtime()
    tool = runtime.tool_registry.get("read_file")
    for index, offset in enumerate(range(0, 951, 200)):
        runtime.executor.execute(
            ToolCall(
                f"scan-{index}",
                "read_file",
                {"path": "game.js", "offset": offset, "limit": 200},
            ),
            context,
        )

    redundant = runtime.executor.execute(
        ToolCall(
            "redundant",
            "read_file",
            {"path": "game.js", "offset": 0, "limit": 200},
        ),
        context,
    )

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


def test_projected_fully_scanned_source_can_rehydrate_once(tmp_path: Path) -> None:
    path = tmp_path / "index.html"
    path.write_text(
        "\n".join(f"<div id='item-{index}'>value</div>" for index in range(80)),
        encoding="utf-8",
    )
    context = make_context(tmp_path)
    runtime = build_runtime()

    initial = runtime.executor.execute(
        ToolCall("initial", "read_file", {"path": "index.html"}),
        context,
    )
    state = context.read_file_segments[str(path)]
    assert initial.metadata["fully_scanned"] is True
    assert state.unprojected_observation_count == 1

    context.mark_source_observation_projected("initial", initial.metadata)
    assert state.unprojected_observation_count == 0

    rehydrated = runtime.executor.execute(
        ToolCall("rehydrated", "read_file", {"path": "index.html"}),
        context,
    )
    assert rehydrated.metadata["redundant_source"] is False
    assert rehydrated.metadata["rehydration"] is True
    assert rehydrated.metadata["rehydrated_lines"] == 80
    assert rehydrated.metadata["returned_lines"] == 80
    assert "item-79" in rehydrated.content
    assert state.unprojected_observation_count == 1
    assert context.source_read_metrics.rehydration_reads == 1
    assert context.source_read_metrics.rehydrated_source_lines == 80
    snapshot = context.source_efficiency_snapshot()
    assert snapshot["duplicate_source_lines_returned"] == 80
    assert snapshot["rehydrated_source_lines"] == 80
    assert snapshot["non_rehydration_overlap_lines"] == 0

    redundant = runtime.executor.execute(
        ToolCall("redundant", "read_file", {"path": "index.html"}),
        context,
    )
    assert redundant.metadata["redundant_source"] is True
    assert redundant.metadata["rehydration"] is False
    assert redundant.metadata["returned_lines"] == 0
    assert context.source_read_metrics.rehydration_reads == 1


def test_broad_duplicate_protection_is_independent_of_default_page_size(
    tmp_path: Path,
) -> None:
    (tmp_path / "large.py").write_text(
        "\n".join(f"line {index}" for index in range(2000)),
        encoding="utf-8",
    )
    context = make_context(tmp_path)
    runtime = build_runtime()
    for index, offset in enumerate(range(0, 2000, 200)):
        runtime.executor.execute(
            ToolCall(
                f"scan-{index}",
                "read_file",
                {"path": "large.py", "offset": offset, "limit": 200},
            ),
            context,
        )

    broad = runtime.executor.execute(
        ToolCall(
            "broad",
            "read_file",
            {"path": "large.py", "offset": 0, "limit": 200},
        ),
        context,
    )
    narrow = runtime.executor.execute(
        ToolCall(
            "narrow",
            "read_file",
            {"path": "large.py", "offset": 100, "limit": 20},
        ),
        context,
    )

    assert broad.metadata["redundant_source"] is True
    assert broad.metadata["returned_lines"] == 0
    assert narrow.metadata["redundant_source"] is False
    assert narrow.metadata["returned_lines"] == 20
    assert narrow.metadata["repeated_segment"] is True


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


def _read_all_pages(
    tool: ReadFileTool,
    context,
    *,
    path: str,
    limit: int | None = None,
) -> list:
    pages = []
    offset = 0
    while True:
        args = {"path": path, "offset": offset}
        if limit is not None:
            args["limit"] = limit
        result = tool.call(args, context)
        pages.append(result)
        next_offset = result.metadata["next_offset"]
        if next_offset is None:
            return pages
        assert next_offset > offset
        offset = next_offset
