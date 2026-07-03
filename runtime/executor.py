from __future__ import annotations

from runtime.hooks import HookEvent
from tools.base import ToolResult


class ToolExecutor:
    def __init__(self, registry, hooks) -> None:
        self.registry = registry
        self.hooks = hooks

    def execute(self, tool_call, context) -> ToolResult:
        tool = self.registry.get(tool_call.name)

        if not tool:
            return ToolResult(
                ok=False,
                content=f"Unknown tool: {tool_call.name}",
                error=f"Unknown tool: {tool_call.name}",
                metadata={"unknown_tool": True},
            )

        try:
            tool.validate(tool_call.arguments, context)
        except Exception as exc:
            return ToolResult(
                ok=False,
                content=f"Invalid tool arguments: {exc}",
                error=str(exc),
                metadata={"validation_error": True},
            )

        blocked = self.hooks.trigger(
            HookEvent.PRE_TOOL_USE,
            tool_call=tool_call,
            tool=tool,
            context=context,
        )

        if blocked is not None:
            result = self._blocked_result(blocked)
            self._trigger_post_tool_use(tool_call, tool, result, context)
            return result

        try:
            result = tool.call(tool_call.arguments, context)
        except Exception as exc:
            result = ToolResult(
                ok=False,
                content=f"Tool error: {exc}",
                error=str(exc),
                metadata={"tool_exception": True},
            )

        self._trigger_post_tool_use(tool_call, tool, result, context)
        return result

    def _blocked_result(self, blocked) -> ToolResult:
        if isinstance(blocked, ToolResult):
            return blocked

        reason = str(blocked)
        return ToolResult(
            ok=False,
            content=reason,
            error=reason,
            metadata={"blocked_by_hook": True},
        )

    def _trigger_post_tool_use(self, tool_call, tool, result: ToolResult, context) -> None:
        self.hooks.trigger_all(
            HookEvent.POST_TOOL_USE,
            tool_call=tool_call,
            tool=tool,
            result=result,
            context=context,
        )