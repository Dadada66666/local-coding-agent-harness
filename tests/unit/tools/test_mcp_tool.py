from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from mcp import InputRequiredRoundsExceededError, MCPError, types
import pytest

from agent.loop import AgentLoop
from agent.messages import ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.context.budget import estimate_value_tokens
from runtime.mcp.runtime import MCPCatalogEntry
from tools.base import ToolValidationError
from tools.mcp_tool import MCPTool, MCPToolCall, MCPToolSearch, search_terms


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


class CatalogRuntime:
    def __init__(self, entries: list[MCPCatalogEntry]) -> None:
        self.catalog = tuple(entries)
        self._entries = {entry.canonical_tool_id: entry for entry in entries}

    def resolve_catalog_tool(self, canonical_tool_id: str):
        return self._entries.get(canonical_tool_id)


def make_tool(runtime: FakeRuntime, schema: dict | None = None) -> MCPTool:
    return MCPTool(
        runtime=runtime,
        server_id="demo",
        remote_name="echo",
        name="mcp__demo__echo",
        description="Echo input.",
        input_schema=schema or {"type": "object"},
    )


def make_entry(
    *,
    server_id: str = "demo",
    remote_name: str = "echo",
    description: str = "Echo input.",
    schema: dict | None = None,
    runtime: FakeRuntime | None = None,
) -> MCPCatalogEntry:
    input_schema = schema or {"type": "object"}
    remote_runtime = runtime or FakeRuntime(
        types.CallToolResult(content=[types.TextContent(text="ok")])
    )
    canonical_tool_id = f"mcp__{server_id}__{remote_name}"
    binding = MCPTool(
        runtime=remote_runtime,
        server_id=server_id,
        remote_name=remote_name,
        name=canonical_tool_id,
        description=description,
        input_schema=input_schema,
    )
    properties = input_schema.get("properties")
    property_names = properties if isinstance(properties, dict) else {}
    return MCPCatalogEntry(
        canonical_tool_id=canonical_tool_id,
        server_id=server_id,
        remote_name=remote_name,
        description=description,
        input_schema=input_schema,
        binding=binding,
        server_terms=search_terms(server_id),
        name_terms=search_terms(remote_name),
        description_terms=search_terms(description),
        property_terms=frozenset(term for name in property_names for term in search_terms(name)),
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


def test_gateway_schemas_are_fixed_and_remote_schema_is_not_direct() -> None:
    entry = make_entry(
        schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
    )
    runtime = CatalogRuntime([entry])

    assert MCPToolSearch(runtime).schema()["input_schema"] == {
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "maxLength": 256},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
        },
        "required": ["query"],
        "additionalProperties": False,
    }
    assert MCPToolCall(runtime).schema()["input_schema"] == {
        "type": "object",
        "properties": {
            "tool": {"type": "string", "minLength": 1},
            "arguments": {"type": "object"},
        },
        "required": ["tool", "arguments"],
        "additionalProperties": False,
    }
    assert MCPToolCall(runtime).schema()["input_schema"] is not entry.input_schema


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"query": "echo", "extra": True},
        {"query": ""},
        {"query": "---"},
        {"query": "x" * 257},
        {"query": "echo", "limit": True},
        {"query": "echo", "limit": 0},
        {"query": "echo", "limit": 11},
    ],
)
def test_search_validation_is_exact_and_local(args) -> None:
    tool = MCPToolSearch(CatalogRuntime([make_entry()]))

    with pytest.raises(ToolValidationError):
        tool.validate(args, SimpleNamespace())


def test_search_ranking_normalization_weights_and_tie_break_are_deterministic() -> None:
    entries = [
        make_entry(
            server_id="zeta",
            remote_name="find_issue",
            description="Search repository issues",
            schema={"type": "object", "properties": {"query_text": {"type": "string"}}},
        ),
        make_entry(
            server_id="alpha",
            remote_name="search",
            description="Find issue records",
            schema={"type": "object", "properties": {"issue": {"type": "string"}}},
        ),
        make_entry(
            server_id="beta",
            remote_name="search",
            description="Find issue records",
            schema={"type": "object", "properties": {"issue": {"type": "string"}}},
        ),
        make_entry(server_id="other", remote_name="unrelated", description="No match"),
    ]
    tool = MCPToolSearch(CatalogRuntime(entries))
    tool.validate({"query": "ＦＩＮＤ---ISSUE", "limit": 10}, SimpleNamespace())

    result = tool.call({"query": "ＦＩＮＤ---ISSUE", "limit": 10}, SimpleNamespace())
    payload = json.loads(result.content)

    assert [item["canonical_tool_id"] for item in payload["tools"]] == [
        "mcp__zeta__find_issue",
        "mcp__alpha__search",
        "mcp__beta__search",
    ]
    assert payload["result_count"] == 3
    assert result.metadata == {
        "mcp_search_query": "ＦＩＮＤ---ISSUE",
        "mcp_search_result_count": 3,
    }


