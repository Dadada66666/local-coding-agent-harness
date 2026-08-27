from __future__ import annotations

from contextlib import contextmanager
import json
import sys
from types import ModuleType

from scripts.export_langfuse import export_tasks, project_trace, read_trace


def _event(event_type: str, *, step: int, task_id: str | None = None, **values):
    event = {
        "type": event_type,
        "run_id": "run-1",
        "step": step,
        "ts": 100.0 + step,
        "ts_iso": f"2026-08-27T00:00:{step:02d}+00:00",
        "elapsed_ms": step * 1000.0,
        **values,
    }
    if task_id is not None:
        event["task_id"] = task_id
    return event


def test_projects_model_start_and_end_as_one_generation() -> None:
    events = [
        _event(
            "task_transition",
            step=1,
            task_id="task-1",
            before="idle",
            after="running",
        ),
        _event(
            "model_call_start",
            step=2,
            turn_id=1,
            message_count=3,
            tool_schema_count=5,
            context_tokens=1200,
        ),
        _event(
            "model_call_end",
            step=3,
            turn_id=1,
            duration_ms=250.5,
            tool_call_count=1,
            tool_names=["read_file"],
            input_tokens=900,
            output_tokens=100,
            cache_read_input_tokens=700,
            stop_reason="tool_use",
        ),
    ]

    task = project_trace(events)[0]
    generation = next(item for item in task.observations if item.kind == "generation")

    assert generation.name == "model-call"
    assert generation.metadata["turn_id"] == 1
    assert generation.metadata["model_call_sequence"] == 1
    assert generation.metadata["status"] == "completed"
    assert generation.metadata["duration_ms"] == 250.5
    assert generation.metadata["tool_names"] == ["read_file"]
    assert generation.usage_details == {
        "input_tokens": 900,
        "output_tokens": 100,
        "cache_read_input_tokens": 700,
    }


def test_projects_tool_outcome_and_permission_without_payloads() -> None:
    events = [
        _event(
            "task_transition",
            step=1,
            task_id="task-1",
            before="idle",
            after="running",
        ),
        _event(
            "tool_use",
            step=2,
            turn_id=1,
            tool_call_id="call-1",
            tool="mcp_tool_call",
            args={"tool": "mcp__browser__navigate", "arguments": {"secret": "DO_NOT_EXPORT"}},
            normalized_args={
                "tool": "mcp__browser__navigate",
                "arguments": {"secret": "DO_NOT_EXPORT"},
            },
            read_only=False,
            dangerous=False,
        ),
        _event(
            "permission_decision",
            step=3,
            turn_id=1,
            tool_call_id="call-1",
            tool="mcp_tool_call",
            phase="resolved",
            behavior="allow",
            risk="remote_call",
            decision_reason="approved_scope",
            operation={
                "kind": "mcp.call",
                "scope_key": "mcp:browser:navigate",
                "subject": "private-target",
            },
        ),
        _event(
            "tool_result",
            step=4,
            turn_id=1,
            tool_call_id="call-1",
            tool="mcp_tool_call",
            ok=True,
            output_preview="DO_NOT_EXPORT",
            metadata={
                "mcp_server_id": "browser",
                "mcp_remote_tool": "navigate",
                "mcp_duration_ms": 42.0,
            },
        ),
    ]

    task = project_trace(events)[0]
    tool = next(item for item in task.observations if item.kind == "tool")
    serialized = json.dumps(task, default=lambda value: value.__dict__)

    assert tool.name == "mcp_tool_call"
    assert tool.metadata["status"] == "succeeded"
    assert tool.metadata["mcp_canonical_tool"] == "mcp__browser__navigate"
    assert tool.metadata["mcp_server_id"] == "browser"
    assert tool.metadata["permission_behavior"] == "allow"
    assert tool.metadata["operation_kind"] == "mcp.call"
    assert tool.metadata["operation_scope"] == "mcp:browser:navigate"
    assert "DO_NOT_EXPORT" not in serialized
    assert "private-target" not in serialized


def test_projects_authoritative_verification_as_separate_observation() -> None:
    events = [
        _event(
            "task_transition",
            step=1,
            task_id="task-1",
            before="idle",
            after="running",
        ),
        _event(
            "test_result",
            step=2,
            turn_id=2,
            tool_call_id="call-test",
            command="pytest -q",
            ok=False,
            purpose="verify",
            mutation_version=1,
            verification_level="full",
        ),
    ]

    verification = next(
        item for item in project_trace(events)[0].observations if item.name == "verification"
    )

    assert verification.kind == "span"
    assert verification.level == "ERROR"
    assert verification.metadata["status"] == "failed"
    assert verification.metadata["command"] == "pytest -q"
    assert verification.metadata["verification_level"] == "full"


