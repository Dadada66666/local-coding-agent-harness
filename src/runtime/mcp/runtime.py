from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from concurrent.futures import Future
import json
import re
import threading
from typing import Any

from mcp import Client, MCPError, StdioServerParameters, stdio_client, types

from runtime.mcp.config import (
    HTTPMCPServerConfig,
    MCPConfig,
    MCPServerConfig,
    StdioMCPServerConfig,
)


MCP_READ_TIMEOUT_SECONDS = 120
_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")
_TOOL_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MCPStartupError(RuntimeError):
    pass


class MCPRuntimeClosedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class DiscoveredMCPTool:
    server_id: str
    remote_name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _ServerDiscovery:
    server_id: str
    transport: str
    protocol_version: str
    tools: tuple[DiscoveredMCPTool, ...]


@dataclass(slots=True)
class _StartCommand:
    future: Future[list[_ServerDiscovery]]


@dataclass(slots=True)
class _CallCommand:
    server_id: str
    remote_name: str
    arguments: dict[str, Any]
    future: Future[Any]


@dataclass(slots=True)
class _StopCommand:
    future: Future[None]


_Command = _StartCommand | _CallCommand | _StopCommand


class _StartupFailure(Exception):
    def __init__(self, stage: str, error_kind: str, exception_type: str) -> None:
        super().__init__("MCP startup failed.")
        self.stage = stage
        self.error_kind = error_kind
        self.exception_type = exception_type


