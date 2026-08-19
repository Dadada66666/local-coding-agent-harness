from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.config import RunConfig
from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from agent.model_client import ModelClient, ModelContextOverflowError
from runtime.bootstrap import build_runtime


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse | Exception]) -> None:
        self.responses = responses
        self.calls = 0
        self.max_tokens = 4096
        self.context_window_tokens = None

    def call(self, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
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


def make_runner(tmp_path: Path, model: FakeModelClient, *, max_turns: int = 40) -> AgentLoop:
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


def test_model_client_preserves_cache_usage_fields() -> None:
    provider_response = SimpleNamespace(
        content=[{"type": "text", "text": "done"}],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=2,
            cache_creation_input_tokens=20,
            cache_read_input_tokens=30,
            cache_deleted_input_tokens=4,
        ),
        stop_reason="end_turn",
    )
    client = ModelClient.__new__(ModelClient)
    client.model = "test-model"
    client.max_tokens = 100
    client.client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **kwargs: provider_response)
    )

    response = client.call(system="system", messages=[], tools=[])

    assert response.usage.logical_input_tokens == 60
    assert response.usage.context_tokens == 62
    assert response.usage.cache_deleted_input_tokens == 4


def test_model_client_classifies_context_overflow_errors() -> None:
    client = ModelClient.__new__(ModelClient)
    client.model = "test-model"
    client.max_tokens = 100
    client.client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("prompt is too long for this context window")
            )
        )
    )

    with pytest.raises(ModelContextOverflowError, match="prompt is too long"):
        client.call(system="system", messages=[], tools=[])


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


def test_saturated_invalid_tool_loop_stops_after_one_bounded_retry(tmp_path: Path) -> None:
    responses = [
        tool_response(ToolCall(f"call_{index}", "write_file", {}))
        for index in range(3)
    ]
    for response in responses:
        response.usage = TokenUsage(output_tokens=4097)
    model = FakeModelClient(responses)
    runner = make_runner(tmp_path, model)
    context = runner.create_context("rewrite a large file", include_initial_message=True)

    runner.run_until_idle(context)

    assert model.calls == 2
    assert context.task_model_calls == 2
    assert context.abort_reason == "repeated_tool_failure"
    assert "write_file" in context.final_text
    runtime_messages = [
        message["content"]
        for message in context.messages
        if message.get("role") == "user" and isinstance(message.get("content"), str)
    ]
    assert sum("reached the output budget" in message for message in runtime_messages) == 1
    assert _has_trace_type(context.trace.path, "tool_progress_retry")
    assert _has_trace_type(context.trace.path, "tool_progress_stalled")


def test_repeated_invalid_tool_loop_stops_before_model_call_limit(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(ToolCall(f"call_{index}", "write_file", {}))
            for index in range(4)
        ]
    )
    runner = make_runner(tmp_path, model)
    context = runner.create_context("write a file", include_initial_message=True)

    runner.run_until_idle(context)

    assert model.calls == 3
    assert context.task_model_calls == 3
    assert context.abort_reason == "repeated_tool_failure"
    assert not _has_trace_type(context.trace.path, "max_turns_exceeded")


def test_context_overflow_compacts_and_retries_once(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            ModelContextOverflowError("prompt too long"),
            final_response("recovered"),
        ]
    )
    runner = make_runner(tmp_path, model)
    context = runner.create_context("inspect", include_initial_message=True)
    context.config.compact_threshold_chars = 1_000_000
    context.config.context_recent_target_tokens = 1
    context.config.context_recent_max_tokens = 1_000
    context.config.context_min_recent_rounds = 2
    for index in range(6):
        context.messages.extend(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"call_{index}",
                            "name": "read_file",
                            "input": {"path": "demo.py"},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"call_{index}",
                            "content": "result " * 100,
                        }
                    ],
                },
            ]
        )

    runner.run_until_idle(context)

    assert context.success is True
    assert context.final_text == "recovered"
    assert context.context_recovery_attempts == 1
    assert context.context_compactions == 1
    assert model.calls == 2
    assert _has_trace_type(context.trace.path, "context_recovery")


def test_repeated_context_overflow_stops_without_a_retry_loop(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            ModelContextOverflowError("prompt too long"),
            ModelContextOverflowError("prompt too long again"),
        ]
    )
    runner = make_runner(tmp_path, model)
    context = runner.create_context("inspect", include_initial_message=True)
    context.config.compact_threshold_chars = 1_000_000
    context.config.context_recent_target_tokens = 1
    context.config.context_recent_max_tokens = 1_000
    context.config.context_min_recent_rounds = 1
    for index in range(4):
        context.messages.extend(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"call_{index}",
                            "name": "list_dir",
                            "input": {},
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"call_{index}",
                            "content": "entry\n" * 100,
                        }
                    ],
                },
            ]
        )

    runner.run_until_idle(context)

    assert context.success is False
    assert context.abort_reason == "model_context_overflow"
    assert context.context_recovery_attempts == 1
    assert model.calls == 2
    assert _has_trace_type(context.trace.path, "context_recovery_skipped")


def _has_trace_type(path: Path, event_type: str) -> bool:
    return any(
        json.loads(line).get("type") == event_type
        for line in path.read_text(encoding="utf-8").splitlines()
    )
