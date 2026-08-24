from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
from types import SimpleNamespace

import pytest

from runtime.mcp.config import HTTPMCPServerConfig, MCPConfig, StdioMCPServerConfig
from runtime.mcp.runtime import (
    MCP_READ_TIMEOUT_SECONDS,
    MCPRuntime,
    MCPStartupError,
    _sdk_client,
)
from tools.base import BaseTool, ToolResult
from tools.mcp_tool import MCPToolSearch
from tools.registry import ToolRegistry


class FakeTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def log(self, event: dict) -> None:
        self.events.append(event)


@dataclass
class NativeTool(BaseTool):
    name: str
    description: str = "native"
    input_schema: dict | None = None

    def __post_init__(self) -> None:
        self.input_schema = self.input_schema or {"type": "object"}

    def call(self, args, context) -> ToolResult:
        return ToolResult(ok=True, content="ok")


class FakeClient:
    def __init__(
        self, pages: dict, *, call_result=None, enter_error: Exception | None = None
    ) -> None:
        self.pages = pages
        self.call_result = call_result
        self.enter_error = enter_error
        self.protocol_version = "2026-07-28"
        self.enter_count = 0
        self.exit_count = 0
        self.call_count = 0
        self.list_cursors = []
        self.owners = []

    async def __aenter__(self):
        self.enter_count += 1
        self._record_owner("enter")
        if self.enter_error is not None:
            raise self.enter_error
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.exit_count += 1
        self._record_owner("exit")

    async def list_tools(self, *, cursor=None):
        self._record_owner("list")
        self.list_cursors.append(cursor)
        return self.pages[cursor]

    async def call_tool(self, name, arguments):
        self.call_count += 1
        self._record_owner("call")
        return self.call_result

    def _record_owner(self, action: str) -> None:
        self.owners.append((action, id(asyncio.get_running_loop()), id(asyncio.current_task())))


def page(tools, next_cursor=None):
    return SimpleNamespace(tools=tools, next_cursor=next_cursor)


def remote_tool(name: str, schema=None, description: str | None = None):
    return SimpleNamespace(
        name=name,
        description=description,
        input_schema={"type": "object"} if schema is None else schema,
    )


def context():
    return SimpleNamespace(trace=FakeTrace())


def http_config(*server_ids: str) -> MCPConfig:
    return MCPConfig(
        tuple(
            HTTPMCPServerConfig(server_id=server_id, url=f"https://{server_id}.test/mcp")
            for server_id in server_ids
        )
    )


def test_runtime_is_dormant_until_start() -> None:
    runtime = MCPRuntime(http_config("demo"))

    assert runtime.started is False
    assert runtime.closed is False
    assert runtime._thread is None
    assert runtime._loop is None
    assert runtime._queue is None


def test_stdio_client_is_constructed_from_stdio_transport(monkeypatch) -> None:
    calls = []

    def fake_parameters(**kwargs):
        calls.append(("parameters", kwargs))
        return "parameters"

    def fake_stdio(parameters):
        calls.append(("stdio_client", parameters))
        return "transport"

    def fake_client(transport, **kwargs):
        calls.append(("Client", transport, kwargs))
        return "client"

    monkeypatch.setattr("runtime.mcp.runtime.StdioServerParameters", fake_parameters)
    monkeypatch.setattr("runtime.mcp.runtime.stdio_client", fake_stdio)
    monkeypatch.setattr("runtime.mcp.runtime.Client", fake_client)

    client = _sdk_client(StdioMCPServerConfig("demo", "python", ("server.py",)))

    assert client == "client"
    assert calls == [
        ("parameters", {"command": "python", "args": ["server.py"]}),
        ("stdio_client", "parameters"),
        (
            "Client",
            "transport",
            {
                "read_timeout_seconds": MCP_READ_TIMEOUT_SECONDS,
                "input_required_max_rounds": 0,
                "cache": None,
            },
        ),
    ]


def test_http_client_uses_fixed_timeout_without_extra_policy(monkeypatch) -> None:
    captured = {}

    def fake_client(server, **kwargs):
        captured.update(server=server, kwargs=kwargs)
        return "client"

    monkeypatch.setattr("runtime.mcp.runtime.Client", fake_client)

    result = _sdk_client(HTTPMCPServerConfig("demo", "https://example.test/mcp"))

    assert result == "client"
    assert captured == {
        "server": "https://example.test/mcp",
        "kwargs": {
            "read_timeout_seconds": 120,
            "input_required_max_rounds": 0,
            "cache": None,
        },
    }