class _PreflightFailure(Exception):
    def __init__(self, stage: str, error_kind: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.error_kind = error_kind


class MCPRuntime:
    """Sole owner of MCP SDK clients and their asynchronous lifecycle."""

    def __init__(self, config: MCPConfig) -> None:
        self.config = config
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[_Command] | None = None
        self._ready: Future[None] | None = None
        self._started = False
        self._closed = False
        self._lock = threading.Lock()

    @property
    def started(self) -> bool:
        return self._started

    @property
    def closed(self) -> bool:
        return self._closed

    def start(self, context, registry) -> None:
        with self._lock:
            if self._started:
                return
            if self._closed:
                raise MCPRuntimeClosedError("MCP runtime is closed.")
            self._start_worker()

        future: Future[list[_ServerDiscovery]] = Future()
        try:
            discoveries = self._submit(_StartCommand(future=future)).result()
            adapters = self._preflight_adapters(discoveries, registry)
        except _StartupFailure as exc:
            self._join_worker()
            self._closed = True
            self._trace_startup_failure(context, exc.stage, exc.exception_type, exc.error_kind)
            raise MCPStartupError("MCP startup failed before the first provider request.") from None
        except Exception as exc:
            try:
                self._stop_worker()
            except Exception as close_error:
                context.trace.log(
                    {
                        "type": "mcp_runtime_close_failed",
                        "exception_type": close_error.__class__.__name__,
                    }
                )
            finally:
                self._closed = True
            stage = exc.stage if isinstance(exc, _PreflightFailure) else "adapter"
            error_kind = exc.error_kind if isinstance(exc, _PreflightFailure) else "startup_error"
            self._trace_startup_failure(context, stage, exc.__class__.__name__, error_kind)
            raise MCPStartupError(
                "MCP startup validation failed before the first provider request."
            ) from None

        for adapter in adapters:
            registry.register(adapter)
        for discovery in discoveries:
            context.trace.log(
                {
                    "type": "mcp_server_connected",
                    "server_id": discovery.server_id,
                    "transport": discovery.transport,
                    "protocol_version": discovery.protocol_version,
                }
            )
            context.trace.log(
                {
                    "type": "mcp_tool_discovery_completed",
                    "server_id": discovery.server_id,
                    "tool_count": len(discovery.tools),
                    "exposed_tool_names": sorted(
                        _exposed_name(tool.server_id, tool.remote_name) for tool in discovery.tools
                    ),
                }
            )
        self._started = True

    def call_tool(
        self,
        server_id: str,
        remote_name: str,
        arguments: dict[str, Any],
    ) -> Any:
        if not self._started or self._closed:
            raise MCPRuntimeClosedError("MCP runtime is not available.")
        future: Future[Any] = Future()
        return self._submit(
            _CallCommand(
                server_id=server_id,
                remote_name=remote_name,
                arguments=arguments,
                future=future,
            )
        ).result()

    def close(self, context) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._stop_worker()
        context.trace.log({"type": "mcp_runtime_closed"})

    def _start_worker(self) -> None:
        self._ready = Future()
        self._thread = threading.Thread(
            target=self._worker_main,
            name="mcp-runtime",
            daemon=True,
        )
        self._thread.start()
        self._ready.result()

    def _worker_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._queue = asyncio.Queue()
        assert self._ready is not None
        self._ready.set_result(None)
        try:
            loop.run_until_complete(self._supervisor())
        finally:
            loop.close()

    async def _supervisor(self) -> None:
        assert self._queue is not None
        clients: dict[str, Client] = {}
        stop_future: Future[None] | None = None
        try:
            async with AsyncExitStack() as stack:
                while True:
                    command = await self._queue.get()
                    if isinstance(command, _StartCommand):
                        try:
                            discoveries = await self._connect_and_discover(stack, clients)
                        except Exception as exc:
                            failure = (
                                exc if isinstance(exc, _StartupFailure) else _startup_failure(exc)
                            )
                            command.future.set_exception(failure)
                            return
                        command.future.set_result(discoveries)
                        continue
                    if isinstance(command, _CallCommand):
                        try:
                            result = await clients[command.server_id].call_tool(
                                command.remote_name,
                                command.arguments,
                            )
                        except Exception as exc:
                            command.future.set_exception(exc)
                        else:
                            command.future.set_result(result)
                        continue
                    stop_future = command.future
                    break
        except Exception as exc:
            if stop_future is not None and not stop_future.done():
                stop_future.set_exception(exc)
            return
        else:
            if stop_future is not None:
                stop_future.set_result(None)

    async def _connect_and_discover(
        self,
        stack: AsyncExitStack,
        clients: dict[str, Client],
    ) -> list[_ServerDiscovery]:
        discoveries: list[_ServerDiscovery] = []
        for server in sorted(self.config.servers, key=lambda item: item.server_id):
            try:
                client = _sdk_client(server)
                entered = await stack.enter_async_context(client)
            except Exception as exc:
                raise _StartupFailure(
                    "connect",
                    _exception_kind(exc),
                    exc.__class__.__name__,
                ) from exc
            clients[server.server_id] = entered
            try:
                tools = await _discover_tools(entered, server.server_id)
            except Exception as exc:
                raise _StartupFailure(
                    "discovery",
                    (
                        "protocol_error"
                        if isinstance(exc, (ValueError, TypeError))
                        else _exception_kind(exc)
                    ),
                    exc.__class__.__name__,
                ) from exc
            discoveries.append(
                _ServerDiscovery(
                    server_id=server.server_id,
                    transport=server.transport,
                    protocol_version=str(entered.protocol_version),
                    tools=tools,
                )
            )
        return discoveries

    def _preflight_adapters(self, discoveries, registry) -> list[Any]:
        from tools.mcp_tool import MCPTool

        discovered = sorted(
            (tool for discovery in discoveries for tool in discovery.tools),
            key=lambda tool: (tool.server_id, tool.remote_name),
        )
        existing_names = set(registry.all_names())
        exposed_names: set[str] = set()
        adapters = []
        for tool in discovered:
            name = _exposed_name(tool.server_id, tool.remote_name)
            _validate_tool(tool, name)
            if name in existing_names or name in exposed_names:
                raise _PreflightFailure(
                    "collision",
                    "collision_error",
                    "MCP tool name collides with an existing exposed tool.",
                )
            exposed_names.add(name)
            adapters.append(
                MCPTool(
                    runtime=self,
                    server_id=tool.server_id,
                    remote_name=tool.remote_name,
                    name=name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                )
            )
        return adapters

    def _submit(self, command: _Command) -> Future[Any]:
        if self._loop is None or self._queue is None:
            raise MCPRuntimeClosedError("MCP runtime worker is not available.")
        self._loop.call_soon_threadsafe(self._queue.put_nowait, command)
        return command.future

    def _stop_worker(self) -> None:
        thread = self._thread
        if thread is None:
            self._loop = None
            self._queue = None
            return
        try:
            if thread.is_alive() and self._loop is not None and self._queue is not None:
                future: Future[None] = Future()
                try:
                    self._submit(_StopCommand(future=future)).result()
                finally:
                    thread.join()
        finally:
            self._thread = None
            self._loop = None
            self._queue = None

    def _join_worker(self) -> None:
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self._loop = None
        self._queue = None

    @staticmethod
    def _trace_startup_failure(context, stage: str, exception_type: str, error_kind: str) -> None:
        context.trace.log(
            {
                "type": "mcp_startup_failed",
                "stage": stage,
                "exception_type": exception_type,
                "error_kind": error_kind,
            }
        )


def _sdk_client(server: MCPServerConfig) -> Client:
    if isinstance(server, StdioMCPServerConfig):
        parameters = StdioServerParameters(
            command=server.command,
            args=list(server.args),
        )
        transport = stdio_client(parameters)
        return Client(
            transport,
            read_timeout_seconds=MCP_READ_TIMEOUT_SECONDS,
            input_required_max_rounds=0,
            cache=None,
        )
    if isinstance(server, HTTPMCPServerConfig):
        return Client(
            server.url,
            read_timeout_seconds=MCP_READ_TIMEOUT_SECONDS,
            input_required_max_rounds=0,
            cache=None,
        )
    raise TypeError("Unsupported MCP server configuration.")


async def _discover_tools(client: Client, server_id: str) -> tuple[DiscoveredMCPTool, ...]:
    cursor: str | None = None
    seen_cursors: set[str] = set()
    seen_names: set[str] = set()
    tools: list[DiscoveredMCPTool] = []
    while True:
        page = await client.list_tools(cursor=cursor)
        for tool in page.tools:
            if tool.name in seen_names:
                raise ValueError("MCP server returned a duplicate tool name.")
            seen_names.add(tool.name)
            tools.append(
                DiscoveredMCPTool(
                    server_id=server_id,
                    remote_name=tool.name,
                    description=(
                        tool.description
                        if tool.description and tool.description.strip()
                        else f"MCP tool {server_id}/{tool.name}."
                    ),
                    input_schema=tool.input_schema,
                )
            )
        next_cursor = page.next_cursor
        if next_cursor is None:
            break
        if next_cursor in seen_cursors:
            raise ValueError("MCP tool discovery returned a repeated cursor.")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    return tuple(tools)


def _validate_tool(tool: DiscoveredMCPTool, exposed_name: str) -> None:
    if not _valid_component(tool.remote_name):
        raise _PreflightFailure(
            "name",
            "name_error",
            "MCP tool name contains unsupported characters.",
        )
    if _TOOL_NAME_PATTERN.fullmatch(exposed_name) is None:
        raise _PreflightFailure(
            "name",
            "name_error",
            "Exposed MCP tool name is invalid or exceeds 64 characters.",
        )
    if not isinstance(tool.input_schema, dict):
        raise _PreflightFailure(
            "schema",
            "schema_error",
            "MCP tool input_schema must be an object.",
        )
    try:
        json.dumps(tool.input_schema, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise _PreflightFailure(
            "schema",
            "schema_error",
            "MCP tool input_schema must be JSON serializable.",
        ) from exc


def _valid_component(value: str) -> bool:
    return bool(value) and "__" not in value and _COMPONENT_PATTERN.fullmatch(value) is not None


def _exposed_name(server_id: str, remote_name: str) -> str:
    return f"mcp__{server_id}__{remote_name}"


def _startup_failure(exc: Exception) -> _StartupFailure:
    error_kind = (
        "protocol_error" if isinstance(exc, (ValueError, TypeError)) else _exception_kind(exc)
    )
    stage = "discovery" if isinstance(exc, (ValueError, TypeError)) else "connect"
    return _StartupFailure(stage, error_kind, exc.__class__.__name__)


def _exception_kind(exc: Exception) -> str:
    if isinstance(exc, MCPError) and exc.code == types.REQUEST_TIMEOUT:
        return "timeout_error"
    if isinstance(exc, TimeoutError):
        return "timeout_error"
    if isinstance(exc, MCPError):
        return "protocol_error"
    return "transport_error"
