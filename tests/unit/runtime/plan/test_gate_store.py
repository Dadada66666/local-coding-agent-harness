from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent.messages import ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.plan import PLAN_SCHEMA_VERSION, PlanPolicy
from runtime.security.redaction import SecretRedactor
from runtime.session_factory import create_agent_session
from runtime.task import TaskStatus


def make_context(tmp_path: Path, policy: PlanPolicy):
    return create_agent_session(
        repo_path=tmp_path,
        task="change the repository",
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", plan_policy=policy),
        initial_messages=[{"role": "user", "content": "change the repository"}],
        system_prompt="system",
        include_initial_message=True,
    )


def trace_events(context) -> list[dict]:
    return [
        json.loads(line)
        for line in context.trace.path.read_text(encoding="utf-8").splitlines()
    ]


def test_undecided_gate_allows_read_and_blocks_write_before_permission(tmp_path) -> None:
    context = make_context(tmp_path, PlanPolicy.AUTO)
    runtime = build_runtime()

    read_result = runtime.executor.execute(ToolCall("read", "list_dir", {}), context)
    write_result = runtime.executor.execute(
        ToolCall("write", "write_file", {"path": "demo.txt", "content": "x"}),
        context,
    )

    assert read_result.ok is True
    assert write_result.ok is False
    assert write_result.metadata["blocked_by"] == "tool_capability"
    assert write_result.metadata["capability_reason"] == "hidden_by_runtime_state"
    assert write_result.metadata["plan_phase"] == "inactive"
    assert not (tmp_path / "demo.txt").exists()
    assert context.mutation_version == 0
    events = trace_events(context)
    assert not any(
        event["type"] == "permission_decision"
        and event.get("tool_call_id") == "write"
        for event in events
    )


def test_planning_and_awaiting_approval_block_bash_without_repair_state(tmp_path) -> None:
    context = make_context(tmp_path, PlanPolicy.REQUIRED)
    runtime = build_runtime()

    planning_result = runtime.executor.execute(
        ToolCall("bash-1", "bash", {"command": "echo hello"}),
        context,
    )
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Perform the change"}]
    )
    context.plan_controller.submit_for_execution()
    awaiting_result = runtime.executor.execute(
        ToolCall("bash-2", "bash", {"command": "echo hello"}),
        context,
    )
    forged_read = runtime.executor.execute(
        ToolCall("read-while-waiting", "read_file", {"path": "missing.py"}),
        context,
    )
    malformed_edit = runtime.executor.execute(
        ToolCall(
            "malformed-edit-while-waiting",
            "edit_file",
            {
                "path": "demo.py",
                "old_string": "before",
                "new_string": "after",
            },
        ),
        context,
    )

    assert planning_result.metadata["plan_phase"] == "planning"
    assert awaiting_result.metadata["plan_phase"] == "awaiting_approval"
    assert forged_read.metadata["blocked_by"] == "tool_capability"
    assert forged_read.metadata["plan_phase"] == "awaiting_approval"
    assert malformed_edit.metadata["blocked_by"] == "tool_capability"
    assert malformed_edit.metadata["model_contract_violation"] is True
    assert "validation_error" not in malformed_edit.metadata
    assert context.mutation_version == 0
    assert context.task_unresolved_mutation_failure is False
    assert context.task_tool_failures == []
    progress = runtime.progress_policy.evaluate(
        context,
        SimpleNamespace(usage=SimpleNamespace(output_tokens=0)),
        [(ToolCall("bash-2", "bash", {"command": "echo hello"}), awaiting_result)],
        max_output_tokens=4096,
    )
    assert progress.action == "continue"
    assert context.task_failure_fingerprint is None
    assert runtime.recovery_policy.should_inject_retry(context) is False


def test_direct_and_executing_paths_return_to_permission_gate(tmp_path) -> None:
    direct_context = make_context(tmp_path / "direct", PlanPolicy.AUTO)
    runtime = build_runtime()
    selected = runtime.executor.execute(
        ToolCall(
            "select",
            "select_execution_mode",
            {"mode": "direct", "reason": "local low-risk file"},
        ),
        direct_context,
    )
    direct_write = runtime.executor.execute(
        ToolCall("write-direct", "write_file", {"path": "direct.txt", "content": "ok"}),
        direct_context,
    )

    executing_context = make_context(tmp_path / "executing", PlanPolicy.REQUIRED)
    executing_context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Write the file"}]
    )
    executing_context.plan_controller.submit_for_execution()
    executing_context.plan_controller.approve()
    executing_write = runtime.executor.execute(
        ToolCall("write-plan", "write_file", {"path": "planned.txt", "content": "ok"}),
        executing_context,
    )

    assert selected.ok is True
    assert direct_write.ok is True
    assert executing_write.ok is True
    assert (direct_context.repo_path / "direct.txt").read_text(encoding="utf-8") == "ok"
    assert (executing_context.repo_path / "planned.txt").read_text(encoding="utf-8") == "ok"


def test_plan_snapshot_is_atomic_parseable_and_bounded(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path, PlanPolicy.REQUIRED)
    replacements = []
    real_replace = __import__("os").replace

    def recording_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr("runtime.plan.store.os.replace", recording_replace)
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Inspect and modify src/runtime"}],
        explanation="A concrete repository plan",
    )
    context.plan_controller.submit_for_execution()

    plan_path = context.run_dir / "plan.json"
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == PLAN_SCHEMA_VERSION
    assert payload["phase"] == "awaiting_approval"
    assert payload["version"] == 1
    assert payload["approved_version"] is None
    assert payload["approval_policy"] == "manual"
    assert payload["task_id"] == "task-1"
    assert payload["task_status"] == "waiting_user"
    assert payload["snapshot_purpose"] == "plan audit; not full session recovery"
    assert replacements
    assert replacements[-1][1] == plan_path
    assert not list(context.run_dir.glob(".plan-*.tmp"))


def test_plan_snapshot_redacts_secrets_and_off_creates_no_file(tmp_path) -> None:
    off_context = make_context(tmp_path / "off", PlanPolicy.OFF)
    assert not (off_context.run_dir / "plan.json").exists()

    required_context = make_context(tmp_path / "required", PlanPolicy.REQUIRED)
    required_context.plan_store.redactor = SecretRedactor(("super-secret-value",))
    required_context.plan_controller.replace_plan(
        [
            {
                "id": "step-1",
                "description": "Do not persist super-secret-value in this plan",
            }
        ]
    )
    rendered = (required_context.run_dir / "plan.json").read_text(encoding="utf-8")

    assert "super-secret-value" not in rendered
    assert "[REDACTED]" in rendered


def test_new_off_task_removes_stale_current_plan_snapshot(tmp_path) -> None:
    context = make_context(tmp_path, PlanPolicy.REQUIRED)
    assert (context.run_dir / "plan.json").is_file()

    context.config.plan_policy = PlanPolicy.OFF
    context.transition_task(TaskStatus.CANCELLED, trigger="test_task_finished")
    context.begin_task("a direct compatibility task")

    assert context.plan_state.policy is PlanPolicy.OFF
    assert not (context.run_dir / "plan.json").exists()


def test_snapshot_write_failure_is_nonfatal_and_traced(tmp_path, monkeypatch) -> None:
    context = make_context(tmp_path, PlanPolicy.REQUIRED)

    def fail_replace(source, destination):
        raise OSError("disk unavailable")

    monkeypatch.setattr("runtime.plan.store.os.replace", fail_replace)
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Keep in-memory state valid"}]
    )

    assert context.plan_state.version == 1
    assert any(event["type"] == "plan_snapshot_error" for event in trace_events(context))
