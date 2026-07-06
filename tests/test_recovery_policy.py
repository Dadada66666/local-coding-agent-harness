from __future__ import annotations

from types import SimpleNamespace

from agent.context import RunConfig
from runtime.recovery import RecoveryPolicy


def make_context(last_test_result: dict, repair_attempts: int = 0):
    return SimpleNamespace(
        last_test_result=last_test_result,
        repair_attempts=repair_attempts,
        config=RunConfig(max_repair_attempts=3),
    )


def test_recovery_retry_message_does_not_duplicate_large_tool_output() -> None:
    large_output = "failure line\n" * 1000
    context = make_context(
        {
            "command": "python -m pytest",
            "ok": False,
            "error": "command exited 1",
            "output_preview": large_output,
        }
    )

    message = RecoveryPolicy().build_retry_message(context)

    assert "python -m pytest" in message["content"]
    assert "command exited 1" in message["content"]
    assert large_output[:100] not in message["content"]
    assert len(message["content"]) < 300


def test_recovery_policy_injects_once_per_failed_result() -> None:
    context = make_context({"ok": False})
    injected_context = make_context({"ok": False, "repair_injected": True})
    passed_context = make_context({"ok": True})
    exhausted_context = make_context({"ok": False}, repair_attempts=3)

    policy = RecoveryPolicy()

    assert policy.should_inject_retry(context) is True
    assert policy.should_inject_retry(injected_context) is False
    assert policy.should_inject_retry(passed_context) is False
    assert policy.should_inject_retry(exhausted_context) is False
