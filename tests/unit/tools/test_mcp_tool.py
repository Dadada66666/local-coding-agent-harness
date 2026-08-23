from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mcp import InputRequiredRoundsExceededError, MCPError, types
import pytest

from agent.loop import AgentLoop
from agent.messages import ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.context.budget import estimate_value_tokens
from tools.mcp_tool import MCPTool


class FakeRuntime:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def call_tool(self, server_id, remote_name, arguments):
        self.calls.append((server_id, remote_name, arguments))
        if self.error is not None:
            raise self.error
        return self.result


def make_tool(runtime: FakeRuntime, schema: dict | None = None) -> MCPTool:
    return MCPTool(
        runtime=runtime,
        server_id="demo",
        remote_name="echo",
        name="mcp__demo__echo",
        description="Echo input.",
        input_schema=schema or {"type": "object"},
    )


def test_schema_is_exposed_directly_without_duplicate_client_validation() -> None:
    schema = {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}
    tool = make_tool(FakeRuntime(), schema)

    assert tool.schema()["input_schema"] is schema
    assert tool.validate({"unexpected": True}, SimpleNamespace()) is None


def test_classifies_remote_call_for_existing_permission_gate() -> None:
    operation = make_tool(FakeRuntime()).classify_operation({}, SimpleNamespace())

    assert operation.kind == "mcp.call"
    assert operation.action == "echo"
    assert operation.subject == "demo/echo"
    assert operation.scope_key == "mcp:demo:echo"
    assert operation.is_sensitive is True
    assert operation.is_read_only is False
    assert operation.is_destructive is False
    assert operation.terminal_on_deny is False
    assert operation.paths == []
    assert operation.command is None


def test_converts_text_and_structured_content_deterministically() -> None:
    runtime = FakeRuntime(
        types.CallToolResult(
            content=[types.TextContent(text="first"), types.TextContent(text="second")],
            structuredContent={"z": 1, "a": "é"},
        )
    )
    result = make_tool(runtime).call({"text": "hello"}, SimpleNamespace())

    assert result.ok is True
    assert result.content == 'first\nsecond\n[structured_content]\n{"a":"é","z":1}'
    assert result.metadata["mcp_server_id"] == "demo"
    assert result.metadata["mcp_remote_tool"] == "echo"
    assert "mcp_duration_ms" in result.metadata
    assert runtime.calls == [("demo", "echo", {"text": "hello"})]


def test_structured_only_result_is_supported() -> None:
    runtime = FakeRuntime(types.CallToolResult(content=[], structuredContent={"ok": True}))

    result = make_tool(runtime).call({}, SimpleNamespace())

    assert result.ok is True
    assert result.content == '{"ok":true}'


def test_unsupported_content_rejects_the_whole_result_without_payload() -> None:
    runtime = FakeRuntime(
        types.CallToolResult(
            content=[
                types.TextContent(text="must not be returned"),
                types.ImageContent(data="secret-image", mimeType="image/png"),
            ]
        )
    )

    result = make_tool(runtime).call({}, SimpleNamespace())

    assert result.ok is False
    assert result.metadata["mcp_error_kind"] == "unsupported_content"
    assert "image" in result.content
    assert "secret-image" not in result.content
    assert "must not be returned" not in result.content


def test_server_error_is_a_normal_failed_tool_result() -> None:
    runtime = FakeRuntime(
        types.CallToolResult(content=[types.TextContent(text="invalid")], isError=True)
    )

    result = make_tool(runtime).call({}, SimpleNamespace())

    assert result.ok is False
    assert result.content == "invalid"
    assert result.metadata["mcp_error_kind"] == "tool_error"


def test_timeout_is_categorized_without_exposing_protocol_details() -> None:
    runtime = FakeRuntime(error=MCPError(types.REQUEST_TIMEOUT, "contains remote secret"))

    result = make_tool(runtime).call({}, SimpleNamespace())

    assert result.ok is False
    assert result.content == "MCP tool call timed out."
    assert result.metadata["mcp_error_kind"] == "timeout_error"
    assert "secret" not in result.content
    assert len(runtime.calls) == 1


@pytest.mark.parametrize(
    ("error", "expected_kind"),
    [
        (InputRequiredRoundsExceededError(0), "input_required"),
        (MCPError(-32000, "bad protocol"), "protocol_error"),
        (ConnectionError("offline"), "transport_error"),
    ],
)
def test_call_failures_are_normal_failed_results_without_retry(error, expected_kind) -> None:
    runtime = FakeRuntime(error=error)

    result = make_tool(runtime).call({}, SimpleNamespace())

    assert result.ok is False
    assert result.metadata["mcp_error_kind"] == expected_kind
    assert len(runtime.calls) == 1


def test_empty_result_fails_without_retry() -> None:
    runtime = FakeRuntime(types.CallToolResult(content=[]))

    result = make_tool(runtime).call({}, SimpleNamespace())

    assert result.ok is False
    assert result.metadata["mcp_error_kind"] == "tool_error"
    assert len(runtime.calls) == 1


