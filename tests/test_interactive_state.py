from __future__ import annotations

from pathlib import Path

from agent.context import RunConfig
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


def test_submit_success_ignores_changed_files_from_previous_prompt(tmp_path: Path) -> None:
    model = FakeModelClient([final_response("fresh answer")])
    runner = make_runner(tmp_path, model)
    context = runner.start_interactive()
    context.changed_files.add("old_task.py")

    runner.submit(context, "answer a new question")

    assert context.success is True
    assert context.changed_files == {"old_task.py"}
