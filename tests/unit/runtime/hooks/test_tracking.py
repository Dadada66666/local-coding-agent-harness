from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.loop import AgentLoop
from runtime.config import RunConfig
from runtime.hooks.tracking import test_result_hook as record_test_result
from runtime.recovery import RecoveryPolicy
from tools.base import ToolResult


class DummyTrace:
    def __init__(self) -> None:
        self.events = []

    def log(self, event: dict) -> None:
        self.events.append(event)


def make_context():
    return SimpleNamespace(
        last_test_result=None,
        trace=DummyTrace(),
        current_turn_id=1,
        turn_count=0,
        mutation_version=0,
        repair_attempts=0,
        config=RunConfig(),
    )


def run_test_result_hook(
    arguments: dict,
    metadata: dict,
    ok: bool = False,
    context=None,
):
    context = context or make_context()
    tool = SimpleNamespace(name="bash")
    tool_call = SimpleNamespace(id="call_1", arguments=arguments)
    result = ToolResult(
        ok=ok,
        content="output",
        error=None if ok else "command exited 1",
        metadata=metadata,
    )

    record_test_result(tool_call, tool, result, context)

    return context, result


def test_verify_purpose_records_non_test_bash_failure() -> None:
    context, result = run_test_result_hook(
        arguments={"command": 'python -c "raise SystemExit(1)"', "purpose": "verify", "result_scope": "command"},
        metadata={"purpose": "verify", "result_scope": "command"},
    )

    assert context.last_test_result is not None
    assert context.last_test_result["ok"] is False
    assert context.last_test_result["command"] == 'python -c "raise SystemExit(1)"'
    assert result.metadata["verification_command"] is True
    assert "test_command" not in result.metadata
    assert RecoveryPolicy().should_inject_retry(context) is True


def test_verify_purpose_can_come_from_metadata() -> None:
    context, result = run_test_result_hook(
        arguments={"command": 'python -c "raise SystemExit(0)"', "result_scope": "command"},
        metadata={"purpose": " verify ", "result_scope": "command"},
        ok=True,
    )

    assert context.last_test_result is not None
    assert context.last_test_result["ok"] is True
    assert result.metadata["verification_command"] is True


@pytest.mark.parametrize("purpose", ["run", "probe"])
def test_environment_probe_does_not_overwrite_verification_or_trigger_recovery(
    purpose: str,
) -> None:
    context, _ = run_test_result_hook(
        arguments={
            "command": "node --check app.js",
            "purpose": "verify",
            "result_scope": "command",
        },
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=True,
    )
    recorded = context.task_test_result

    context, result = run_test_result_hook(
        arguments={
            "command": "curl -fsSI http://127.0.0.1:4173/",
            "purpose": purpose,
            "result_scope": "command",
        },
        metadata={"purpose": purpose, "result_scope": "command"},
        ok=False,
        context=context,
    )

    assert context.last_test_result is recorded
    assert context.task_test_result is recorded
    assert "verification_command" not in result.metadata
    assert RecoveryPolicy().should_inject_retry(context) is False


def test_launcher_scope_does_not_overwrite_existing_verification() -> None:
    command = "opaque verification command"
    context, _ = run_test_result_hook(
        arguments={
            "command": command,
            "purpose": "verify",
            "result_scope": "command",
        },
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=False,
    )
    recorded = context.task_test_result
    verification_version = context.task_verification_version

    context, result = run_test_result_hook(
        arguments={
            "command": command,
            "purpose": "verify",
            "result_scope": "launcher",
        },
        metadata={"purpose": "verify", "result_scope": "launcher"},
        ok=True,
        context=context,
    )

    assert context.last_test_result is recorded
    assert context.task_test_result is recorded
    assert context.task_verification_version == verification_version
    assert result.metadata["verification_ignored"] is True
    assert result.metadata["verification_ignored_reason"] == "launcher_result"
    assert context.trace.events[-1]["type"] == "verification_ignored"
    assert context.trace.events[-1]["reason"] == "launcher_result"


def test_missing_result_scope_fails_closed() -> None:
    context, result = run_test_result_hook(
        arguments={
            "command": "opaque verification command",
            "purpose": "verify",
            "result_scope": "command",
        },
        metadata={"purpose": "verify"},
        ok=True,
    )

    assert context.last_test_result is None
    assert not hasattr(context, "task_test_result")
    assert result.metadata["verification_ignored"] is True
    assert result.metadata["verification_ignored_reason"] == "missing_execution_result_scope"


def test_read_only_discovery_command_is_not_recorded_as_verification() -> None:
    context, result = run_test_result_hook(
        arguments={"command": "find . -maxdepth 3 -type f -print", "purpose": "verify", "result_scope": "command"},
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=True,
    )

    assert context.last_test_result is None
    assert result.metadata["verification_ignored"] is True
    assert any(event["type"] == "verification_ignored" for event in context.trace.events)


def test_command_discovery_does_not_overwrite_real_verification() -> None:
    context, _ = run_test_result_hook(
        arguments={"command": "node --check app.js", "purpose": "verify", "result_scope": "command"},
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=True,
    )
    recorded = context.task_test_result

    context, result = run_test_result_hook(
        arguments={
            "command": "command -v chromium || command -v firefox || true",
            "purpose": "verify",
            "result_scope": "command",
        },
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=True,
        context=context,
    )

    assert context.task_test_result is recorded
    assert context.task_test_result["command"] == "node --check app.js"
    assert result.metadata["verification_ignored"] is True


