from __future__ import annotations

import json
from pathlib import Path

from runtime.config import RunConfig
from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage
from runtime.bootstrap import build_runtime


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def call(self, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def final_response(text: str = "done") -> ModelResponse:
    return ModelResponse(
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
        text=text,
        usage=TokenUsage(),
    )


def make_runner(tmp_path: Path, model: FakeModelClient) -> AgentLoop:
    return AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )


def test_submit_resets_stale_verification_result(tmp_path: Path) -> None:
    model = FakeModelClient([final_response("fresh answer")])
    runner = make_runner(tmp_path, model)
    context = runner.start_interactive()
    context.last_test_result = {"ok": False, "error": "old failure"}

    runner.submit(context, "answer a new question")

    assert context.success is True
    assert context.final_text == "fresh answer"
    assert context.task_test_result is None
    assert context.last_test_result == {"ok": False, "error": "old failure"}


def test_report_failure_summary_follows_latest_success(tmp_path: Path) -> None:
    model = FakeModelClient([final_response("fresh answer")])
    runner = make_runner(tmp_path, model)
    context = runner.start_interactive()
    context.last_test_result = {"ok": False, "error": "old failure"}

    runner.submit(context, "answer a new question")
    report = context.report_writer.write(context).read_text(encoding="utf-8")

    assert "Success: true" in report
    assert "## Failure Summary\nN/A" in report
    assert "Result: not recorded" in report
    assert "Verification: not recorded" in report


def test_report_separates_current_task_from_session_state(tmp_path: Path) -> None:
    model = FakeModelClient([final_response("fresh answer")])
    runner = make_runner(tmp_path, model)
    context = runner.start_interactive()
    context.changed_files.add("old_task.py")

    runner.submit(context, "current task")
    context.changed_files.add("current_task.py")
    context.task_changed_files.add("current_task.py")
    report = context.report_writer.write(context).read_text(encoding="utf-8")

    assert "## Task\ncurrent task" in report
    assert "## Changed Files\n- current_task.py" in report
    assert "## Session Changed Files\n- current_task.py\n- old_task.py" in report


def test_submit_success_ignores_changed_files_from_previous_prompt(tmp_path: Path) -> None:
    model = FakeModelClient([final_response("fresh answer")])
    runner = make_runner(tmp_path, model)
    context = runner.start_interactive()
    context.changed_files.add("old_task.py")

    runner.submit(context, "answer a new question")

    assert context.success is True
    assert context.changed_files == {"old_task.py"}


def test_each_interactive_task_gets_an_independent_model_call_budget(tmp_path: Path) -> None:
    model = FakeModelClient([final_response("first"), final_response("second")])
    runner = AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", max_turns=1),
    )
    context = runner.start_interactive()

    runner.submit(context, "first task")
    assert context.success is True
    assert context.task_model_calls == 1

    runner.submit(context, "second task")

    assert context.success is True
    assert context.final_text == "second"
    assert context.task_model_calls == 1
    assert context.turn_count == 2
    assert model.calls == 2


def test_submit_records_task_boundary_and_task_cost(tmp_path: Path) -> None:
    model = FakeModelClient([final_response("done")])
    runner = make_runner(tmp_path, model)
    context = runner.start_interactive()

    runner.submit(context, "current task")
    report = context.report_writer.write(context).read_text(encoding="utf-8")
    events = [
        json.loads(line)
        for line in context.trace.path.read_text(encoding="utf-8").splitlines()
    ]
    user_events = [event for event in events if event["type"] == "user_prompt"]

    assert context.task_id == "task-1"
    assert user_events[-1]["task_id"] == "task-1"
    assert user_events[-1]["task"] == "current task"
    assert "## Task Cost\ncalls=1" in report
    assert "## Session Cost\ncalls=1" in report


def test_report_keeps_recovered_tool_failures(tmp_path: Path) -> None:
    model = FakeModelClient([final_response("## Summary\ndone")])
    runner = make_runner(tmp_path, model)
    context = runner.start_interactive()
    context.begin_task("recover")
    context.task_tool_failures.append(
        {"turn_id": 3, "tool": "bash", "error": "command exited 22"}
    )
    context.success = True
    context.final_text = "## Summary\ndone"

    report = context.report_writer.write(context).read_text(encoding="utf-8")

    assert "## Recovered Failures\n- turn 3 bash: command exited 22" in report
    assert "## Summary\ndone" in report
    assert "## Summary\n## Summary" not in report
