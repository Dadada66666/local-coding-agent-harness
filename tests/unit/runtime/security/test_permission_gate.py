from __future__ import annotations

from pathlib import Path

from runtime.config import RunConfig
from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from runtime.bootstrap import build_runtime
from runtime.security import BashRisk, PermissionBehavior, PermissionMode, RiskClassifier
from tools.bash import BashTool
from tools.base import ToolResult
from tools.delete_file import DeleteFileTool
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


def test_read_only_write_file_allowed_once(monkeypatch, tmp_path: Path, capsys) -> None:
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
    assert "[permission] allowed once; executing tool." in capsys.readouterr().out


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
    assert decision.execution_route == "structured_tool"


def test_bash_apply_patch_file_is_also_routed_to_structured_tool() -> None:
    decision = RiskClassifier().classify_bash("apply_patch changes.patch")

    assert decision.risk == BashRisk.FILE_WRITE_VIA_BASH
    assert decision.execution_route == "structured_tool"
    assert decision.suggested_tool == "edit_file"


def test_delete_missing_snapshot_fails_before_approval(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "existing.py"
    path.write_text("value = 1\n", encoding="utf-8")
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("delete existing file", include_initial_message=True)
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt="": (_ for _ in ()).throw(AssertionError("approval was requested")),
    )

    result = runner.runtime.executor.execute(
        ToolCall("delete", "delete_file", {"path": "existing.py"}),
        context,
    )

    assert result.ok is False
    assert result.metadata["validation_error"] is True
    assert "use read_file first" in result.content
    assert path.exists()


