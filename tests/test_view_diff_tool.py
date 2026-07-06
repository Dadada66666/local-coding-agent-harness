from __future__ import annotations

from pathlib import Path

from agent.context import RunConfig
from agent.loop import AgentLoop
from runtime.bootstrap import build_runtime
from tools.view_diff import ViewDiffTool


def make_context(tmp_path: Path):
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )
    return runner.create_context("view diff", include_initial_message=True)


def test_view_diff_reports_non_git_directory_without_error(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    context = make_context(tmp_path)

    result = ViewDiffTool().call({}, context)

    assert result.ok is True
    assert result.content == "No git repository; diff unavailable."
    assert result.metadata["git_repository"] is False
