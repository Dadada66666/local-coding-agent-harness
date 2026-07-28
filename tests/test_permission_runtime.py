from __future__ import annotations

from pathlib import Path

from agent.context import RunConfig
from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from runtime.bootstrap import build_runtime
from runtime.permission import BashRisk, PermissionBehavior, PermissionMode, RiskClassifier
from tools.bash import BashTool
from tools.base import ToolResult
from tools.edit_file import EditFileTool
from tools.list_dir import ListDirTool
from tools.read_file import ReadFileTool
from tools.write_file import WriteFileTool


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


def make_runner(tmp_path: Path, permission_mode: str, model_client=None) -> AgentLoop:
    return AgentLoop(
        model_client=model_client or FakeModelClient([]),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode=permission_mode,
        config=RunConfig(permission_mode=permission_mode),
    )


def test_read_only_write_file_denied_terminal(monkeypatch, tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(
                ToolCall("call_1", "write_file", {"path": "one.py", "content": "x = 1\n"}),
                ToolCall("call_2", "write_file", {"path": "two.py", "content": "x = 2\n"}),
            )
        ]
    )
    runner = make_runner(tmp_path, PermissionMode.READ_ONLY, model)
    context = runner.create_context("write files", include_initial_message=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    runner.run_until_idle(context)

    assert context.finished is True
    assert context.success is False
    assert not (tmp_path / "one.py").exists()
    assert not (tmp_path / "two.py").exists()
    assert context.denied_permission_scopes == {"write:create:one.py"}
    assert model.calls == 1
    result_message = context.messages[-1]
    assert [block["tool_use_id"] for block in result_message["content"]] == ["call_1", "call_2"]
    assert result_message["content"][0]["is_error"] is True
    assert result_message["content"][1]["is_error"] is True
    assert "cancelled" in result_message["content"][1]["content"].lower()


def test_read_only_write_file_allowed_once(monkeypatch, tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(ToolCall("call_1", "write_file", {"path": "one.py", "content": "x = 1\n"})),
            final_response(),
        ]
    )
    runner = make_runner(tmp_path, PermissionMode.READ_ONLY, model)
    context = runner.create_context("write file", include_initial_message=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    runner.run_until_idle(context)

    assert (tmp_path / "one.py").read_text(encoding="utf-8") == "x = 1\n"
    assert context.approved_permission_scopes == set()


def test_read_only_write_file_allow_scope(monkeypatch, tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(ToolCall("call_1", "write_file", {"path": "one.py", "content": "x = 1\n"})),
            final_response(),
        ]
    )
    runner = make_runner(tmp_path, PermissionMode.READ_ONLY, model)
    context = runner.create_context("write file", include_initial_message=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "a")

    runner.run_until_idle(context)

    assert (tmp_path / "one.py").exists()
    assert "write:create:one.py" in context.approved_permission_scopes
    decision = context.permission_gate.check(
        WriteFileTool(),
        {"path": "two.py", "content": "x = 2\n"},
        context,
    )
    assert decision.behavior == PermissionBehavior.ASK
    assert decision.proposed_scope == "write:create:two.py"


def test_bash_apply_patch_heredoc_no_pivot_false_positive() -> None:
    command = """apply_patch <<'PATCH'
*** Begin Patch
*** Add File: quick_sort.py
+right = [num for num in nums if num > pivot]
*** End Patch
PATCH"""

    decision = RiskClassifier().classify_bash(command)

    assert decision.risk == BashRisk.FILE_WRITE_VIA_BASH
    assert "pivot]" not in decision.target_paths
    assert decision.target_paths == []


def test_bash_cat_heredoc_uses_header_redirection_only() -> None:
    command = """cat <<'EOF' > quick_sort.py
right = [num for num in nums if num > pivot]
EOF"""

    decision = RiskClassifier().classify_bash(command)

    assert decision.risk == BashRisk.FILE_WRITE_VIA_BASH
    assert decision.target_paths == ["quick_sort.py"]


def test_bash_new_file_write_suggests_write_file() -> None:
    decision = RiskClassifier().classify_bash("Set-Content demo.py 'x = 1'")

    assert decision.risk == BashRisk.FILE_WRITE_VIA_BASH
    assert decision.suggested_tool == "write_file"


