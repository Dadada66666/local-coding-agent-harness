from __future__ import annotations

from dataclasses import dataclass

from runtime.context.manager import ContextManager
from runtime.hooks.lifecycle import model_call_start_hook, stop_report_hook, user_prompt_submit_hook
from runtime.hooks.policy import large_output_hook, permission_hook, secret_redaction_hook
from runtime.hooks.tracking import (
    failure_history_hook,
    mutation_outcome_hook,
    mutation_result_hook,
    plan_progress_hook,
    post_tool_trace_hook,
    record_tool_budget_hook,
    pre_tool_trace_hook,
    test_result_hook,
)
from runtime.executor import ToolExecutor
from runtime.hooks import HookEvent, HookManager
from runtime.progress import ToolProgressPolicy
from runtime.plan.gate import plan_gate_hook
from runtime.recovery import RecoveryPolicy
from tools.bash import BashTool
from tools.delete_file import DeleteFileTool
from tools.edit_file import EditFileTool
from tools.grep import GrepTool
from tools.list_dir import ListDirTool
from tools.read_artifact import ReadArtifactTool
from tools.read_file import ReadFileTool
from tools.resolve_plan_response import ResolvePlanResponseTool
from tools.registry import ToolRegistry
from tools.select_execution_mode import SelectExecutionModeTool
from tools.update_plan import UpdatePlanTool
from tools.view_diff import ViewDiffTool
from tools.write_file import WriteFileTool


@dataclass
class RuntimeBundle:
    tool_registry: ToolRegistry
    executor: ToolExecutor
    context_manager: ContextManager
    hooks: HookManager
    recovery_policy: RecoveryPolicy
    progress_policy: ToolProgressPolicy


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ListDirTool())
    registry.register(GrepTool())
    registry.register(ReadFileTool())
    registry.register(ReadArtifactTool())
    registry.register(WriteFileTool())
    registry.register(EditFileTool())
    registry.register(DeleteFileTool())
    registry.register(BashTool())
    registry.register(ViewDiffTool())
    registry.register(SelectExecutionModeTool())
    registry.register(ResolvePlanResponseTool())
    registry.register(UpdatePlanTool())
    return registry


def build_hooks() -> HookManager:
    hooks = HookManager()

    hooks.register(HookEvent.USER_PROMPT_SUBMIT, user_prompt_submit_hook)
    hooks.register(HookEvent.MODEL_CALL_START, model_call_start_hook)

    hooks.register(HookEvent.PRE_TOOL_USE, pre_tool_trace_hook)
    hooks.register(HookEvent.PRE_TOOL_USE, plan_gate_hook)
    hooks.register(HookEvent.PRE_TOOL_USE, permission_hook)

    hooks.register(HookEvent.POST_TOOL_USE, secret_redaction_hook)
    hooks.register(HookEvent.POST_TOOL_USE, large_output_hook)
    hooks.register(HookEvent.POST_TOOL_USE, record_tool_budget_hook)
    hooks.register(HookEvent.POST_TOOL_USE, mutation_result_hook)
    hooks.register(HookEvent.POST_TOOL_USE, mutation_outcome_hook)
    hooks.register(HookEvent.POST_TOOL_USE, test_result_hook)
    hooks.register(HookEvent.POST_TOOL_USE, failure_history_hook)
    hooks.register(HookEvent.POST_TOOL_USE, plan_progress_hook)
    hooks.register(HookEvent.POST_TOOL_USE, post_tool_trace_hook)

    hooks.register(HookEvent.STOP, stop_report_hook)

    return hooks


def build_runtime() -> RuntimeBundle:
    registry = build_tool_registry()
    hooks = build_hooks()
    return RuntimeBundle(
        tool_registry=registry,
        executor=ToolExecutor(registry, hooks),
        context_manager=ContextManager(),
        hooks=hooks,
        recovery_policy=RecoveryPolicy(),
        progress_policy=ToolProgressPolicy(),
    )
