from __future__ import annotations

from dataclasses import dataclass

from agent.context import AgentContext
from runtime.artifact_store import ArtifactStore
from runtime.context_manager import ContextManager
from runtime.cost_tracker import CostTracker
from runtime.default_hooks import install_default_hooks
from runtime.diff_manager import DiffManager
from runtime.executor import ToolExecutor
from runtime.hooks import HookManager
from runtime.permission import PermissionGate
from runtime.recovery import TestRepairPolicy
from runtime.report_writer import ReportWriter
from runtime.trace_logger import TraceLogger
from tools.bash import BashTool
from tools.edit_file import EditFileTool
from tools.grep import GrepTool
from tools.list_dir import ListDirTool
from tools.read_file import ReadFileTool
from tools.registry import ToolRegistry
from tools.view_diff import ViewDiffTool


@dataclass(slots=True)
class RuntimeBundle:
    registry: ToolRegistry
    executor: ToolExecutor
    hooks: HookManager
    permission_gate: PermissionGate
    context_manager: ContextManager
    artifact_store: ArtifactStore
    trace_logger: TraceLogger
    cost_tracker: CostTracker
    diff_manager: DiffManager
    recovery: TestRepairPolicy
    report_writer: ReportWriter


def build_runtime(context: AgentContext) -> RuntimeBundle:
    context.run_dir.mkdir(parents=True, exist_ok=True)

    registry = ToolRegistry()
    registry.register(ListDirTool(context.repo_path))
    registry.register(GrepTool(context.repo_path))
    registry.register(ReadFileTool(context.repo_path))
    registry.register(EditFileTool(context.repo_path))
    registry.register(BashTool(context.repo_path))
    registry.register(ViewDiffTool(context.repo_path))

    hooks = HookManager()
    permission_gate = PermissionGate(context.repo_path)
    trace_logger = TraceLogger(context.run_dir)
    install_default_hooks(hooks, trace_logger)

    return RuntimeBundle(
        registry=registry,
        executor=ToolExecutor(registry, permission_gate, hooks),
        hooks=hooks,
        permission_gate=permission_gate,
        context_manager=ContextManager(),
        artifact_store=ArtifactStore(context.run_dir),
        trace_logger=trace_logger,
        cost_tracker=CostTracker(context.run_dir),
        diff_manager=DiffManager(context.repo_path, context.run_dir),
        recovery=TestRepairPolicy(),
        report_writer=ReportWriter(),
    )