def test_delete_preflight_preserves_terminal_path_escape(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("delete outside file", include_initial_message=True)

    result = runner.runtime.executor.execute(
        ToolCall("delete", "delete_file", {"path": "../outside.py"}),
        context,
    )

    assert result.ok is False
    assert result.metadata["risk"] == "path_escape"
    assert result.metadata["terminal_on_deny"] is True
    assert context.finished is True


def test_bash_apply_patch_routes_to_edit_file_without_approval(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("edit through shell patch", include_initial_message=True)
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt="": (_ for _ in ()).throw(AssertionError("approval was requested")),
    )

    decision = context.permission_gate.check(
        BashTool(),
        {
            "command": (
                "apply_patch <<'PATCH'\n"
                "*** Begin Patch\n"
                "*** Update File: demo.py\n"
                "*** End Patch\n"
                "PATCH"
            )
        },
        context,
    )

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.terminal_on_deny is False
    assert decision.decision_reason == "bash_structured_tool_route"
    assert "edit_file" in decision.message


def test_verification_cannot_mix_shell_write_and_test(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("write and verify", include_initial_message=True)

    decision = context.permission_gate.check(
        BashTool(),
        {
            "command": "echo x > demo.py\npython -m pytest",
            "purpose": "verify",
        },
        context,
    )

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.terminal_on_deny is False
    assert decision.decision_reason == "bash_mixed_mutation_verification"
    assert decision.metadata["track_mutation_failure"] is False
    assert "separate Bash call" in decision.message


def test_blocked_mixed_verification_does_not_mark_mutation_failure(
    tmp_path: Path,
) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("verify without mutation", include_initial_message=True)

    result = runner.runtime.executor.execute(
        ToolCall(
            "verify",
            "bash",
            {
                "command": "echo x > temporary.txt\npython -m pytest",
                "purpose": "verify",
            },
        ),
        context,
    )

    assert result.ok is False
    assert result.metadata["denied"] is True
    assert result.metadata["mutation_outcome"] == "not_executed"
    assert context.task_unresolved_mutation_failure is False
    assert not (tmp_path / "temporary.txt").exists()


def test_bash_cat_heredoc_uses_header_redirection_only() -> None:
    command = """cat <<'EOF' > quick_sort.py
right = [num for num in nums if num > pivot]
EOF"""

    decision = RiskClassifier().classify_bash(command)

    assert decision.risk == BashRisk.FILE_WRITE_VIA_BASH
    assert decision.target_paths == ["quick_sort.py"]


def test_quoted_html_is_not_misclassified_as_redirection() -> None:
    command = (
        "node --check game/game.js && "
        "grep -q '恐怖氛围音乐' game/README.md && "
        "grep -q 'M</kbd>' game/index.html && "
        "printf '%s\\n' 'Syntax and horror-theme UI checks: OK'"
    )

    decision = RiskClassifier().classify_bash(command)

    assert decision.risk == BashRisk.SAFE_CHECK
    assert decision.target_paths == []


def test_network_download_reports_host_and_filesystem_effects(tmp_path: Path) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("download asset", include_initial_message=True)
    command = (
        "mkdir -p game/assets && "
        "curl -L -o game/assets/darkest-child.mp3 "
        "'https://incompetech.com/music/darkest-child.mp3'"
    )

    decision = context.permission_gate.check(BashTool(), {"command": command}, context)

    assert decision.behavior == PermissionBehavior.ASK
    assert decision.risk == BashRisk.NETWORK
    assert decision.operation is not None
    assert decision.operation.kind == "fs.write"
    assert decision.operation.paths == ["game/assets/darkest-child.mp3", "game/assets"]
    assert decision.proposed_scope == (
        "bash:network:curl:incompetech.com:game_assets_darkest-child.mp3"
    )
    assert "Hosts: incompetech.com" in decision.message
    assert "Filesystem mutations" in decision.message


def test_chmod_is_classified_as_file_metadata_write() -> None:
    decision = RiskClassifier().classify_bash("chmod +x run_game.sh")

    assert decision.risk == BashRisk.FILE_WRITE_VIA_BASH
    assert decision.target_paths == ["run_game.sh"]
    assert decision.effects == ("file_write",)


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


def test_failed_shell_patch_blocks_success_until_structured_edit_recovers(
    tmp_path: Path,
) -> None:
    path = tmp_path / "demo.py"
    path.write_text("value = 1\n", encoding="utf-8")
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("update demo", include_initial_message=True)

    blocked = runner.runtime.executor.execute(
        ToolCall(
            "patch",
            "bash",
            {
                "command": (
                    "apply_patch <<'PATCH'\n"
                    "*** Begin Patch\n"
                    "*** Update File: demo.py\n"
                    "*** End Patch\n"
                    "PATCH"
                )
            },
        ),
        context,
    )
    context.task_test_result = {"ok": True}
    context.final_text = "done"

    assert blocked.ok is False
    assert context.task_unresolved_mutation_failure is True
    assert runner.infer_success(context) is False

    read_result = runner.runtime.executor.execute(
        ToolCall("read", "read_file", {"path": "demo.py"}),
        context,
    )
    edit_result = runner.runtime.executor.execute(
        ToolCall(
            "edit",
            "edit_file",
            {
                "path": "demo.py",
                "old_text": "value = 1",
                "new_text": "value = 2",
            },
        ),
        context,
    )
    context.task_test_result = {"ok": True}
    context.task_verification_version = context.mutation_version

    assert read_result.ok is True
    assert edit_result.ok is True
    assert context.task_unresolved_mutation_failure is False
    assert edit_result.metadata["mutation_failure_recovered"] is True
    assert runner.infer_success(context) is True


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


def test_unsnapshotted_existing_file_is_tool_failure_not_permission_denial(tmp_path: Path) -> None:
    (tmp_path / "normal.py").write_text("x = 1\n", encoding="utf-8")
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("write file", include_initial_message=True)
    args = {"path": "normal.py", "content": "x = 2\n"}

    decision = context.permission_gate.check(WriteFileTool(), args, context)
    result = WriteFileTool().call(args, context)

    assert decision.behavior == PermissionBehavior.ALLOW
    assert result.ok is False
    assert result.error == "file not read"
    assert result.metadata["recovery_tool"] == "read_file"
    assert result.metadata["delete_not_required"] is True
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


def test_terminal_deny_summary_preserves_earlier_task_changes(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(
                ToolCall(
                    "call_1",
                    "write_file",
                    {"path": "kept.py", "content": "value = 1\n"},
                )
            ),
            tool_response(
                ToolCall("call_2", "bash", {"command": "rm -rf important"})
            ),
        ]
    )
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS, model)
    context = runner.create_context("write then attempt broad delete", include_initial_message=True)

    runner.run_until_idle(context)

    assert context.success is False
    assert context.task_changed_files == {"kept.py"}
    assert "Changed files\n- kept.py" in context.final_text
    assert "Checks run\n- Not run." in context.final_text
    assert (tmp_path / "kept.py").exists()