def test_search_exact_canonical_and_remote_name_bonuses() -> None:
    entries = [
        make_entry(server_id="zeta", remote_name="echo", description="echo"),
        make_entry(server_id="alpha", remote_name="echo", description="echo"),
    ]
    tool = MCPToolSearch(CatalogRuntime(entries))

    canonical = json.loads(tool.call({"query": "mcp__zeta__echo"}, SimpleNamespace()).content)
    remote = json.loads(tool.call({"query": "echo"}, SimpleNamespace()).content)

    assert canonical["tools"][0]["canonical_tool_id"] == "mcp__zeta__echo"
    assert [item["canonical_tool_id"] for item in remote["tools"]] == [
        "mcp__alpha__echo",
        "mcp__zeta__echo",
    ]


def test_search_default_limit_is_five_and_hard_limit_is_ten() -> None:
    entries = [
        make_entry(server_id=f"server{index}", remote_name=f"search_{index}") for index in range(12)
    ]
    tool = MCPToolSearch(CatalogRuntime(entries))

    default = json.loads(tool.call({"query": "search"}, SimpleNamespace()).content)
    maximum = json.loads(tool.call({"query": "search", "limit": 10}, SimpleNamespace()).content)

    assert default["result_count"] == 5
    assert maximum["result_count"] == 10


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"tool": "mcp__demo__echo", "arguments": {}, "extra": True},
        {"tool": "", "arguments": {}},
        {"tool": "mcp__demo__echo", "arguments": []},
        {"tool": "MCP__demo__echo", "arguments": {}},
    ],
)
def test_call_gateway_rejects_invalid_or_inexact_arguments(args) -> None:
    tool = MCPToolCall(CatalogRuntime([make_entry()]))

    with pytest.raises(ToolValidationError):
        tool.validate(args, SimpleNamespace())


def test_call_gateway_delegates_exact_binding_without_argument_rewrite() -> None:
    remote = FakeRuntime(types.CallToolResult(content=[types.TextContent(text="remote")]))
    entry = make_entry(runtime=remote)
    tool = MCPToolCall(CatalogRuntime([entry]))
    args = {"tool": entry.canonical_tool_id, "arguments": {"unexpected": True}}

    tool.validate(args, SimpleNamespace())
    operation = tool.classify_operation(args, SimpleNamespace())
    result = tool.call(args, SimpleNamespace())

    assert operation.scope_key == "mcp:demo:echo"
    assert result.ok is True
    assert result.content == "remote"
    assert remote.calls == [("demo", "echo", {"unexpected": True})]


