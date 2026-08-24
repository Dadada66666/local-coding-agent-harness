from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mcp import types
import pytest

from runtime.config import RunConfig
from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from agent.model_client import ModelClient, ModelContextOverflowError
from runtime.bootstrap import build_runtime
from runtime.mcp import MCPStartupError
from runtime.mcp.runtime import MCPCatalogEntry
from runtime.plan import ExecutionPath, PlanPhase, PlanPolicy, PlanState
from runtime.plan.capabilities import plan_capabilities
from tools.mcp_tool import MCPTool, MCPToolCall, MCPToolSearch, search_terms


class FakeModelClient:
    def __init__(self, responses: list[ModelResponse | Exception]) -> None:
        self.responses = responses
        self.calls = 0
        self.semantic_calls = 0
        self.max_tokens = 4096
        self.context_window_tokens = None
        self.tool_payloads: list[str] = []

    def call(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        *,
        max_tokens: int | None = None,
    ) -> ModelResponse:
        if max_tokens is not None:
            self.semantic_calls += 1
            headings = (
                "## USER_CONSTRAINTS",
                "## CONFIRMED",
                "## REJECTED_OR_OBSOLETE",
                "## UNRESOLVED",
                "## NEXT_ACTIONS",
                "## CRITICAL_REFERENCES",
            )
            text = "\n\n".join(f"{heading}\n\n- None." for heading in headings)
            return ModelResponse(
                message={"role": "assistant", "content": [{"type": "text", "text": text}]},
                text=text,
                usage=TokenUsage(),
                stop_reason="end_turn",
            )
        self.tool_payloads.append(
            json.dumps(tools, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, Exception):
            raise response
        return response


class FakeGatewayMCPRuntime:
    def __init__(self) -> None:
        self.remote_calls: list[tuple[str, str, dict]] = []
        binding = MCPTool(
            runtime=self,
            server_id="demo",
            remote_name="echo",
            name="mcp__demo__echo",
            description="Echo text.",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        )
        self.catalog = (
            MCPCatalogEntry(
                canonical_tool_id=binding.name,
                server_id=binding.server_id,
                remote_name=binding.remote_name,
                description=binding.description,
                input_schema=binding.input_schema,
                binding=binding,
                server_terms=search_terms(binding.server_id),
                name_terms=search_terms(binding.remote_name),
                description_terms=search_terms(binding.description),
                property_terms=search_terms("text"),
            ),
        )
        self._entry = self.catalog[0]

    def start(self, context, registry) -> None:
        registry.register(MCPToolSearch(self))
        registry.register(MCPToolCall(self))

    def close(self, context) -> None:
        return None

    def resolve_catalog_tool(self, canonical_tool_id: str):
        return self._entry if canonical_tool_id == self._entry.canonical_tool_id else None

    def call_tool(self, server_id: str, remote_name: str, arguments: dict):
        self.remote_calls.append((server_id, remote_name, arguments))
        return types.CallToolResult(
            content=[types.TextContent(text=str(arguments.get("text", "")))]
        )


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


def test_model_client_preserves_raw_cache_usage_fields_without_logical_aliases() -> None:
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

    assert response.usage.input_tokens == 10
    assert response.usage.cache_creation_input_tokens == 20
    assert response.usage.cache_read_input_tokens == 30
    assert response.usage.cache_deleted_input_tokens == 4
    assert not hasattr(response.usage, "logical_input_tokens")
    assert not hasattr(response.usage, "context_tokens")


def test_loop_passes_current_verification_fact_to_prompt_builder(
    tmp_path: Path, monkeypatch
) -> None:
    import agent.loop as loop_module

    observed: list[dict | None] = []
    original = loop_module.build_system_prompt

    def capture_prompt(*args, **kwargs):
        observed.append(kwargs.get("task_test_result"))
        return original(*args, **kwargs)

    monkeypatch.setattr(loop_module, "build_system_prompt", capture_prompt)
    model = FakeModelClient([final_response("done")])
    runner = make_runner(tmp_path, model)
    context = runner.create_context("answer", include_initial_message=True)
    verification = {
        "ok": False,
        "verification_level": "static",
        "command": "git diff --check",
    }
    context.task_test_result = verification

    runner.run_until_idle(context)

    assert verification in observed


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


def test_unshapable_tool_result_batch_fails_before_round_visibility(tmp_path: Path) -> None:
    model = FakeModelClient([tool_response(ToolCall("call_1", "missing_tool", {}))])
    runner = make_runner(tmp_path, model)
    context = runner.create_context("run tool", include_initial_message=True)
    context.config.max_tool_round_tokens = 1
    before_messages = list(context.messages)
    before_audit = list(context.conversation_messages)

    runner.run_until_idle(context)

    assert context.finished is True
    assert context.success is False
    assert context.abort_reason == "tool_result_admission_failed"
    assert context.messages == before_messages
    assert context.conversation_messages == before_audit
    assert context.context_generation == 0


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
    responses = [tool_response(ToolCall(f"call_{index}", "write_file", {})) for index in range(3)]
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
        [tool_response(ToolCall(f"call_{index}", "write_file", {})) for index in range(4)]
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
    context.config.context_recent_raw_tokens = 1_000
    for index in range(6):
        context.add_assistant_message(
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
            }
        )
        context.add_tool_result(f"call_{index}", "result " * 100)

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
    context.config.context_recent_raw_tokens = 1_000
    for index in range(4):
        context.add_assistant_message(
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
            }
        )
        context.add_tool_result(f"call_{index}", "entry\n" * 10_000)

    runner.run_until_idle(context)

    assert context.success is False
    assert context.abort_reason == "model_context_overflow"
    assert context.context_recovery_attempts == 1
    assert model.calls == 2
    assert _has_trace_type(context.trace.path, "context_recovery_skipped")