def test_single_file_bash_delete_routes_to_delete_tool_without_cancelling(
    tmp_path: Path,
) -> None:
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("clean temporary file", include_initial_message=True)

    decision = context.permission_gate.check(
        BashTool(),
        {"command": "rm temporary.py && python3 app.py"},
        context,
    )

    assert decision.behavior == PermissionBehavior.DENY
    assert decision.risk == BashRisk.FILE_DELETE_VIA_BASH
    assert decision.terminal_on_deny is False
    assert "delete_file" in decision.message


def test_agent_can_recover_from_shell_delete_routing_failure(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(
                ToolCall(
                    "call_1",
                    "write_file",
                    {"path": "temporary.py", "content": "value = 1\n"},
                )
            ),
            tool_response(
                ToolCall(
                    "call_2",
                    "bash",
                    {
                        "command": "python -m py_compile temporary.py",
                        "purpose": "verify",
                    },
                )
            ),
            tool_response(
                ToolCall("call_3", "bash", {"command": "rm temporary.py"})
            ),
            tool_response(
                ToolCall("call_4", "delete_file", {"path": "temporary.py"})
            ),
            final_response(),
        ]
    )
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS, model)
    context = runner.create_context("verify and clean up", include_initial_message=True)

    runner.run_until_idle(context)

    assert context.success is True
    assert context.abort_reason is None
    assert context.task_model_calls == 5
    assert context.task_changed_files == set()
    assert not (tmp_path / "temporary.py").exists()


def test_python_file_delete_is_not_an_unknown_bash_bypass() -> None:
    classifier = RiskClassifier()

    pathlib_decision = classifier.classify_bash(
        '''python3 -c "from pathlib import Path; Path('temporary.py').unlink()"'''
    )
    os_decision = classifier.classify_bash(
        '''python3 -c "import os; os.remove('temporary.py')"'''
    )
    powershell_decision = classifier.classify_bash(
        "Get-ChildItem temporary.py | Remove-Item"
    )

    assert pathlib_decision.risk == BashRisk.FILE_DELETE_VIA_BASH
    assert pathlib_decision.target_paths == ["temporary.py"]
    assert os_decision.risk == BashRisk.FILE_DELETE_VIA_BASH
    assert os_decision.target_paths == ["temporary.py"]
    assert powershell_decision.risk == BashRisk.FILE_DELETE_VIA_BASH


def test_verification_command_is_not_treated_as_read_only(tmp_path: Path) -> None:
    read_only_runner = make_runner(tmp_path, PermissionMode.READ_ONLY)
    read_only_context = read_only_runner.create_context("verify", include_initial_message=True)
    accept_runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    accept_context = accept_runner.create_context("verify", include_initial_message=True)
    args = {"command": "python3 -m unittest -v test_demo.py"}

    read_only_decision = read_only_context.permission_gate.check(
        BashTool(),
        args,
        read_only_context,
    )
    accept_decision = accept_context.permission_gate.check(
        BashTool(),
        args,
        accept_context,
    )

    assert read_only_decision.behavior == PermissionBehavior.ASK
    assert read_only_decision.risk == "unknown_operation"
    assert accept_decision.behavior == PermissionBehavior.ALLOW
    assert accept_decision.risk == BashRisk.SAFE_CHECK


def test_preexisting_delete_file_is_not_terminal_when_user_denies(
    monkeypatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "existing.py"
    path.write_text("value = 1\n", encoding="utf-8")
    runner = make_runner(tmp_path, PermissionMode.ACCEPT_EDITS)
    context = runner.create_context("delete existing file", include_initial_message=True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    decision = context.permission_gate.check(
        DeleteFileTool(),
        {"path": "existing.py"},
        context,
    )
    resolved = context.permission_gate.resolve(
        decision,
        DeleteFileTool(),
        {"path": "existing.py"},
        context,
    )

    assert resolved.behavior == PermissionBehavior.DENY
    assert resolved.terminal_on_deny is False
    assert context.finished is False
    assert path.exists()


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