def test_start_builds_sorted_catalog_registers_gateways_and_traces(monkeypatch) -> None:
    clients = {
        "zeta": FakeClient(
            {
                None: page([remote_tool("z")], "second"),
                "second": page([remote_tool("m")], "third"),
                "third": page([remote_tool("a")]),
            },
            call_result=SimpleNamespace(content=[]),
        ),
        "alpha": FakeClient({None: page([remote_tool("read")])}),
    }
    monkeypatch.setattr(
        "runtime.mcp.runtime._sdk_client",
        lambda server: clients[server.server_id],
    )
    runtime = MCPRuntime(http_config("alpha", "zeta"))
    registry = ToolRegistry()
    registry.register(NativeTool("native"))
    ctx = context()

    runtime.start(ctx, registry)
    runtime.start(ctx, registry)

    assert registry.all_names() == [
        "native",
        "mcp_tool_search",
        "mcp_tool_call",
    ]
    assert [entry.canonical_tool_id for entry in runtime.catalog] == [
        "mcp__alpha__read",
        "mcp__zeta__a",
        "mcp__zeta__m",
        "mcp__zeta__z",
    ]
    assert [event["type"] for event in ctx.trace.events] == [
        "mcp_server_connected",
        "mcp_tool_discovery_completed",
        "mcp_server_connected",
        "mcp_tool_discovery_completed",
        "mcp_catalog_ready",
    ]
    assert runtime.started is True
    assert clients["zeta"].list_cursors == [None, "second", "third"]
    assert runtime.resolve_catalog_tool("mcp__alpha__read").description == ("MCP tool alpha/read.")
    assert ctx.trace.events[-1] == {
        "type": "mcp_catalog_ready",
        "registered_mcp_tool_count": 4,
        "searchable_mcp_tool_count": 4,
    }
    assert not hasattr(runtime, "startup_events")
    assert not hasattr(runtime, "startup_event_buffer")

    runtime.close(ctx)

    assert all(client.exit_count == 1 for client in clients.values())
    assert ctx.trace.events[-1]["type"] == "mcp_runtime_closed"


def test_connect_discover_call_and_exit_share_one_loop_and_supervisor_task(monkeypatch) -> None:
    result = SimpleNamespace(content=[])
    client = FakeClient({None: page([remote_tool("echo")])}, call_result=result)
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()
    ctx = context()

    runtime.start(ctx, registry)
    assert runtime.call_tool("demo", "echo", {"value": 1}) is result
    runtime.close(ctx)

    assert len({owner[1] for owner in client.owners}) == 1
    assert len({owner[2] for owner in client.owners}) == 1
    assert client.call_count == 1


def test_call_failure_does_not_reconnect_remove_adapter_or_block_later_call(monkeypatch) -> None:
    class SequenceClient(FakeClient):
        async def call_tool(self, name, arguments):
            self.call_count += 1
            self._record_owner("call")
            if self.call_count == 1:
                raise ConnectionError("temporary transport failure")
            return self.call_result

    result = SimpleNamespace(content=[])
    client = SequenceClient({None: page([remote_tool("echo")])}, call_result=result)
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()
    ctx = context()
    runtime.start(ctx, registry)

    with pytest.raises(ConnectionError):
        runtime.call_tool("demo", "echo", {})

    assert runtime.call_tool("demo", "echo", {}) is result
    assert runtime.resolve_catalog_tool("mcp__demo__echo") is not None
    assert registry.get("mcp__demo__echo") is None
    assert client.enter_count == 1
    assert client.call_count == 2
    runtime.close(ctx)


def test_repeated_cursor_fails_startup_closes_resources_and_registers_nothing(monkeypatch) -> None:
    client = FakeClient(
        {
            None: page([remote_tool("one")], "again"),
            "again": page([remote_tool("two")], "again"),
        }
    )
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()
    ctx = context()

    with pytest.raises(MCPStartupError):
        runtime.start(ctx, registry)

    assert registry.all_names() == []
    assert client.exit_count == 1
    assert runtime.closed is True
    assert ctx.trace.events == [
        {
            "type": "mcp_startup_failed",
            "stage": "discovery",
            "exception_type": "ValueError",
            "error_kind": "protocol_error",
        }
    ]


def test_duplicate_remote_name_fails_startup_before_registration(monkeypatch) -> None:
    client = FakeClient({None: page([remote_tool("duplicate"), remote_tool("duplicate")])})
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()
    ctx = context()

    with pytest.raises(MCPStartupError):
        runtime.start(ctx, registry)

    assert registry.all_names() == []
    assert client.exit_count == 1
    assert ctx.trace.events[-1]["stage"] == "discovery"


def test_collision_preflight_registers_no_adapters_and_closes_client(monkeypatch) -> None:
    client = FakeClient({None: page([remote_tool("echo")])})
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()
    registry.register(NativeTool("mcp__demo__echo"))
    ctx = context()

    with pytest.raises(MCPStartupError):
        runtime.start(ctx, registry)

    assert registry.all_names() == ["mcp__demo__echo"]
    assert client.exit_count == 1
    assert ctx.trace.events[-1]["stage"] == "collision"


