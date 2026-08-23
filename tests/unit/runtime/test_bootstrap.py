from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent.factory as agent_factory
from runtime.bootstrap import build_runtime, build_tool_registry
from runtime.config import RunConfig
from runtime.mcp import MCPConfigError


NATIVE_TOOL_NAMES = [
    "list_dir",
    "grep",
    "read_file",
    "read_artifact",
    "history_list_windows",
    "history_list_items",
    "history_search_contents",
    "history_read_item",
    "write_file",
    "edit_file",
    "delete_file",
    "bash",
    "view_diff",
    "select_execution_mode",
    "resolve_plan_response",
    "update_plan",
]


def write_config(path: Path) -> Path:
    path.write_text(
        json.dumps({"mcpServers": {"demo": {"type": "http", "url": "https://example.test/mcp"}}}),
        encoding="utf-8",
    )
    return path


def test_zero_config_keeps_native_registry_and_starts_no_mcp_resources() -> None:
    runtime = build_runtime()

    assert runtime.mcp_runtime is None
    assert runtime.tool_registry.all_names() == NATIVE_TOOL_NAMES
    assert runtime.tool_registry.schemas() == build_tool_registry().schemas()


def test_configured_runtime_is_dormant_after_bootstrap(tmp_path: Path) -> None:
    path = write_config(tmp_path / "mcp.json")

    runtime = build_runtime(RunConfig(mcp_config_path=str(path)))

    assert runtime.mcp_runtime is not None
    assert runtime.mcp_runtime.started is False
    assert runtime.mcp_runtime._thread is None
    assert runtime.tool_registry.all_names() == NATIVE_TOOL_NAMES


def test_agent_runner_construction_starts_no_external_mcp_resources(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = write_config(tmp_path / "mcp.json")
    monkeypatch.setattr(
        agent_factory,
        "ModelClient",
        lambda: SimpleNamespace(context_window_tokens=None),
    )

    runner = agent_factory.build_agent_runner(
        repo_path=tmp_path,
        config=RunConfig(mcp_config_path=str(path)),
    )

    assert runner.runtime.mcp_runtime is not None
    assert runner.runtime.mcp_runtime._thread is None
    assert runner.runtime.mcp_runtime.started is False


def test_invalid_config_fails_runner_construction_without_starting_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "mcp.json"
    path.write_text('{"mcpServers": {}}', encoding="utf-8")
    monkeypatch.setattr(
        agent_factory,
        "ModelClient",
        lambda: SimpleNamespace(context_window_tokens=None),
    )

    with pytest.raises(MCPConfigError):
        agent_factory.build_agent_runner(
            repo_path=tmp_path,
            config=RunConfig(mcp_config_path=str(path)),
        )
