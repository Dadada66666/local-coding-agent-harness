from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent.context import RunConfig
from agent.loop import AgentLoop
from runtime.default_hooks import _cancel_task_for_terminal_deny, stop_report_hook
from runtime.hooks import HookEvent
from runtime.operation import Operation
from runtime.permission import PermissionBehavior, PermissionDecision


class RaisingModelClient:
    def __init__(self, exc: BaseException) -> None:
        self.exc = exc

    def call(self, system: str, messages: list[dict], tools: list[dict]):
        raise self.exc


class FakeTrace:
    def __init__(self) -> None:
        self.events = []

    def log(self, event: dict) -> None:
        self.events.append(event)


class FakeHooks:
    def __init__(self, fail_on_stop: bool = False) -> None:
        self.events = []
        self.fail_on_stop = fail_on_stop

    def trigger(self, event: str, *args, **kwargs):
        self.events.append(event)
        if self.fail_on_stop and event == HookEvent.STOP:
            raise RuntimeError("stop failed")
        return None


class FakeContextManager:
    def prepare_context(self, context) -> None:
        return None


class FakeToolRegistry:
    def schemas(self) -> list[dict]:
        return []


def make_runner(context, exc: BaseException, fail_on_stop: bool = False) -> AgentLoop:
    hooks = FakeHooks(fail_on_stop=fail_on_stop)
    runtime = SimpleNamespace(
        hooks=hooks,
        context_manager=FakeContextManager(),
        tool_registry=FakeToolRegistry(),
    )
    runner = AgentLoop(
        model_client=RaisingModelClient(exc),
        runtime=runtime,
        repo_path=Path("."),
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )
    runner.create_context = lambda task, include_initial_message=True: context
    return runner


def make_context():
    return SimpleNamespace(
        task="inspect the project",
        messages=[],
        system_prompt="",
        config=RunConfig(permission_mode="accept_edits"),
        trace=FakeTrace(),
        finished=False,
        success=False,
        final_text="",
        abort_reason=None,
        stop_recorded=False,
        turn_count=0,
        current_turn_id=0,
    )


def test_model_call_error_triggers_stop_once() -> None:
    context = make_context()
    runner = make_runner(context, TimeoutError("request timed out"))

    result = runner.run("inspect the project")

    assert result is context
    assert context.success is False
    assert context.abort_reason == "model_call_failed"
    assert "TimeoutError" in context.final_text
    assert _has_trace_type(context, "model_call_error")
    assert runner.runtime.hooks.events.count(HookEvent.STOP) == 1


def test_keyboard_interrupt_triggers_stop_once() -> None:
    context = make_context()
    runner = make_runner(context, KeyboardInterrupt())

    result = runner.run("inspect the project")

    assert result is context
    assert context.success is False
    assert context.abort_reason == "interrupted"
    assert "Ctrl+C" in context.final_text
    assert _has_trace_type(context, "model_call_interrupted")
    assert _has_trace_type(context, "run_aborted")
    assert runner.runtime.hooks.events.count(HookEvent.STOP) == 1


def test_finish_records_stop_hook_error_without_raising() -> None:
    context = make_context()
    runner = make_runner(context, TimeoutError("request timed out"), fail_on_stop=True)

    runner.finish(context)

    assert context.stop_recorded is True
    assert _has_trace_type(context, "stop_hook_error")


def test_stop_report_hook_continues_after_artifact_failure(monkeypatch) -> None:
    class BrokenReadableTraceWriter:
        def write(self, context):
            raise FileNotFoundError("missing run dir")

    calls = []
    context = SimpleNamespace(
        run_dir=Path("."),
        success=False,
        repair_attempts=0,
        trace=FakeTrace(),
        report_writer=SimpleNamespace(write=lambda context: calls.append("report") or Path("report.md")),
        diff_manager=SimpleNamespace(write_patch=lambda context: calls.append("diff") or Path("diff.patch")),
        cost_tracker=SimpleNamespace(write=lambda context: calls.append("cost") or Path("cost.json")),
    )
    monkeypatch.setattr("runtime.default_hooks.ReadableTraceWriter", BrokenReadableTraceWriter)

    stop_report_hook(context)

    assert calls == ["report", "diff", "cost"]
    assert _has_trace_type(context, "stop_artifact_error")
    assert _has_trace_type(context, "stop")


def test_terminal_tool_error_does_not_cache_denied_scope() -> None:
    context = SimpleNamespace(
        denied_permission_scopes=set(),
        trace=FakeTrace(),
        current_turn_id=1,
        finished=False,
        success=True,
        final_text="",
    )
    operation = Operation(
        kind="fs.write",
        action="edit",
        subject="demo.py",
        paths=["demo.py"],
        scope_key="write:edit:demo.py",
        terminal_on_deny=True,
    )
    decision = PermissionDecision(
        behavior=PermissionBehavior.DENY,
        risk="invalid_edit",
        message="old_text not found",
        proposed_scope="write:edit:demo.py",
        operation=operation,
        terminal_on_deny=True,
        decision_reason="tool_permission",
    )
    tool_call = SimpleNamespace(id="call_1", name="edit_file")

    _cancel_task_for_terminal_deny(tool_call, decision, context)

    assert context.finished is True
    assert context.success is False
    assert context.denied_permission_scopes == set()


def _has_trace_type(context, event_type: str) -> bool:
    return any(event.get("type") == event_type for event in context.trace.events)