def test_read_trace_skips_malformed_lines_and_unknown_events(tmp_path) -> None:
    trace_path = tmp_path / "trace.jsonl"
    trace_path.write_text(
        "\n".join(
            [
                json.dumps(
                    _event(
                        "task_transition",
                        step=1,
                        task_id="task-1",
                        before="idle",
                        after="running",
                    )
                ),
                "not-json",
                json.dumps(["not", "an", "event"]),
                json.dumps(_event("future_unknown_event", step=2)),
            ]
        ),
        encoding="utf-8",
    )

    events, skipped = read_trace(trace_path)
    tasks = project_trace(events)

    assert skipped == 2
    assert len(events) == 2
    assert len(tasks) == 1
    assert [item.name for item in tasks[0].observations] == ["task-transition"]


def test_groups_multiple_tasks_from_one_run_without_cross_task_pairing() -> None:
    events = [
        _event(
            "task_transition",
            step=1,
            task_id="task-1",
            before="idle",
            after="running",
        ),
        _event("model_call_start", step=2, turn_id=1),
        _event("model_call_end", step=3, turn_id=1, input_tokens=10, output_tokens=2),
        _event(
            "task_transition",
            step=4,
            task_id="task-1",
            before="running",
            after="completed",
        ),
        _event(
            "task_transition",
            step=5,
            task_id="task-2",
            before="completed",
            after="running",
        ),
        _event("model_call_start", step=6, turn_id=1),
        _event("model_call_error", step=7, turn_id=1, exception_type="ProviderError"),
        _event(
            "task_transition",
            step=8,
            task_id="task-2",
            before="running",
            after="failed",
        ),
    ]

    tasks = project_trace(events)

    assert [task.task_id for task in tasks] == ["task-1", "task-2"]
    assert [task.metadata["task_status"] for task in tasks] == ["completed", "failed"]
    assert sum(item.kind == "generation" for item in tasks[0].observations) == 1
    assert sum(item.kind == "generation" for item in tasks[1].observations) == 1
    second_generation = next(item for item in tasks[1].observations if item.kind == "generation")
    assert second_generation.metadata["status"] == "failed"
    assert second_generation.level == "ERROR"


def test_export_uses_deterministic_task_trace_and_session_metadata(monkeypatch) -> None:
    events = [
        _event(
            "task_transition",
            step=1,
            task_id="task-1",
            before="idle",
            after="running",
        ),
        _event("model_call_start", step=2, turn_id=1),
        _event(
            "model_call_end",
            step=3,
            turn_id=1,
            input_tokens=10,
            output_tokens=2,
        ),
    ]
    task = project_trace(events)[0]
    calls = []
    propagated = []

    class FakeObservation:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeClient:
        def __init__(self, **config):
            self.config = config
            self.flushed = False
            self.closed = False

        def create_trace_id(self, *, seed):
            calls.append(("seed", seed))
            return f"trace:{seed}"

        def start_as_current_observation(self, **kwargs):
            calls.append(("observation", kwargs))
            return FakeObservation()

        def flush(self):
            self.flushed = True

        def shutdown(self):
            self.closed = True

    fake_client = FakeClient()

    @contextmanager
    def fake_propagate_attributes(**kwargs):
        propagated.append(kwargs)
        yield

    fake_langfuse = ModuleType("langfuse")
    fake_langfuse.Langfuse = lambda **config: fake_client
    fake_langfuse.propagate_attributes = fake_propagate_attributes
    monkeypatch.setitem(sys.modules, "langfuse", fake_langfuse)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "public")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "secret")
    monkeypatch.setenv("LANGFUSE_BASE_URL", "https://langfuse.example")

    trace_ids = export_tasks([task])

    observations = [value for kind, value in calls if kind == "observation"]
    assert trace_ids == ["trace:run-1:task-1"]
    assert ("seed", "run-1:task-1") in calls
    assert observations[0]["as_type"] == "agent"
    assert observations[0]["trace_context"] == {"trace_id": "trace:run-1:task-1"}
    assert observations[1]["as_type"] == "span"
    assert observations[2]["as_type"] == "generation"
    assert observations[2]["usage_details"] == {"input_tokens": 10, "output_tokens": 2}
    assert all("input" not in observation for observation in observations)
    assert all("output" not in observation for observation in observations)
    assert propagated == [
        {
            "trace_name": "coding-agent-task",
            "session_id": "run-1",
            "metadata": {"run_id": "run-1", "task_id": "task-1"},
            "tags": ["offline-trace-export"],
        }
    ]
    assert fake_client.flushed is True
    assert fake_client.closed is True