def test_search_and_calls_never_mutate_the_direct_gateway_schemas() -> None:
    first_runtime = FakeRuntime(types.CallToolResult(content=[types.TextContent(text="first")]))
    second_runtime = FakeRuntime(types.CallToolResult(content=[types.TextContent(text="second")]))
    runtime = CatalogRuntime(
        [
            make_entry(server_id="alpha", remote_name="echo", runtime=first_runtime),
            make_entry(server_id="beta", remote_name="search", runtime=second_runtime),
        ]
    )
    search = MCPToolSearch(runtime)
    call = MCPToolCall(runtime)
    before = json.dumps(
        [search.schema(), call.schema()],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    search.call({"query": "echo"}, SimpleNamespace())
    search.call({"query": "search"}, SimpleNamespace())
    call.call(
        {"tool": "mcp__alpha__echo", "arguments": {"value": 1}},
        SimpleNamespace(),
    )
    call.call(
        {"tool": "mcp__beta__search", "arguments": {"value": 2}},
        SimpleNamespace(),
    )
    after = json.dumps(
        [search.schema(), call.schema()],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    assert after == before
    assert first_runtime.calls == [("alpha", "echo", {"value": 1})]
    assert second_runtime.calls == [("beta", "search", {"value": 2})]


def test_schema_economics_is_constant_for_five_twenty_five_and_one_hundred_tools() -> None:
    v1_bytes = []
    v1_tokens = []
    v2_bytes = []
    v2_tokens = []
    for count in (5, 25, 100):
        entries = [
            make_entry(
                server_id="demo",
                remote_name=f"search_{index}",
                description=f"Search record {index} by repository issue metadata.",
                schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "owner": {"type": "string"},
                        "state": {"type": "string"},
                        "filters": {
                            "type": "object",
                            "properties": {"labels": {"type": "array"}},
                        },
                    },
                },
            )
            for index in range(count)
        ]
        runtime = CatalogRuntime(entries)
        v1_surface = [entry.binding.schema() for entry in entries]
        v2_surface = [MCPToolSearch(runtime).schema(), MCPToolCall(runtime).schema()]
        v1_serialized = json.dumps(
            v1_surface, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        v2_serialized = json.dumps(
            v2_surface, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        v1_bytes.append(len(v1_serialized.encode("utf-8")))
        v1_tokens.append(estimate_value_tokens(v1_surface))
        v2_bytes.append(len(v2_serialized.encode("utf-8")))
        v2_tokens.append(estimate_value_tokens(v2_surface))

        search_result = MCPToolSearch(runtime).call({"query": "search"}, SimpleNamespace())
        payload = json.loads(search_result.content)
        assert payload["result_count"] == 5
        assert estimate_value_tokens(search_result.content) <= 12_000

    assert v1_bytes[0] < v1_bytes[1] < v1_bytes[2]
    assert v1_tokens[0] < v1_tokens[1] < v1_tokens[2]
    assert v2_bytes[0] == v2_bytes[1] == v2_bytes[2]
    assert v2_tokens[0] == v2_tokens[1] == v2_tokens[2]


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
    runner, context = _runner_with_mcp_gateway(tmp_path, tool)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    result = runner.runtime.executor.execute(
        ToolCall(
            "call-1",
            "mcp_tool_call",
            {"tool": tool.name, "arguments": {}},
        ),
        context,
    )

    assert result.ok is False
    assert result.metadata["permission_denied"] is True
    assert runtime.calls == []


def test_approved_scope_is_reused_by_existing_permission_gate(tmp_path: Path, monkeypatch) -> None:
    runtime = FakeRuntime(types.CallToolResult(content=[types.TextContent(text="remote")]))
    tool = make_tool(runtime)
    runner, context = _runner_with_mcp_gateway(tmp_path, tool)
    answers = iter(("a",))
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    arguments = {"tool": tool.name, "arguments": {}}
    first = runner.runtime.executor.execute(ToolCall("call-1", "mcp_tool_call", arguments), context)
    second = runner.runtime.executor.execute(
        ToolCall("call-2", "mcp_tool_call", arguments), context
    )

    assert first.ok is True
    assert second.ok is True
    assert len(runtime.calls) == 2
    assert "mcp:demo:echo" in context.approved_permission_scopes


def test_large_mcp_result_uses_existing_artifact_pipeline(tmp_path: Path) -> None:
    original = "large-result\n" * 200
    runtime = FakeRuntime(types.CallToolResult(content=[types.TextContent(text=original)]))
    tool = make_tool(runtime)
    runner, context = _runner_with_mcp_gateway(tmp_path, tool, max_result_chars=256)
    context.approved_permission_scopes.add("mcp:demo:echo")

    result = runner.runtime.executor.execute(
        ToolCall(
            "large",
            "mcp_tool_call",
            {"tool": tool.name, "arguments": {}},
        ),
        context,
    )

    assert result.ok is True
    assert result.artifact_id is not None
    assert result.metadata["persisted"] is True
    recovered = runner.runtime.tool_registry.get("read_artifact").call(
        {"artifact_id": result.artifact_id, "limit": 100},
        context,
    )
    assert recovered.ok is True
    assert recovered.content.startswith(original[:100])


def test_large_search_result_uses_existing_artifact_pipeline(tmp_path: Path) -> None:
    entries = [
        make_entry(
            server_id="demo",
            remote_name=f"search_{index}",
            description="search " + ("large-description " * 30),
            schema={
                "type": "object",
                "properties": {f"query_{index}": {"type": "string", "description": "x" * 500}},
            },
        )
        for index in range(5)
    ]
    runtime = build_runtime()
    runtime.tool_registry.register(MCPToolSearch(CatalogRuntime(entries)))
    runner = AgentLoop(
        model_client=SimpleNamespace(context_window_tokens=None),
        runtime=runtime,
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", max_tool_result_chars=256),
    )
    context = runner.create_context("search MCP", include_initial_message=True)

    result = runner.runtime.executor.execute(
        ToolCall("search", "mcp_tool_search", {"query": "search"}), context
    )

    assert result.ok is True
    assert result.artifact_id is not None
    assert result.metadata["persisted"] is True
    recovered = runner.runtime.tool_registry.get("read_artifact").call(
        {"artifact_id": result.artifact_id, "limit": 100}, context
    )
    assert recovered.ok is True
    assert '"canonical_tool_id"' in recovered.content


def test_multi_mcp_result_batch_uses_existing_admission_before_visibility(
    tmp_path: Path,
) -> None:
    runtime = FakeRuntime(types.CallToolResult(content=[types.TextContent(text="x" * 17_000)]))
    tool = make_tool(runtime)
    runner, context = _runner_with_mcp_gateway(tmp_path, tool)
    context.approved_permission_scopes.add("mcp:demo:echo")
    calls = [
        ToolCall(
            f"call-{index}",
            "mcp_tool_call",
            {"tool": tool.name, "arguments": {}},
        )
        for index in range(3)
    ]
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


def _runner_with_mcp_gateway(
    tmp_path: Path,
    tool: MCPTool,
    *,
    max_result_chars: int = 18_000,
):
    runtime = build_runtime()
    entry = make_entry(
        server_id=tool.server_id,
        remote_name=tool.remote_name,
        description=tool.description,
        schema=tool.input_schema,
        runtime=tool.runtime,
    )
    runtime.tool_registry.register(MCPToolCall(CatalogRuntime([entry])))
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
