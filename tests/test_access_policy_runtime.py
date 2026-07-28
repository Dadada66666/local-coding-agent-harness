from __future__ import annotations

from pathlib import Path

from agent.context import RunConfig
from agent.loop import AgentLoop
from runtime.bootstrap import build_runtime
from runtime.permission import PermissionBehavior
from tools.grep import GrepTool
from tools.list_dir import ListDirTool
from tools.read_file import ReadFileTool


def make_context(tmp_path: Path):
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )
    return runner.create_context("inspect", include_initial_message=True)


def test_repository_search_filters_protected_files(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET_MARKER=hidden-value\n", encoding="utf-8")
    (tmp_path / ".agent").mkdir()
    (tmp_path / ".agent" / "private.txt").write_text(
        "SECRET_MARKER=runtime-value\n",
        encoding="utf-8",
    )
    (tmp_path / ".env.example").write_text("SECRET_MARKER=example-value\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("SECRET_MARKER = 'public-code'\n", encoding="utf-8")
    context = make_context(tmp_path)

    listed = ListDirTool().call({"path": "."}, context)
    searched = GrepTool().call({"path": ".", "pattern": "SECRET_MARKER"}, context)

    assert listed.ok is True
    assert ".env\n" not in f"{listed.content}\n"
    assert ".env.example" in listed.content
    assert "hidden-value" not in searched.content
    assert "runtime-value" not in searched.content
    assert ".env:1" not in searched.content
    assert ".agent/private.txt:1" not in searched.content
    assert ".env.example:1" in searched.content
    assert "app.py:1" in searched.content


def test_explicit_protected_read_is_denied_after_path_resolution(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SECRET=hidden\n", encoding="utf-8")
    context = make_context(tmp_path)
    requested_path = "config/../.env"

    decision = context.permission_gate.check(
        ReadFileTool(),
        {"path": requested_path},
        context,
    )
    direct_read = ReadFileTool().call({"path": requested_path}, context)
    direct_search = GrepTool().call({"path": requested_path, "pattern": "SECRET"}, context)

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.risk == "protected_read"
    assert direct_read.error == "protected read"
    assert direct_search.error == "protected read"
