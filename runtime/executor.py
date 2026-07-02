from __future__ import annotations

from agent.messages import ToolCall
from runtime.hooks import HookManager
from runtime.permission import PermissionGate
from tools.base import ToolResult
from tools.registry import ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, permission_gate: PermissionGate, hooks: HookManager) -> None:
        self.registry = registry
        self.permission_gate = permission_gate
        self.hooks = hooks

    def execute(self, tool_call: ToolCall) -> ToolResult:
        tool = self.registry.get(tool_call.name)
        tool.validate(tool_call.arguments)
        self.permission_gate.check(tool_call)
        self.hooks.emit("PreToolUse", {"tool": tool_call.name, "arguments": tool_call.arguments})
        result = tool.call(tool_call.arguments)
        self.hooks.emit("PostToolUse", {"tool": tool_call.name, "ok": result.ok})
        return result

