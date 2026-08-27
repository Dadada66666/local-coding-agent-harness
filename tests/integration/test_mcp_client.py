from __future__ import annotations

import json
from pathlib import Path
import socket
import subprocess
import sys
import time
from types import SimpleNamespace

from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.mcp import MCPRuntime, load_mcp_config
from runtime.plan import PlanApprovalPolicy, PlanPhase, PlanPolicy
from runtime.security import PermissionMode
from runtime.security.permission_rules import PermissionRule, PermissionRuleValue
from runtime.task import TaskStatus
from tools.registry import ToolRegistry


FIXTURE_SERVER = Path(__file__).parents[1] / "fixtures" / "mcp_test_server.py"


class Trace:
    def __init__(self) -> None:
        self.events = []

    def log(self, event: dict) -> None:
        self.events.append(event)


class ScriptedModelClient:
    max_tokens = 4096
    context_window_tokens = 64000

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)

    def call(self, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        return self.responses.pop(0)


def tool_response(*calls: ToolCall) -> ModelResponse:
    return ModelResponse(
        message={
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
                for call in calls
            ],
        },
        tool_calls=list(calls),
        usage=TokenUsage(),
    )


def final_response(text: str) -> ModelResponse:
    return ModelResponse(
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
        text=text,
        usage=TokenUsage(),
    )


def write_config(path: Path, server: dict) -> Path:
    path.write_text(json.dumps({"mcpServers": {"demo": server}}), encoding="utf-8")
    return path


def start_runtime(path: Path):
    runtime = MCPRuntime(load_mcp_config(path))
    registry = ToolRegistry()
    context = SimpleNamespace(trace=Trace())
    runtime.start(context, registry)
    return runtime, registry, context


def test_unattended_required_plan_with_preauthorized_mcp_scope_reaches_terminal_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "playwright-mcp.json"
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "playwright": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(FIXTURE_SERVER), "--transport", "stdio"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config = RunConfig(
        permission_mode=PermissionMode.ACCEPT_EDITS,
        permission_prompt_policy="deny",
        plan_policy=PlanPolicy.REQUIRED,
        plan_approval_policy=PlanApprovalPolicy.AUTO,
        mcp_config_path=str(config_path),
    )
    model = ScriptedModelClient(
        [
            tool_response(
                ToolCall(
                    "plan",
                    "update_plan",
                    {
                        "action": "replace_plan",
                        "steps": [{"id": "call", "description": "Call the MCP tool"}],
                        "submit": True,
                    },
                )
            ),
            tool_response(
                ToolCall(
                    "remote",
                    "mcp_tool_call",
                    {
                        "tool": "mcp__playwright__echo",
                        "arguments": {"text": "browser verified"},
                    },
                )
            ),
            tool_response(
                ToolCall(
                    "step",
                    "update_plan",
                    {"action": "update_step", "step_id": "call", "status": "completed"},
                )
            ),
            tool_response(ToolCall("complete", "update_plan", {"action": "complete"})),
            final_response("benchmark complete"),
        ]
    )
    runner = AgentLoop(
        model_client=model,
        runtime=build_runtime(config),
        repo_path=tmp_path,
        permission_mode=PermissionMode.ACCEPT_EDITS,
        config=config,
    )
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt="": (_ for _ in ()).throw(AssertionError("stdin was read")),
    )
    context = runner.start_interactive()
    context.permission_rules.add(
        PermissionRule(
            source="policy",
            behavior="allow",
            value=PermissionRuleValue(
                tool_name="mcp_tool_call",
                operation_scope="mcp:playwright:echo",
            ),
        )
    )

    try:
        runner.start_task(context, "Run the deterministic MCP benchmark")
    finally:
        runner.finish(context)

    assert context.task_status is TaskStatus.COMPLETED
    assert context.success is True
    assert context.final_text == "benchmark complete"
    assert context.plan_state.phase is PlanPhase.COMPLETED
    assert context.plan_state.approved_version == context.plan_state.version
    assert context.plan_state.approval_source == "auto_policy"
    assert not model.responses
    events = [
        json.loads(line) for line in context.trace.path.read_text(encoding="utf-8").splitlines()
    ]
    assert not any(event.get("type") == "permission_user_response" for event in events)
    assert any(
        event.get("type") == "tool_result"
        and event.get("tool") == "mcp_tool_call"
        and event.get("ok") is True
        for event in events
    )


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
        search = registry.get("mcp_tool_search")
        call = registry.get("mcp_tool_call")

        assert search is not None
        assert call is not None
        assert registry.get("mcp__demo__echo") is None
        found = json.loads(search.call({"query": "echo"}, context).content)
        assert found["tools"][0]["canonical_tool_id"] == "mcp__demo__echo"
        assert (
            call.call({"tool": "mcp__demo__echo", "arguments": {"text": "hello"}}, context).content
            == "hello"
        )
        structured_result = call.call(
            {"tool": "mcp__demo__structured", "arguments": {"value": 3}}, context
        )
        assert structured_result.ok is True
        assert '[structured_content]\n{"double":6,"value":3}' in structured_result.content
        error_result = call.call({"tool": "mcp__demo__tool_error", "arguments": {}}, context)
        assert error_result.ok is False
        assert error_result.metadata["mcp_error_kind"] == "tool_error"
        unsupported_result = call.call(
            {"tool": "mcp__demo__unsupported_content", "arguments": {}}, context
        )
        assert unsupported_result.ok is False
        assert unsupported_result.metadata["mcp_error_kind"] == "unsupported_content"
        assert "fixture-image" not in unsupported_result.content
        first_pid = call.call({"tool": "mcp__demo__process_id", "arguments": {}}, context).content
        second_pid = call.call({"tool": "mcp__demo__process_id", "arguments": {}}, context).content
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
            search = registry.get("mcp_tool_search")
            call = registry.get("mcp_tool_call")
            assert search is not None
            assert call is not None
            assert json.loads(search.call({"query": "echo"}, context).content)["result_count"] == 1
            assert (
                call.call(
                    {"tool": "mcp__demo__echo", "arguments": {"text": "one"}}, context
                ).content
                == "one"
            )
            assert (
                call.call(
                    {"tool": "mcp__demo__echo", "arguments": {"text": "two"}}, context
                ).content
                == "two"
            )
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
