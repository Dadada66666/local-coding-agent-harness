from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent.context import RunConfig
from agent.loop import AgentLoop
from runtime.hooks import HookEvent


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
    def __init__(self) -> None:
        self.events = []

    def trigger(self, event: str, *args, **kwargs):
        self.events.append(event)
        return None


class FakeContextManager:
    def prepare_context(self, context) -> None:
        return None


class FakeToolRegistry:
    def schemas(self) -> list[dict]:
        return []


def make_runner(context, exc: BaseException) -> AgentLoop:
    hooks = FakeHooks()
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


def _has_trace_type(context, event_type: str) -> bool:
    return any(event.get("type") == event_type for event in context.trace.events)