def test_unrecoverable_local_hard_pressure_stops_before_provider_call(
    tmp_path: Path,
) -> None:
    model = FakeModelClient([final_response("must not be called")])
    runner = make_runner(tmp_path, model)
    context = runner.create_context("inspect", include_initial_message=True)
    context.config.context_window_tokens = 1

    runner.run_until_idle(context)

    assert model.calls == 0
    assert context.success is False
    assert context.abort_reason == "model_context_overflow"
    assert _has_trace_type(context.trace.path, "context_rebase_failure")


def _has_trace_type(path: Path, event_type: str) -> bool:
    return any(
        json.loads(line).get("type") == event_type
        for line in path.read_text(encoding="utf-8").splitlines()
    )


def test_mcp_starts_only_after_agent_context_exists(tmp_path: Path) -> None:
    model = FakeModelClient([final_response()])
    runner = make_runner(tmp_path, model)

    class ContextCheckingRuntime:
        def __init__(self) -> None:
            self.started = False

        def start(self, context, registry) -> None:
            assert context.repo_path == tmp_path.resolve()
            assert context.trace.path.parent == context.run_dir
            assert registry is runner.runtime.tool_registry
            self.started = True

        def close(self, context) -> None:
            return None

    mcp_runtime = ContextCheckingRuntime()
    runner.runtime.mcp_runtime = mcp_runtime

    runner.create_context("inspect", include_initial_message=True)

    assert mcp_runtime.started is True
    assert model.calls == 0


def test_failed_mcp_startup_prevents_first_provider_call(tmp_path: Path) -> None:
    model = FakeModelClient([final_response()])
    runner = make_runner(tmp_path, model)

    class FailingRuntime:
        def start(self, context, registry) -> None:
            assert context.trace.path.exists()
            raise MCPStartupError("startup failed")

        def close(self, context) -> None:
            raise AssertionError("failed create_context is already cleaned by MCPRuntime.start")

    runner.runtime.mcp_runtime = FailingRuntime()

    with pytest.raises(MCPStartupError):
        runner.run("inspect")

    assert model.calls == 0


def test_mcp_closes_before_stop_hook(tmp_path: Path) -> None:
    model = FakeModelClient([final_response()])
    runner = make_runner(tmp_path, model)
    context = runner.create_context("inspect", include_initial_message=True)

    class ClosingRuntime:
        def __init__(self) -> None:
            self.close_count = 0

        def close(self, context) -> None:
            self.close_count += 1
            context.trace.log({"type": "mcp_runtime_closed"})

    mcp_runtime = ClosingRuntime()
    runner.runtime.mcp_runtime = mcp_runtime
    runner.finish(context)
    runner.finish(context)

    event_types = [
        json.loads(line)["type"]
        for line in context.trace.path.read_text(encoding="utf-8").splitlines()
    ]
    assert event_types.index("mcp_runtime_closed") < event_types.index("stop")
    assert mcp_runtime.close_count == 1


