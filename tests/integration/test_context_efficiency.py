from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent.loop import AgentLoop
from agent.messages import ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig


@dataclass(frozen=True)
class TraceResult:
    game_pages: int
    read_file_calls: int
    unique_source_lines: int
    duplicate_source_lines: int
    high_overlap_rereads: int
    full_compactions: int
    tool_result_projections: int
    peak_tool_result_chars: int


def test_larger_pages_and_context_working_set_reduce_trace_churn(tmp_path: Path) -> None:
    legacy = _run_trace(
        tmp_path / "legacy",
        config=RunConfig(
            permission_mode="accept_edits",
            max_tool_result_chars=8000,
            compact_threshold_chars=120000,
            context_target_tokens=32000,
            context_eager_projection_tokens=23040,
            context_recent_target_tokens=8000,
            context_recent_max_tokens=16000,
        ),
        read_limit=200,
    )
    optimized = _run_trace(
        tmp_path / "optimized",
        config=RunConfig(permission_mode="accept_edits"),
        read_limit=None,
    )

    assert optimized.game_pages * 10 <= legacy.game_pages * 7
    assert optimized.read_file_calls < legacy.read_file_calls
    assert optimized.unique_source_lines == legacy.unique_source_lines
    assert optimized.duplicate_source_lines == legacy.duplicate_source_lines
    assert optimized.high_overlap_rereads == legacy.high_overlap_rereads
    assert optimized.full_compactions < legacy.full_compactions
    assert optimized.tool_result_projections <= legacy.tool_result_projections
    assert optimized.peak_tool_result_chars <= 12000
    assert legacy.peak_tool_result_chars <= 8000


def _run_trace(
    root: Path,
    *,
    config: RunConfig,
    read_limit: int | None,
) -> TraceResult:
    root.mkdir()
    (root / "README.md").write_text(
        "\n".join(f"Repository fact {index}" for index in range(100)),
        encoding="utf-8",
    )
    (root / "index.html").write_text(
        "\n".join(f'<div data-index="{index}">game shell</div>' for index in range(150)),
        encoding="utf-8",
    )
    game_lines = [
        f'const value_{index} = "component-{index}"; // update animation and collision state'
        for index in range(1100)
    ]
    (root / "game.js").write_text("\n".join(game_lines), encoding="utf-8")

    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=root,
        permission_mode="accept_edits",
        config=config,
    )
    context = runner.create_context("inspect, improve, and verify the game", True)
    peak_tool_result_chars = 0
    tool_sequence = 0

    def execute(name: str, arguments: dict) -> object:
        nonlocal peak_tool_result_chars, tool_sequence
        tool_sequence += 1
        context.current_turn_id += 1
        context.mark_model_request_consumed(len(context.messages))
        call_id = f"trace-{tool_sequence}"
        context.add_assistant_message(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": arguments,
                    }
                ],
            }
        )
        result = runner.runtime.executor.execute(
            ToolCall(call_id, name, arguments),
            context,
        )
        context.add_tool_result(call_id, result.content)
        peak_tool_result_chars = max(peak_tool_result_chars, len(result.content))
        runner.runtime.context_manager.prepare_context(
            context,
            max_output_tokens=16000,
        )
        return result

    execute("read_file", {"path": "README.md"})
    execute("read_file", {"path": "index.html"})

    game_pages = 0
    offset = 0
    while True:
        arguments = {"path": "game.js", "offset": offset}
        if read_limit is not None:
            arguments["limit"] = read_limit
        result = execute("read_file", arguments)
        game_pages += 1
        next_offset = result.metadata["next_offset"]
        if next_offset is None:
            break
        offset = next_offset

    execute("grep", {"path": "game.js", "pattern": "value_500"})
    execute("read_file", {"path": "game.js", "offset": 490, "limit": 30})
    execute(
        "edit_file",
        {
            "path": "game.js",
            "old_text": game_lines[500],
            "new_text": 'const value_500 = "optimized-component";',
        },
    )
    execute("read_file", {"path": "game.js", "offset": 490, "limit": 30})

    for index in range(8):
        context.current_turn_id += 1
        context.add_assistant_message(
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": f"Execution note {index}: " + ("working-set " * 1450),
                    }
                ],
            }
        )
        context.add_user_message({"role": "user", "content": "Continue verification."})
        runner.runtime.context_manager.prepare_context(
            context,
            max_output_tokens=16000,
        )

    projection_events = [
        event
        for event in context.cost_tracker.context_events
        if event.get("mode") == "tool_results"
    ]
    metrics = context.source_read_metrics
    return TraceResult(
        game_pages=game_pages,
        read_file_calls=metrics.read_file_calls,
        unique_source_lines=metrics.unique_source_lines_returned,
        duplicate_source_lines=metrics.duplicate_source_lines_returned,
        high_overlap_rereads=metrics.high_overlap_rereads,
        full_compactions=context.context_compactions,
        tool_result_projections=len(projection_events),
        peak_tool_result_chars=peak_tool_result_chars,
    )
