from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent.context import RunConfig
from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from agent.model_client import ModelClient
from runtime.bootstrap import build_runtime


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def call(self, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        response = self.responses[self.calls]
        self.calls += 1
        return response


def final_response(text: str = "done", stop_reason: str | None = "end_turn") -> ModelResponse:
    return ModelResponse(
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
        text=text,
        usage=TokenUsage(),
        stop_reason=stop_reason,
    )


def tool_response(*tool_calls: ToolCall, stop_reason: str | None = "tool_use") -> ModelResponse:
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
        stop_reason=stop_reason,
    )


def make_runner(tmp_path: Path, model: FakeModelClient, *, max_turns: int = 30) -> AgentLoop:
    return AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", max_turns=max_turns),
    )


def test_max_tokens_response_is_incomplete_not_success(tmp_path: Path) -> None:
    model = FakeModelClient([final_response("partial answer", stop_reason="max_tokens")])
    runner = make_runner(tmp_path, model)
    context = runner.create_context("answer", include_initial_message=True)

    runner.run_until_idle(context)

    assert context.finished is True
    assert context.success is False
    assert context.abort_reason == "model_incomplete"
    assert "max_tokens" in context.final_text
    assert model.calls == 1
    assert _has_trace_type(context.trace.path, "model_response_incomplete")


def test_model_client_preserves_provider_stop_reason() -> None:
    provider_response = SimpleNamespace(
        content=[{"type": "text", "text": "done"}],
        usage=SimpleNamespace(input_tokens=10, output_tokens=2),
        stop_reason="end_turn",
    )
    client = ModelClient.__new__(ModelClient)
    client.model = "test-model"
    client.max_tokens = 100
    client.client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kwargs: provider_response)
    )

    response = client.call(system="system", messages=[], tools=[])

    assert response.stop_reason == "end_turn"


def test_stop_reason_and_content_mismatch_is_protocol_error(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(
                ToolCall("call_1", "write_file", {"path": "demo.py", "content": "x = 1\n"}),
                stop_reason="end_turn",
            )
        ]
    )
    runner = make_runner(tmp_path, model)
    context = runner.create_context("write", include_initial_message=True)

    runner.run_until_idle(context)

    assert context.abort_reason == "model_protocol_error"
    assert not (tmp_path / "demo.py").exists()
    assert context.messages[-1]["content"][0]["tool_use_id"] == "call_1"
    assert context.messages[-1]["content"][0]["is_error"] is True


def test_parallel_tool_results_are_returned_in_one_user_message(tmp_path: Path) -> None:
    (tmp_path / "one.py").write_text("one\n", encoding="utf-8")
    (tmp_path / "two.py").write_text("two\n", encoding="utf-8")
    model = FakeModelClient(
        [
            tool_response(
                ToolCall("call_1", "read_file", {"path": "one.py"}),
                ToolCall("call_2", "read_file", {"path": "two.py"}),
            ),
            final_response(),
        ]
    )
    runner = make_runner(tmp_path, model)
    context = runner.create_context("read files", include_initial_message=True)

    runner.run_until_idle(context)

    result_messages = [
        message
        for message in context.messages
        if message.get("role") == "user"
        and isinstance(message.get("content"), list)
        and message["content"]
        and all(block.get("type") == "tool_result" for block in message["content"])
    ]
    assert len(result_messages) == 1
    assert [block["tool_use_id"] for block in result_messages[0]["content"]] == [
        "call_1",
        "call_2",
    ]
    assert context.task_tool_rounds == 1
    assert context.task_model_calls == 2


def test_model_call_limit_counts_final_and_tool_turns(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(ToolCall("call_1", "list_dir", {})),
            final_response(),
        ]
    )
    runner = make_runner(tmp_path, model, max_turns=1)
    context = runner.create_context("inspect", include_initial_message=True)

    runner.run_until_idle(context)

    assert context.success is False
    assert context.abort_reason == "max_model_calls_exceeded"
    assert context.task_model_calls == 1
    assert context.turn_count == 1
    assert model.calls == 1


def _has_trace_type(path: Path, event_type: str) -> bool:
    return any(
        json.loads(line).get("type") == event_type
        for line in path.read_text(encoding="utf-8").splitlines()
    )
