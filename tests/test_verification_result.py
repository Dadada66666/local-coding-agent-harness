from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent.loop import AgentLoop
from runtime.default_hooks import test_result_hook as record_test_result
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
    )


def run_test_result_hook(arguments: dict, metadata: dict, ok: bool = False):
    context = make_context()
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
        arguments={"command": 'python -c "raise SystemExit(1)"', "purpose": "verify"},
        metadata={"purpose": "verify"},
    )

    assert context.last_test_result is not None
    assert context.last_test_result["ok"] is False
    assert context.last_test_result["command"] == 'python -c "raise SystemExit(1)"'
    assert result.metadata["verification_command"] is True
    assert "test_command" not in result.metadata


def test_verify_purpose_can_come_from_metadata() -> None:
    context, result = run_test_result_hook(
        arguments={"command": 'python -c "raise SystemExit(0)"'},
        metadata={"purpose": " verify "},
        ok=True,
    )

    assert context.last_test_result is not None
    assert context.last_test_result["ok"] is True
    assert result.metadata["verification_command"] is True


def test_read_only_discovery_command_is_not_recorded_as_verification() -> None:
    context, result = run_test_result_hook(
        arguments={"command": "find . -maxdepth 3 -type f -print", "purpose": "verify"},
        metadata={"purpose": "verify"},
        ok=True,
    )

    assert context.last_test_result is None
    assert result.metadata["verification_ignored"] is True
    assert any(event["type"] == "verification_ignored" for event in context.trace.events)


def test_test_command_is_still_recorded_without_verify_purpose() -> None:
    context, result = run_test_result_hook(
        arguments={"command": "PYTEST examples/demo_repo/tests"},
        metadata={},
        ok=True,
    )

    assert context.last_test_result is not None
    assert context.last_test_result["ok"] is True
    assert result.metadata["verification_command"] is True
    assert result.metadata["test_command"] is True


def test_denied_bash_result_is_not_recorded_as_verification() -> None:
    context, result = run_test_result_hook(
        arguments={"command": 'python -c "raise SystemExit(1)"', "purpose": "verify"},
        metadata={"purpose": "verify", "denied": True},
    )

    assert context.last_test_result is None
    assert "verification_command" not in result.metadata


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
