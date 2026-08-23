from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

from runtime.mcp import MCPRuntime, load_mcp_config
from tools.registry import ToolRegistry


FIXTURE_SERVER = Path(__file__).parents[1] / "fixtures" / "mcp_test_server.py"


class Trace:
    def __init__(self) -> None:
        self.events = []

    def log(self, event: dict) -> None:
        self.events.append(event)


def write_config(path: Path, server: dict) -> Path:
    path.write_text(json.dumps({"mcpServers": {"demo": server}}), encoding="utf-8")
    return path


def start_runtime(path: Path):
    runtime = MCPRuntime(load_mcp_config(path))
    registry = ToolRegistry()
    context = SimpleNamespace(trace=Trace())
    runtime.start(context, registry)
    return runtime, registry, context


def test_real_stdio_discovery_calls_and_process_reuse(tmp_path: Path) -> None:
    path = write_config(
        tmp_path / "stdio.json",
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [str(FIXTURE_SERVER), "--transport", "stdio"],
        },
    )
    runtime, registry, context = start_runtime(path)
    try:
        trace_text = json.dumps(context.trace.events)
        assert str(path) not in trace_text
        assert str(FIXTURE_SERVER) not in trace_text
        echo = registry.get("mcp__demo__echo")
        process_id = registry.get("mcp__demo__process_id")
        structured = registry.get("mcp__demo__structured")
        tool_error = registry.get("mcp__demo__tool_error")
        unsupported = registry.get("mcp__demo__unsupported_content")

        assert echo is not None
        assert process_id is not None
        assert structured is not None
        assert tool_error is not None
        assert unsupported is not None
        assert echo.call({"text": "hello"}, context).content == "hello"
        structured_result = structured.call({"value": 3}, context)
        assert structured_result.ok is True
        assert '[structured_content]\n{"double":6,"value":3}' in structured_result.content
        error_result = tool_error.call({}, context)
        assert error_result.ok is False
        assert error_result.metadata["mcp_error_kind"] == "tool_error"
        unsupported_result = unsupported.call({}, context)
        assert unsupported_result.ok is False
        assert unsupported_result.metadata["mcp_error_kind"] == "unsupported_content"
        assert "fixture-image" not in unsupported_result.content
        first_pid = process_id.call({}, context).content
        second_pid = process_id.call({}, context).content
        assert first_pid == second_pid
    finally:
        runtime.close(context)
        assert runtime._thread is None


def test_real_streamable_http_reuses_runtime_and_sdk_client_owner(tmp_path: Path) -> None:
    port = _unused_local_port()
    process = subprocess.Popen(
        [
            sys.executable,
            str(FIXTURE_SERVER),
            "--transport",
            "http",
            "--port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(port, process)
        path = write_config(
            tmp_path / "http.json",
            {"type": "http", "url": f"http://127.0.0.1:{port}/mcp"},
        )
        runtime, registry, context = start_runtime(path)
        try:
            assert f"127.0.0.1:{port}" not in json.dumps(context.trace.events)
            echo = registry.get("mcp__demo__echo")
            assert echo is not None
            assert echo.call({"text": "one"}, context).content == "one"
            assert echo.call({"text": "two"}, context).content == "two"
            assert runtime.started is True
        finally:
            runtime.close(context)
            assert runtime._thread is None
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


def _unused_local_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_port(port: int, process: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("MCP HTTP fixture stopped before accepting connections.")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise AssertionError("MCP HTTP fixture did not start in time.")
