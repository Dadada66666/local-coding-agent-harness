from __future__ import annotations

from tools.base import BaseTool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def schemas(self, context=None) -> list[dict]:
        return [
            tool.schema(context)
            for tool in self._tools.values()
            if self._is_available(tool, context)
        ]

    def names(self, context=None) -> list[str]:
        return [
            tool.name
            for tool in self._tools.values()
            if self._is_available(tool, context)
        ]

    def _is_available(self, tool: BaseTool, context) -> bool:
        if not tool.is_available(context):
            return False
        visibility = getattr(context, "is_tool_visible", None)
        return bool(visibility(tool.name)) if callable(visibility) else True
