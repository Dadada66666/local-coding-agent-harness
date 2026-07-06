from __future__ import annotations

import hashlib
from pathlib import Path

from agent.context import RunConfig
from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from runtime.bootstrap import build_runtime
from tools.create_file import CreateFileTool
from tools.edit_file import EditFileTool
from tools.read_file import ReadFileTool


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def call(self, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def tool_response(*tool_calls: ToolCall) -> ModelResponse:
    return ModelResponse(
        message={
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in tool_calls
            ],
        },
        tool_calls=list(tool_calls),
        usage=TokenUsage(),
    )


def final_response(text: str = "done") -> ModelResponse:
    return ModelResponse(
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
        text=text,
        usage=TokenUsage(),
    )


def make_context(tmp_path: Path):
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )
    return runner.create_context("edit file", include_initial_message=True)


def test_edit_file_batch_applies_multiple_replacements(tmp_path: Path) -> None:
    path = tmp_path / "demo.py"
    path.write_text('name = "old"\ncount = 1\n', encoding="utf-8")
    context = make_context(tmp_path)
    ReadFileTool().call({"path": "demo.py"}, context)

    result = EditFileTool().call(
        {
            "path": "demo.py",
            "edits": [
                {"old_text": 'name = "old"', "new_text": 'name = "new"'},
                {"old_text": "count = 1", "new_text": "count = 2"},
            ],
        },
        context,
    )

    assert result.ok is True
    assert result.metadata["edit_count"] == 2
    assert result.metadata["snapshot_updated"] is True
    assert path.read_text(encoding="utf-8") == 'name = "new"\ncount = 2\n'
    assert context.changed_files == {"demo.py"}

    snapshot = context.read_file_state[str(path)]
    raw = path.read_bytes()
    assert snapshot.mtime_ns == path.stat().st_mtime_ns
    assert snapshot.sha256 == hashlib.sha256(raw).hexdigest()


def test_edit_file_batch_failure_is_atomic(tmp_path: Path) -> None:
    path = tmp_path / "demo.py"
    original = "alpha\nbeta\n"
    path.write_text(original, encoding="utf-8")
    context = make_context(tmp_path)
    ReadFileTool().call({"path": "demo.py"}, context)

    result = EditFileTool().call(
        {
            "path": "demo.py",
            "edits": [
                {"old_text": "alpha", "new_text": "ALPHA"},
                {"old_text": "missing", "new_text": "MISSING"},
            ],
        },
        context,
    )

    assert result.ok is False
    assert result.error == "old_text not found"
    assert result.metadata["failed_edit"] == 2
    assert path.read_text(encoding="utf-8") == original
    assert context.changed_files == set()


def test_edit_file_success_updates_snapshot_for_next_edit(tmp_path: Path) -> None:
    path = tmp_path / "demo.py"
    path.write_text("a = 1\nb = 2\n", encoding="utf-8")
    context = make_context(tmp_path)
    ReadFileTool().call({"path": "demo.py"}, context)

    first = EditFileTool().call({"path": "demo.py", "old_text": "a = 1", "new_text": "a = 10"}, context)
    second = EditFileTool().call({"path": "demo.py", "old_text": "b = 2", "new_text": "b = 20"}, context)

    assert first.ok is True
    assert second.ok is True
    assert path.read_text(encoding="utf-8") == "a = 10\nb = 20\n"


def test_create_file_success_updates_snapshot_for_followup_edit(tmp_path: Path) -> None:
    path = tmp_path / "demo.py"
    context = make_context(tmp_path)

    created = CreateFileTool().call({"path": "demo.py", "content": "a = 1\nb = 2\n"}, context)
    edited = EditFileTool().call({"path": "demo.py", "old_text": "b = 2", "new_text": "b = 20"}, context)

    assert created.ok is True
    assert edited.ok is True
    assert path.read_text(encoding="utf-8") == "a = 1\nb = 20\n"


def test_edit_file_batch_occurrence_replaces_selected_match(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("item\nitem\n", encoding="utf-8")
    context = make_context(tmp_path)
    ReadFileTool().call({"path": "demo.txt"}, context)

    result = EditFileTool().call(
        {
            "path": "demo.txt",
            "edits": [{"old_text": "item", "new_text": "thing", "occurrence": 2}],
        },
        context,
    )

    assert result.ok is True
    assert path.read_text(encoding="utf-8") == "item\nthing\n"


def test_edit_file_replace_all_is_explicit_for_repeated_text(tmp_path: Path) -> None:
    path = tmp_path / "demo.txt"
    path.write_text("foo\nfoo\n", encoding="utf-8")
    context = make_context(tmp_path)
    ReadFileTool().call({"path": "demo.txt"}, context)

    ambiguous = EditFileTool().call({"path": "demo.txt", "old_text": "foo", "new_text": "bar"}, context)
    replaced = EditFileTool().call(
        {"path": "demo.txt", "old_text": "foo", "new_text": "bar", "replace_all": True},
        context,
    )

    assert ambiguous.ok is False
    assert ambiguous.error == "ambiguous edit"
    assert replaced.ok is True
    assert replaced.metadata["edits"] == [{"index": 1, "occurrences": 2}]
    assert path.read_text(encoding="utf-8") == "bar\nbar\n"


def test_edit_file_noop_does_not_mark_file_changed(tmp_path: Path) -> None:
    path = tmp_path / "demo.py"
    path.write_text("a = 1\n", encoding="utf-8")
    context = make_context(tmp_path)
    ReadFileTool().call({"path": "demo.py"}, context)

    result = EditFileTool().call({"path": "demo.py", "old_text": "a = 1", "new_text": "a = 1"}, context)

    assert result.ok is True
    assert result.metadata["changed"] is False
    assert path.read_text(encoding="utf-8") == "a = 1\n"
    assert context.changed_files == set()


def test_agent_loop_can_run_multiple_edit_file_calls_without_reread(tmp_path: Path) -> None:
    path = tmp_path / "demo.py"
    path.write_text("a = 1\nb = 2\n", encoding="utf-8")
    model = FakeModelClient(
        [
            tool_response(ToolCall("read", "read_file", {"path": "demo.py"})),
            tool_response(
                ToolCall("edit_1", "edit_file", {"path": "demo.py", "old_text": "a = 1", "new_text": "a = 10"}),
                ToolCall("edit_2", "edit_file", {"path": "demo.py", "old_text": "b = 2", "new_text": "b = 20"}),
            ),
            final_response(),
        ]
    )
    runner = AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )
    context = runner.create_context("edit file", include_initial_message=True)

    runner.run_until_idle(context)

    assert model.calls == 3
    assert path.read_text(encoding="utf-8") == "a = 10\nb = 20\n"


def test_agent_loop_can_create_then_edit_file_without_reread(tmp_path: Path) -> None:
    path = tmp_path / "demo.py"
    model = FakeModelClient(
        [
            tool_response(
                ToolCall("create", "create_file", {"path": "demo.py", "content": "a = 1\nb = 2\n"}),
                ToolCall("edit", "edit_file", {"path": "demo.py", "old_text": "b = 2", "new_text": "b = 20"}),
            ),
            final_response(),
        ]
    )
    runner = AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )
    context = runner.create_context("create then edit", include_initial_message=True)

    runner.run_until_idle(context)

    assert model.calls == 2
    assert path.read_text(encoding="utf-8") == "a = 1\nb = 20\n"