def test_which_test_binary_is_discovery_even_without_verify_purpose() -> None:
    context, result = run_test_result_hook(
        arguments={"command": "which pytest", "result_scope": "command"},
        metadata={"result_scope": "command"},
        ok=True,
    )

    assert context.last_test_result is None
    assert result.metadata["verification_ignored"] is True


def test_cd_wrapped_git_diff_check_is_static_verification() -> None:
    context, result = run_test_result_hook(
        arguments={
            "command": "cd project && git diff --check",
            "purpose": "verify",
            "result_scope": "command",
        },
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=False,
    )

    assert context.task_test_result["ok"] is False
    assert context.task_test_result["command"] == "cd project && git diff --check"
    assert context.task_test_result["verification_level"] == "static"
    assert result.metadata["verification_command"] is True


def test_git_diff_no_index_check_is_static_verification() -> None:
    context, result = run_test_result_hook(
        arguments={
            "command": "git diff --no-index --check before after",
            "purpose": "verify",
            "result_scope": "command",
        },
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=False,
    )

    assert context.task_test_result["ok"] is False
    assert context.task_test_result["verification_level"] == "static"
    assert result.metadata["verification_command"] is True


def test_plain_git_diff_remains_discovery() -> None:
    context, result = run_test_result_hook(
        arguments={"command": "git diff -- app.py", "purpose": "verify", "result_scope": "command"},
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=True,
    )

    assert context.last_test_result is None
    assert result.metadata["verification_ignored"] is True


def test_ruff_check_remains_a_real_static_verification() -> None:
    context, result = run_test_result_hook(
        arguments={"command": "ruff check .", "purpose": "verify", "result_scope": "command"},
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=True,
    )

    assert context.last_test_result["command"] == "ruff check ."
    assert context.last_test_result["verification_level"] == "static"
    assert result.metadata["verification_command"] is True


def test_test_command_is_still_recorded_without_verify_purpose() -> None:
    context, result = run_test_result_hook(
        arguments={"command": "PYTEST examples/demo_repo/tests", "result_scope": "command"},
        metadata={"result_scope": "command"},
        ok=True,
    )

    assert context.last_test_result is not None
    assert context.last_test_result["ok"] is True
    assert result.metadata["verification_command"] is True
    assert result.metadata["test_command"] is True


def test_denied_bash_result_is_not_recorded_as_verification() -> None:
    context, result = run_test_result_hook(
        arguments={"command": 'python -c "raise SystemExit(1)"', "purpose": "verify", "result_scope": "command"},
        metadata={"purpose": "verify", "result_scope": "command", "denied": True},
    )

    assert context.last_test_result is None
    assert "verification_command" not in result.metadata


def test_mutating_test_command_is_not_recorded_as_verification() -> None:
    context, result = run_test_result_hook(
        arguments={
            "command": "echo x > demo.py\npython -m pytest",
            "purpose": "verify",
            "result_scope": "command",
        },
        metadata={"purpose": "verify", "result_scope": "command", "mutation_recorded": True},
        ok=True,
    )

    assert context.last_test_result is None
    assert result.metadata["verification_ignored"] is True
    assert result.metadata["verification_ignored_reason"] == "explicit_mutation_command"


def test_infer_success_prefers_recorded_verification_result() -> None:
    loop = AgentLoop(model_client=None, runtime=None, repo_path=Path("."))

    assert (
        loop.infer_success(
            SimpleNamespace(
                changed_files=set(),
                last_test_result={"ok": False},
                final_text="done",
            )
        )
        is False
    )
    assert (
        loop.infer_success(
            SimpleNamespace(
                changed_files={"app.py"},
                last_test_result={"ok": True},
                final_text="done",
            )
        )
        is True
    )


def test_infer_success_requires_verification_after_changes() -> None:
    loop = AgentLoop(model_client=None, runtime=None, repo_path=Path("."))

    assert (
        loop.infer_success(
            SimpleNamespace(
                changed_files={"app.py"},
                last_test_result=None,
                final_text="done",
            )
        )
        is False
    )


def test_infer_success_rejects_unresolved_mutation_failure() -> None:
    loop = AgentLoop(model_client=None, runtime=None, repo_path=Path("."))

    assert (
        loop.infer_success(
            SimpleNamespace(
                task_unresolved_mutation_failure=True,
                task_changed_files=set(),
                task_test_result={"ok": True},
                final_text="done",
            )
        )
        is False
    )


def test_verification_becomes_stale_after_a_later_mutation() -> None:
    context, _ = run_test_result_hook(
        arguments={"command": "python -m pytest", "purpose": "verify", "result_scope": "command"},
        metadata={"purpose": "verify", "result_scope": "command"},
        ok=True,
    )
    context.task_changed_files = {"app.py"}
    loop = AgentLoop(model_client=None, runtime=None, repo_path=Path("."))

    assert context.task_verification_version == 0
    assert loop.infer_success(context) is True

    context.mutation_version += 1

    assert loop.infer_success(context) is False
