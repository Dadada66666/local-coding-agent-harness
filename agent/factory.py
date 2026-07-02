from __future__ import annotations

from pathlib import Path

from agent.context import AgentContext, RunConfig
from agent.loop import AgentRunner
from agent.model_client import ModelClient
from runtime.bootstrap import build_runtime


def build_agent(repo_path: Path, task: str) -> AgentRunner:
    config = RunConfig(repo_path=repo_path.resolve(), task=task)
    context = AgentContext(config=config)
    runtime = build_runtime(context)
    return AgentRunner(context=context, model_client=ModelClient(), runtime=runtime)

