from __future__ import annotations

from dataclasses import dataclass

from runtime.context_manager import ContextManager
from runtime.default_hooks import (
    large_output_hook,
    permission_hook,
    post_tool_trace_hook,
    pre_tool_trace_hook,
    stop_report_hook,
    test_result_hook,
    user_prompt_submit_hook,
)
from runtime.executor import ToolExecutor
from runtime.hooks import HookEvent, HookManager
from runtime.recovery import RecoveryPolicy
from tools.bash import BashTool
from tools.edit_file import EditFileTool
from tools.grep import GrepTool
from tools.list_dir import ListDirTool
from tools.read_file import ReadFileTool
from tools.registry import ToolRegistry
from tools.view_diff import ViewDiffTool


@dataclass
class RuntimeBundle:
    tool_registry: ToolRegistry
    executor: ToolExecutor
    context_manager: ContextManager
    hooks: HookManager
    recovery_policy: RecoveryPolicy


def build_tool_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ListDirTool())
    registry.register(GrepTool())
    registry.register(ReadFileTool())
    registry.register(EditFileTool())
    registry.register(BashTool())
    registry.register(ViewDiffTool())
    return registry


def build_hooks() -> HookManager:
    hooks = HookManager()

    hooks.register(HookEvent.USER_PROMPT_SUBMIT, user_prompt_submit_hook)

    hooks.register(HookEvent.PRE_TOOL_USE, pre_tool_trace_hook)
    hooks.register(HookEvent.PRE_TOOL_USE, permission_hook)

    hooks.register(HookEvent.POST_TOOL_USE, large_output_hook)
    hooks.register(HookEvent.POST_TOOL_USE, test_result_hook)
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
    )