@pytest.mark.parametrize(
    "block",
    [
        types.AudioContent(data="secret-audio-payload", mimeType="audio/wav"),
        types.ResourceLink(name="file", uri="file:///tmp/item"),
        types.EmbeddedResource(
            resource=types.TextResourceContents(uri="file:///tmp/item", text="body")
        ),
    ],
)
def test_each_non_text_content_type_is_rejected_without_payload(block) -> None:
    runtime = FakeRuntime(types.CallToolResult(content=[block]))

    result = make_tool(runtime).call({}, SimpleNamespace())

    assert result.ok is False
    assert result.metadata["mcp_error_kind"] == "unsupported_content"
    assert "body" not in result.content
    assert "secret-audio-payload" not in result.content


def test_non_json_structured_content_is_a_serialization_failure() -> None:
    runtime = FakeRuntime(types.CallToolResult(content=[], structuredContent={"bad": {1}}))

    result = make_tool(runtime).call({}, SimpleNamespace())

    assert result.ok is False
    assert result.metadata["mcp_error_kind"] == "serialization_error"


def test_later_explicit_call_uses_same_adapter_after_transport_failure() -> None:
    runtime = FakeRuntime(error=ConnectionError("offline"))
    tool = make_tool(runtime)
    first = tool.call({}, SimpleNamespace())
    runtime.error = None
    runtime.result = types.CallToolResult(content=[types.TextContent(text="recovered")])

    second = tool.call({}, SimpleNamespace())

    assert first.ok is False
    assert second.ok is True
    assert second.content == "recovered"
    assert len(runtime.calls) == 2


def test_permission_denial_prevents_remote_call(tmp_path: Path, monkeypatch) -> None:
    runtime = FakeRuntime(types.CallToolResult(content=[types.TextContent(text="remote")]))
    tool = make_tool(runtime)
    runner, context = _runner_with_mcp_tool(tmp_path, tool)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    result = runner.runtime.executor.execute(ToolCall("call-1", tool.name, {}), context)

    assert result.ok is False
    assert result.metadata["permission_denied"] is True
    assert runtime.calls == []


def test_approved_scope_is_reused_by_existing_permission_gate(tmp_path: Path, monkeypatch) -> None:
    runtime = FakeRuntime(types.CallToolResult(content=[types.TextContent(text="remote")]))
    tool = make_tool(runtime)
    runner, context = _runner_with_mcp_tool(tmp_path, tool)
    answers = iter(("a",))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    first = runner.runtime.executor.execute(ToolCall("call-1", tool.name, {}), context)
    second = runner.runtime.executor.execute(ToolCall("call-2", tool.name, {}), context)

    assert first.ok is True
    assert second.ok is True
    assert len(runtime.calls) == 2
    assert "mcp:demo:echo" in context.approved_permission_scopes


def test_large_mcp_result_uses_existing_artifact_pipeline(tmp_path: Path) -> None:
    original = "large-result\n" * 200
    runtime = FakeRuntime(types.CallToolResult(content=[types.TextContent(text=original)]))
    tool = make_tool(runtime)
    runner, context = _runner_with_mcp_tool(tmp_path, tool, max_result_chars=256)
    context.approved_permission_scopes.add("mcp:demo:echo")

    result = runner.runtime.executor.execute(ToolCall("large", tool.name, {}), context)

    assert result.ok is True
    assert result.artifact_id is not None
    assert result.metadata["persisted"] is True
    recovered = runner.runtime.tool_registry.get("read_artifact").call(
        {"artifact_id": result.artifact_id, "limit": 100},
        context,
    )
    assert recovered.ok is True
    assert recovered.content.startswith(original[:100])


def test_multi_mcp_result_batch_uses_existing_admission_before_visibility(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(types.CallToolResult(content=[types.TextContent(text="x" * 17_000)]))
    tool = make_tool(runtime)
    runner, context = _runner_with_mcp_tool(tmp_path, tool)
    context.approved_permission_scopes.add("mcp:demo:echo")
    calls = [ToolCall(f"call-{index}", tool.name, {}) for index in range(3)]
    results = [runner.runtime.executor.execute(call, context) for call in calls]
    messages_before = list(context.messages)

    admitted = runner.runtime.context_manager.admit_tool_results(
        context,
        calls,
        [(call.id, result.content, not result.ok) for call, result in zip(calls, results)],
    )

    assert context.messages == messages_before
    blocks = [
        {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
            **({"is_error": True} if is_error else {}),
        }
        for tool_use_id, content, is_error in admitted
    ]
    assert sum(estimate_value_tokens(block) for block in blocks) <= 12_000
    assert any("<persisted-output>" in content for _, content, _ in admitted)


def _runner_with_mcp_tool(
    tmp_path: Path,
    tool: MCPTool,
    *,
    max_result_chars: int = 18_000,
):
    runtime = build_runtime()
    runtime.tool_registry.register(tool)
    runner = AgentLoop(
        model_client=SimpleNamespace(context_window_tokens=None),
        runtime=runtime,
        repo_path=tmp_path,
        permission_mode="manual_approval",
        config=RunConfig(
            permission_mode="manual_approval",
            max_tool_result_chars=max_result_chars,
        ),
    )
    return runner, runner.create_context("call MCP", include_initial_message=True)
