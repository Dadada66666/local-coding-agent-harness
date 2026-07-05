from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from runtime.operation import Operation

if TYPE_CHECKING:
    from runtime.permission import PermissionDecision


@dataclass
class ToolResult:
    ok: bool
    content: str
    error: str | None = None
    artifact_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ToolValidationError(Exception):
    pass


class BaseTool(ABC):
    name: str
    description: str
    input_schema: dict

    read_only: bool = False
    dangerous: bool = False
    concurrency_safe: bool = False

    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }

    def validate(self, args: dict, context) -> None:
        return None

    def classify_operation(self, args: dict, context) -> Operation:
        return Operation(
            kind="tool",
            action=self.name,
            subject=self.name,
            scope_key=f"tool:{self.name}",
            is_read_only=self.read_only,
            is_destructive=self.dangerous,
        )

    def check_permissions(
        self,
        args: dict,
        context,
        operation: Operation,
    ) -> PermissionDecision | None:
        return None

    @abstractmethod
    def call(self, args: dict, context) -> ToolResult:
        raise NotImplementedError

    def format_result(self, result: ToolResult) -> str:
        if result.ok:
            return result.content
        return f"Error: {result.error or result.content}"