def test_approved_bash_file_write_records_task_mutation(monkeypatch, tmp_path: Path) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("write through shell", include_initial_message=True)
    bash_tool = runner.runtime.tool_registry.get("bash")
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")
    monkeypatch.setattr(
        bash_tool,
        "call",
        lambda args, current_context: ToolResult(ok=True, content="written"),
    )

    result = runner.runtime.executor.execute(
        ToolCall("call_1", "bash", {"command": "echo x > demo.py"}),
        context,
    )

    assert result.ok is True
    assert context.has_task_mutations() is True
    assert context.task_changed_files == {"demo.py"}
    assert result.metadata["mutation_recorded"] is True
    assert result.metadata["mutation_paths"] == ["demo.py"]


def test_protected_agent_dir_hidden_or_denied(tmp_path: Path) -> None:
    (tmp_path / ".agent" / "runs").mkdir(parents=True)
    (tmp_path / ".agent" / "runs" / "trace.jsonl").write_text("{}", encoding="utf-8")
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("inspect", include_initial_message=True)

    result = ListDirTool().call({"path": "."}, context)
    decision = context.permission_gate.check(
        ReadFileTool(),
        {"path": ".agent/runs/trace.jsonl"},
        context,
    )

    assert ".agent" not in result.content
    assert decision.behavior == PermissionBehavior.DENY
    assert decision.risk == "protected_read"


def test_accept_edits_allows_normal_write_file(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("write file", include_initial_message=True)

    decision = context.permission_gate.check(
        WriteFileTool(),
        {"path": "normal.py", "content": "x = 1\n"},
        context,
    )

    assert decision.behavior == PermissionBehavior.ALLOW


def test_existing_file_is_tool_failure_not_permission_denial(tmp_path: Path) -> None:
    (tmp_path / "normal.py").write_text("x = 1\n", encoding="utf-8")
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("write file", include_initial_message=True)
    args = {"path": "normal.py", "content": "x = 2\n"}

    decision = context.permission_gate.check(WriteFileTool(), args, context)
    result = WriteFileTool().call(args, context)

    assert decision.behavior == PermissionBehavior.ALLOW
    assert result.ok is False
    assert result.error == "file exists"
    assert context.denied_permission_scopes == set()


def test_invalid_edit_is_tool_failure_and_can_be_retried(tmp_path: Path) -> None:
    path = tmp_path / "normal.py"
    path.write_text("x = 1\n", encoding="utf-8")
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("edit file", include_initial_message=True)
    ReadFileTool().call({"path": "normal.py"}, context)

    wrong_args = {"path": "normal.py", "old_text": "missing", "new_text": "x = 2"}
    decision = context.permission_gate.check(EditFileTool(), wrong_args, context)
    failed = EditFileTool().call(wrong_args, context)

    assert decision.behavior == PermissionBehavior.ALLOW
    assert failed.ok is False
    assert failed.error == "old_text not found"
    assert context.denied_permission_scopes == set()

    fixed = EditFileTool().call({"path": "normal.py", "old_text": "x = 1\n", "new_text": "x = 2\n"}, context)

    assert fixed.ok is True
    assert path.read_text(encoding="utf-8") == "x = 2\n"


def test_accept_edits_denies_sensitive_write(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("write env", include_initial_message=True)

    decision = context.permission_gate.check(
        WriteFileTool(),
        {"path": ".env", "content": "SECRET=1\n"},
        context,
    )

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.risk == "protected_write"


def test_destructive_bash_denied(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("delete files", include_initial_message=True)

    decision = context.permission_gate.check(
        BashTool(),
        {"command": "rm -rf important"},
        context,
    )

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.risk == BashRisk.DESTRUCTIVE
    assert decision.terminal_on_deny is True


def test_report_permission_denied_not_success(monkeypatch, tmp_path: Path) -> None:
    model = FakeModelClient(
        [tool_response(ToolCall("call_1", "write_file", {"path": "one.py", "content": "x = 1\n"}))]
    )
    runner = make_runner(tmp_path, PermissionMode.READ_ONLY, model)
    context = runner.create_context("write file", include_initial_message=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    runner.run_until_idle(context)
    report_path = context.report_writer.write(context)
    report = report_path.read_text(encoding="utf-8")

    assert "Success: false" in report
    assert "Permission denied" in report
