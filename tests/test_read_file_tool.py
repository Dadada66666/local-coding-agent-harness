from __future__ import annotations

from pathlib import Path

from agent.context import RunConfig
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
