from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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

    @abstractmethod
    def call(self, args: dict, context) -> ToolResult:
        raise NotImplementedError

    def format_result(self, result: ToolResult) -> str:
        if result.ok:
            return result.content
        return f"Error: {result.error or result.content}"