@pytest.mark.parametrize(
    ("policy", "execution_path", "phase", "search_visible", "call_visible"),
    [
        (PlanPolicy.OFF, ExecutionPath.UNDECIDED, PlanPhase.INACTIVE, True, True),
        (PlanPolicy.AUTO, ExecutionPath.DIRECT, PlanPhase.INACTIVE, True, True),
        (PlanPolicy.AUTO, ExecutionPath.UNDECIDED, PlanPhase.INACTIVE, True, False),
        (PlanPolicy.REQUIRED, ExecutionPath.PLAN, PlanPhase.PLANNING, True, False),
        (PlanPolicy.REQUIRED, ExecutionPath.PLAN, PlanPhase.AWAITING_APPROVAL, False, False),
        (PlanPolicy.REQUIRED, ExecutionPath.PLAN, PlanPhase.EXECUTING, True, True),
        (PlanPolicy.REQUIRED, ExecutionPath.PLAN, PlanPhase.COMPLETED, False, False),
        (PlanPolicy.REQUIRED, ExecutionPath.PLAN, PlanPhase.CANCELLED, False, False),
    ],
)
def test_existing_plan_capabilities_govern_mcp_gateway_visibility(
    policy,
    execution_path,
    phase,
    search_visible,
    call_visible,
) -> None:
    state = SimpleNamespace(
        policy=policy,
        execution_path=execution_path,
        phase=phase,
    )

    capabilities = plan_capabilities(state)
    assert capabilities.tool_is_visible("mcp_tool_search") is search_visible
    assert capabilities.tool_is_visible("mcp_tool_call") is call_visible


def test_mcp_search_continuation_keeps_base_tools_byte_identical(tmp_path: Path) -> None:
    model = FakeModelClient(
        [
            tool_response(ToolCall("search-1", "mcp_tool_search", {"query": "echo"})),
            final_response(),
        ]
    )
    runner = make_runner(tmp_path, model)
    mcp_runtime = FakeGatewayMCPRuntime()
    runner.runtime.mcp_runtime = mcp_runtime
    context = runner.create_context("find MCP tools", include_initial_message=True)

    runner.run_until_idle(context)

    assert len(model.tool_payloads) == 2
    assert model.tool_payloads[0] == model.tool_payloads[1]
    assert "mcp_tool_search" in model.tool_payloads[0]
    assert "mcp_tool_call" in model.tool_payloads[0]
    assert "mcp__demo__echo" not in model.tool_payloads[0]
    assert mcp_runtime.remote_calls == []


def test_mcp_call_continuation_keeps_base_tools_byte_identical(tmp_path: Path) -> None:
    arguments = {
        "tool": "mcp__demo__echo",
        "arguments": {"text": "hello"},
    }
    model = FakeModelClient(
        [
            tool_response(ToolCall("call-1", "mcp_tool_call", arguments)),
            final_response(),
        ]
    )
    runner = make_runner(tmp_path, model)
    mcp_runtime = FakeGatewayMCPRuntime()
    runner.runtime.mcp_runtime = mcp_runtime
    context = runner.create_context("call MCP", include_initial_message=True)
    context.approved_permission_scopes.add("mcp:demo:echo")

    runner.run_until_idle(context)

    assert len(model.tool_payloads) == 2
    assert model.tool_payloads[0] == model.tool_payloads[1]
    assert mcp_runtime.remote_calls == [("demo", "echo", {"text": "hello"})]


def test_planning_search_is_local_and_forged_call_is_unavailable(tmp_path: Path) -> None:
    model = FakeModelClient([final_response()])
    runner = make_runner(tmp_path, model)
    mcp_runtime = FakeGatewayMCPRuntime()
    runner.runtime.mcp_runtime = mcp_runtime
    context = runner.create_context("plan MCP use", include_initial_message=True)
    context.plan_state = PlanState.initial(PlanPolicy.REQUIRED, "plan MCP use")

    search_result = runner.runtime.executor.execute(
        ToolCall("search", "mcp_tool_search", {"query": "echo"}), context
    )
    call_result = runner.runtime.executor.execute(
        ToolCall(
            "call",
            "mcp_tool_call",
            {"tool": "mcp__demo__echo", "arguments": {"text": "blocked"}},
        ),
        context,
    )

    assert search_result.ok is True
    assert call_result.ok is False
    assert call_result.metadata["unavailable_tool"] is True
    assert mcp_runtime.remote_calls == []
