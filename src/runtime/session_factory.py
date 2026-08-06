from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from runtime.config import RunConfig
from runtime.observability.artifact_store import ArtifactStore
from runtime.observability.cost_tracker import CostTracker
from runtime.observability.diff_manager import DiffManager
from runtime.observability.report_writer import ReportWriter
from runtime.observability.trace_logger import TraceLogger
from runtime.security import PermissionGate
from runtime.security.access_policy import AccessPolicy
from runtime.security.environment_policy import EnvironmentPolicy
from runtime.security.redaction import SecretRedactor
from runtime.security.sandbox import SandboxRuntime
from runtime.session import AgentContext


def make_run_id() -> str:
    prefix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{uuid4().hex[:8]}"


def create_agent_session(
    *,
    repo_path: Path,
    task: str,
    permission_mode: str,
    config: RunConfig | None,
    initial_messages: list[dict],
    system_prompt: str,
    include_initial_message: bool,
    model_context_window_tokens: int | str | None = None,
) -> AgentContext:
    repo_path = repo_path.resolve()
    run_id = make_run_id()
    run_dir = repo_path / ".agent" / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    session_config = config or RunConfig(permission_mode=permission_mode)
    session_config.permission_mode = permission_mode
    if session_config.context_window_tokens is None and model_context_window_tokens:
        session_config.context_window_tokens = int(model_context_window_tokens)

    access_policy = AccessPolicy()
    environment_policy = EnvironmentPolicy(session_config.bash_env_allowlist)
    redactor = SecretRedactor.from_environment()
    sandbox = SandboxRuntime(
        repo_path=repo_path,
        run_dir=run_dir,
        config=session_config,
        access_policy=access_policy,
    )
    if (
        session_config.sandbox_fail_if_unavailable
        and sandbox.status.enabled
        and not sandbox.status.available
    ):
        raise RuntimeError(f"Sandbox requested but unavailable: {sandbox.status.reason}")

    context = AgentContext(
        run_id=run_id,
        task=task,
        repo_path=repo_path,
        run_dir=run_dir,
        messages=list(initial_messages),
        system_prompt=system_prompt,
        config=session_config,
        conversation_messages=list(initial_messages),
        permission_mode=session_config.permission_mode,
        permission_gate=PermissionGate(),
        trace=TraceLogger(run_dir, run_id=run_id, redactor=redactor),
        artifacts=ArtifactStore(run_dir),
        cost_tracker=CostTracker(run_dir),
        diff_manager=DiffManager(repo_path, run_dir),
        report_writer=ReportWriter(),
        sandbox=sandbox,
        access_policy=access_policy,
        environment_policy=environment_policy,
        redactor=redactor,
    )
    if include_initial_message:
        context.task_sequence = 1
        context.task_id = "task-1"
    return context