def test_failed_second_server_startup_closes_first_server_and_registers_nothing(
    monkeypatch,
) -> None:
    first = FakeClient({None: page([remote_tool("one")])})
    second = FakeClient({}, enter_error=ConnectionError("offline"))
    clients = {"first": first, "second": second}
    monkeypatch.setattr(
        "runtime.mcp.runtime._sdk_client",
        lambda server: clients[server.server_id],
    )
    runtime = MCPRuntime(http_config("first", "second"))
    registry = ToolRegistry()
    ctx = context()

    with pytest.raises(MCPStartupError):
        runtime.start(ctx, registry)

    assert registry.all_names() == []
    assert first.exit_count == 1
    assert second.exit_count == 0
    assert ctx.trace.events[-1]["stage"] == "connect"


@pytest.mark.parametrize(
    "remote_name",
    ["bad.name", "bad__name", "x" * 60],
)
def test_invalid_or_oversized_exposed_name_fails_before_registration(
    monkeypatch,
    remote_name: str,
) -> None:
    client = FakeClient({None: page([remote_tool(remote_name)])})
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()

    with pytest.raises(MCPStartupError):
        runtime.start(context(), registry)

    assert registry.all_names() == []
    assert client.exit_count == 1


@pytest.mark.parametrize(
    "schema",
    [[], {"type": "object", "bad": {1, 2}}],
)
def test_non_object_or_non_serializable_schema_fails_before_registration(
    monkeypatch,
    schema,
) -> None:
    client = FakeClient({None: page([remote_tool("echo", schema=schema)])})
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()

    with pytest.raises(MCPStartupError):
        runtime.start(context(), registry)

    assert registry.all_names() == []
    assert client.exit_count == 1


def test_registration_order_is_independent_of_config_and_discovery_order(monkeypatch) -> None:
    expected_ids = [
        "mcp__alpha__a",
        "mcp__alpha__z",
        "mcp__zeta__a",
        "mcp__zeta__z",
    ]

    def snapshot(server_ids, first_name: str, second_name: str):
        clients = {
            server_id: FakeClient(
                {
                    None: page([remote_tool(first_name)], "next"),
                    "next": page([remote_tool(second_name)]),
                }
            )
            for server_id in server_ids
        }
        monkeypatch.setattr(
            "runtime.mcp.runtime._sdk_client",
            lambda server: clients[server.server_id],
        )
        runtime = MCPRuntime(http_config(*server_ids))
        registry = ToolRegistry()
        ctx = context()
        runtime.start(ctx, registry)
        search_result = MCPToolSearch(runtime).call(
            {"query": "a z", "limit": 10},
            SimpleNamespace(),
        )
        result = (
            registry.all_names(),
            [entry.canonical_tool_id for entry in runtime.catalog],
            json.loads(search_result.content),
        )
        runtime.close(ctx)
        return result

    forward = snapshot(("alpha", "zeta"), "a", "z")
    reversed_order = snapshot(("zeta", "alpha"), "z", "a")

    assert forward == reversed_order
    assert forward[0] == ["mcp_tool_search", "mcp_tool_call"]
    assert forward[1] == expected_ids


def test_one_hundred_remote_tools_register_only_two_direct_gateways(monkeypatch) -> None:
    tools = [remote_tool(f"tool_{index}") for index in range(100)]
    client = FakeClient({None: page(tools)})
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()

    runtime.start(context(), registry)

    assert registry.all_names() == ["mcp_tool_search", "mcp_tool_call"]
    assert len(runtime.catalog) == 100
    assert all(registry.get(entry.canonical_tool_id) is None for entry in runtime.catalog)


def test_second_start_reuses_same_immutable_catalog(monkeypatch) -> None:
    client = FakeClient({None: page([remote_tool("echo")])})
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()
    ctx = context()

    runtime.start(ctx, registry)
    catalog = runtime.catalog
    runtime.start(ctx, registry)

    assert runtime.catalog is catalog
    assert client.list_cursors == [None]
    assert len([event for event in ctx.trace.events if event["type"] == "mcp_catalog_ready"]) == 1


@pytest.mark.parametrize("gateway_name", ["mcp_tool_search", "mcp_tool_call"])
def test_gateway_collision_fails_before_catalog_commit(monkeypatch, gateway_name: str) -> None:
    client = FakeClient({None: page([remote_tool("echo")])})
    monkeypatch.setattr("runtime.mcp.runtime._sdk_client", lambda server: client)
    runtime = MCPRuntime(http_config("demo"))
    registry = ToolRegistry()
    registry.register(NativeTool(gateway_name))
    ctx = context()

    with pytest.raises(MCPStartupError):
        runtime.start(ctx, registry)

    assert registry.all_names() == [gateway_name]
    assert runtime.catalog == ()
    assert client.exit_count == 1
