from __future__ import annotations

from types import SimpleNamespace

from runtime.hooks import HookEvent
from tools.base import ToolResult


class ToolExecutor:
    def __init__(self, registry, hooks) -> None:
        self.registry = registry
        self.hooks = hooks

    def execute(self, tool_call, context) -> ToolResult:
        resolution = self.registry.resolve(tool_call.name, context)
        tool = resolution.tool
        gate_tool = tool or self._unknown_tool(tool_call)

        if not resolution.known:
            result = ToolResult(
                ok=False,
                content=f"Unknown tool: {tool_call.name}",
                error=f"Unknown tool: {tool_call.name}",
                metadata={"unknown_tool": True},
            )
            self._trigger_post_tool_use(tool_call, gate_tool, result, context)
            return result

        if not resolution.available:
            message = f"Tool is unavailable in the current runtime state: {tool_call.name}"
            plan_state = getattr(context, "plan_state", None)
            result = ToolResult(
                ok=False,
                content=message,
                error=message,
                metadata={
                    "known_tool": True,
                    "unavailable_tool": True,
                    "blocked_by": "tool_capability",
                    "capability_reason": resolution.reason,
                    "track_mutation_failure": False,
                    "model_contract_violation": True,
                    **(
                        {
                            "plan_policy": plan_state.policy.value,
                            "execution_path": plan_state.execution_path.value,
                            "plan_phase": plan_state.phase.value,
                        }
                        if plan_state is not None
                        else {}
                    ),
                },
            )
            self._trigger_post_tool_use(tool_call, tool, result, context)
            return result

        blocked = self.hooks.trigger(
            HookEvent.PRE_TOOL_VALIDATE,
            tool_call=tool_call,
            tool=tool,
            context=context,
        )
        if blocked is not None:
            result = self._blocked_result(blocked)
            self._trigger_post_tool_use(tool_call, tool, result, context)
            return result

        try:
            tool.validate(tool_call.arguments, context)
        except Exception as exc:
            result = ToolResult(
                ok=False,
                content=f"Invalid tool arguments: {exc}",
                error=str(exc),
                metadata={"validation_error": True, "track_mutation_failure": False},
            )
            self._trigger_post_tool_use(tool_call, tool, result, context)
            return result

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

    def _unknown_tool(self, tool_call):
        return SimpleNamespace(
            name=tool_call.name,
            read_only=False,
            dangerous=True,
        )
