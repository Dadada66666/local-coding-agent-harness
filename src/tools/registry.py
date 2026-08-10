from __future__ import annotations

from dataclasses import dataclass

from tools.base import BaseTool


@dataclass(frozen=True, slots=True)
class ToolResolution:
    tool: BaseTool | None
    known: bool
    available: bool
    reason: str | None = None


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def all_names(self) -> list[str]:
        return list(self._tools)

    def resolve(self, name: str, context=None) -> ToolResolution:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResolution(None, known=False, available=False, reason="unknown_tool")
        if not tool.is_available(context):
            return ToolResolution(
                tool,
                known=True,
                available=False,
                reason="tool_declared_unavailable",
            )
        visibility = getattr(context, "is_tool_visible", None)
        if callable(visibility) and not visibility(tool.name):
            return ToolResolution(
                tool,
                known=True,
                available=False,
                reason="hidden_by_runtime_state",
            )
        return ToolResolution(tool, known=True, available=True)

    def schemas(self, context=None) -> list[dict]:
        return [
            tool.schema(context)
            for tool in self._tools.values()
            if self.resolve(tool.name, context).available
        ]

    def names(self, context=None) -> list[str]:
        return [
            tool.name for tool in self._tools.values() if self.resolve(tool.name, context).available
        ]
