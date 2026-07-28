from __future__ import annotations

from pathlib import Path

import pytest

from agent.context import RunConfig
from agent.loop import AgentLoop
from runtime.bootstrap import build_runtime
from tools.base import ToolValidationError
from tools.write_file import WriteFileTool


def make_context(tmp_path: Path):
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )
    return runner.create_context("write file", include_initial_message=True)


def test_registry_exposes_write_file_only() -> None:
    names = build_runtime().tool_registry.names()

    assert "write_file" in names
    assert "create_file" not in names


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"path": "", "content": "text"}, "non-empty string path"),
        ({"path": 123, "content": "text"}, "non-empty string path"),
        ({"path": "demo.txt"}, "requires content"),
        ({"path": "demo.txt", "content": None}, "content must be a string"),
        ({"path": "demo.txt", "content": "\ud800"}, "content must be valid UTF-8"),
    ],
)
def test_write_file_validates_string_arguments(args: dict, message: str) -> None:
    with pytest.raises(ToolValidationError, match=message):
        WriteFileTool().validate(args, context=None)


def test_write_file_creates_nested_file_and_records_snapshot(tmp_path: Path) -> None:
    context = make_context(tmp_path)

    result = WriteFileTool().call(
        {"path": "nested/demo.txt", "content": "hello\n"},
        context,
    )

    path = tmp_path / "nested" / "demo.txt"
    assert result.ok is True
    assert result.metadata["operation"] == "write_file"
    assert result.metadata["write_mode"] == "create"
    assert result.metadata["changed"] is True
    assert result.metadata["snapshot_updated"] is True
    assert path.read_text(encoding="utf-8") == "hello\n"
    assert context.changed_files == {str(Path("nested") / "demo.txt")}
    assert context.read_file_state[str(path)].partial is False


def test_write_file_does_not_overwrite_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("original\n", encoding="utf-8")
    context = make_context(tmp_path)

    result = WriteFileTool().call(
        {"path": "demo.txt", "content": "replacement\n"},
        context,
    )

    assert result.ok is False
    assert result.error == "file exists"
    assert path.read_text(encoding="utf-8") == "original\n"
    assert context.changed_files == set()


def test_write_file_rejects_existing_directory(tmp_path: Path) -> None:
    (tmp_path / "existing").mkdir()
    context = make_context(tmp_path)

    result = WriteFileTool().call(
        {"path": "existing", "content": "replacement\n"},
        context,
    )

    assert result.ok is False
    assert result.error == "not a file"
    assert context.changed_files == set()


def test_write_file_rejects_parent_path_that_is_a_file(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    parent.write_text("original\n", encoding="utf-8")
    context = make_context(tmp_path)

    result = WriteFileTool().call(
        {"path": "parent/demo.txt", "content": "replacement\n"},
        context,
    )

    assert result.ok is False
    assert result.error == "parent not a directory"
    assert parent.read_text(encoding="utf-8") == "original\n"
    assert context.changed_files == set()


def test_write_file_does_not_overwrite_path_created_during_call(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = (tmp_path / "race.txt").resolve()
    context = make_context(tmp_path)
    original_open = Path.open

    def open_after_external_create(
        self,
        mode="r",
        buffering=-1,
        encoding=None,
        errors=None,
        newline=None,
    ):
        if self == path and mode == "x":
            with original_open(self, "w", encoding="utf-8") as handle:
                handle.write("external\n")
        return original_open(
            self,
            mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    monkeypatch.setattr(Path, "open", open_after_external_create)

    result = WriteFileTool().call(
        {"path": "race.txt", "content": "agent\n"},
        context,
    )

    assert result.ok is False
    assert result.error == "file exists"
    assert path.read_text(encoding="utf-8") == "external\n"
    assert context.changed_files == set()
