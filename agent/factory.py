from __future__ import annotations

from pathlib import Path

from agent.context import RunConfig
from agent.loop import AgentLoop
from agent.model_client import ModelClient
from runtime.bootstrap import build_runtime


def build_agent_runner(
    repo_path: Path | None = None,
    permission_mode: str = "manual_approval",
    config: RunConfig | None = None,
) -> AgentLoop:
    runtime = build_runtime()
    workdir = (repo_path or Path.cwd()).resolve()
    return AgentLoop(
        model_client=ModelClient(),
        runtime=runtime,
        repo_path=workdir,
        permission_mode=permission_mode,
        config=config,
    )


def build_agent(repo_path: Path | None = None, permission_mode: str = "manual_approval") -> AgentLoop:
    return build_agent_runner(repo_path=repo_path, permission_mode=permission_mode)