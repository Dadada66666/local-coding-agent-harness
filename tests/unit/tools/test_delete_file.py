from __future__ import annotations

from pathlib import Path

import pytest

from runtime.config import RunConfig
from agent.loop import AgentLoop
from runtime.bootstrap import build_runtime
from runtime.security import PermissionBehavior, PermissionMode
from tools.delete_file import DeleteFileTool
from tools.read_file import ReadFileTool
from tools.write_file import WriteFileTool


def make_context(tmp_path: Path, permission_mode: str = PermissionMode.ACCEPT_EDITS):
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode=permission_mode,
        config=RunConfig(permission_mode=permission_mode),
    )
    return runner.create_context("delete file", include_initial_message=True)


def test_registry_exposes_delete_file() -> None:
    assert "delete_file" in build_runtime().tool_registry.names()


def test_current_task_file_can_be_cleaned_up_automatically(tmp_path: Path) -> None:
    context = make_context(tmp_path)
    write_result = WriteFileTool().call(
        {"path": "temporary.py", "content": "value = 1\n"},
        context,
    )

    decision = context.permission_gate.check(
        DeleteFileTool(),
        {"path": "temporary.py"},
        context,
    )
    delete_result = DeleteFileTool().call({"path": "temporary.py"}, context)

    assert write_result.ok is True
    assert decision.behavior == PermissionBehavior.ALLOW
    assert decision.risk == "task_created_file_cleanup"
    assert delete_result.ok is True
    assert not (tmp_path / "temporary.py").exists()
    assert context.task_changed_files == set()
    assert context.changed_files == set()
    assert context.task_created_files == set()
    assert context.created_files == set()


def test_preexisting_file_deletion_requires_approval(tmp_path: Path) -> None:
    path = tmp_path / "existing.py"
    path.write_text("value = 1\n", encoding="utf-8")
    context = make_context(tmp_path)
    ReadFileTool().call({"path": "existing.py"}, context)

    decision = context.permission_gate.check(
        DeleteFileTool(),
        {"path": "existing.py"},
        context,
    )

    assert decision.behavior == PermissionBehavior.ASK
    assert decision.risk == "preexisting_file_delete"
    assert decision.terminal_on_deny is False


def test_delete_requires_a_current_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "existing.py"
    path.write_text("value = 1\n", encoding="utf-8")
    context = make_context(tmp_path)

    result = DeleteFileTool().call({"path": "existing.py"}, context)

    assert result.ok is False
    assert result.error == "file not read"
    assert path.exists()


def test_delete_rejects_file_changed_since_snapshot(tmp_path: Path) -> None:
    path = tmp_path / "existing.py"
    path.write_text("value = 1\n", encoding="utf-8")
    context = make_context(tmp_path)
    ReadFileTool().call({"path": "existing.py"}, context)
    path.write_text("value = 2\n", encoding="utf-8")

    result = DeleteFileTool().call({"path": "existing.py"}, context)

    assert result.ok is False
    assert result.error == "stale file"
    assert path.exists()


def test_delete_rejects_directories_and_protected_paths(tmp_path: Path) -> None:
    (tmp_path / "folder").mkdir()
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    context = make_context(tmp_path)

    directory_result = DeleteFileTool().call({"path": "folder"}, context)
    protected_decision = context.permission_gate.check(
        DeleteFileTool(),
        {"path": ".env"},
        context,
    )

    assert directory_result.ok is False
    assert directory_result.error == "not a file"
    assert protected_decision.behavior == PermissionBehavior.DENY
    assert protected_decision.risk == "protected_delete"
    assert protected_decision.terminal_on_deny is True


def test_delete_path_escape_is_terminal(tmp_path: Path) -> None:
    context = make_context(tmp_path)

    decision = context.permission_gate.check(
        DeleteFileTool(),
        {"path": "../outside.py"},
        context,
    )

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.risk == "path_escape"
    assert decision.terminal_on_deny is True


def test_delete_rejects_symlinks(tmp_path: Path) -> None:
    target = tmp_path / "target.py"
    link = tmp_path / "link.py"
    target.write_text("value = 1\n", encoding="utf-8")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    context = make_context(tmp_path)

    result = DeleteFileTool().call({"path": "link.py"}, context)

    assert result.ok is False
    assert result.error == "symlink delete unsupported"
    assert target.exists()
    assert link.exists()
