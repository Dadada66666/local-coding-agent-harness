from __future__ import annotations

import json
from pathlib import Path

from agent.context import RunConfig
from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from runtime.bootstrap import build_runtime


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


def make_runner(tmp_path: Path, model: FakeModelClient) -> AgentLoop:
    return AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )


def test_unknown_tool_result_is_traced(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(ToolCall("bad_tool", "missing_tool", {})),
            final_response(),
        ]
    )
    runner = make_runner(tmp_path, model)
    context = runner.create_context("use bad tool", include_initial_message=True)

    runner.run_until_idle(context)

    result = _tool_result_event(context.trace.path, "bad_tool")
    assert result["ok"] is False
    assert result["metadata"]["unknown_tool"] is True


def test_validation_error_result_is_traced(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(ToolCall("bad_args", "read_file", {})),
            final_response(),
        ]
    )
    runner = make_runner(tmp_path, model)
    context = runner.create_context("use bad args", include_initial_message=True)

    runner.run_until_idle(context)

    result = _tool_result_event(context.trace.path, "bad_args")
    assert result["ok"] is False
    assert result["metadata"]["validation_error"] is True


def _tool_result_event(path: Path, tool_call_id: str) -> dict:
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("type") == "tool_result" and event.get("tool_call_id") == tool_call_id:
            return event
    raise AssertionError(f"tool_result not found for {tool_call_id}")
