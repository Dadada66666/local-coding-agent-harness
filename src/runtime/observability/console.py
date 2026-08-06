from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text


CONSOLE = Console(highlight=False)


def print_tool_call(name: str, arguments: dict[str, Any]) -> None:
    line = Text()
    line.append("[tool]", style="bold yellow")
    line.append(f" {name} {arguments}")
    CONSOLE.print(line)


def print_tool_validation_failure(name: str, error: str) -> None:
    line = Text()
    line.append("[tool]", style="bold yellow")
    line.append(f" {name} rejected: ")
    line.append(error, style="red")
    CONSOLE.print(line)


def print_model_call_start(call_number: int, max_calls: int) -> None:
    line = Text()
    line.append("[model]", style="bold cyan")
    line.append(f" call {call_number}/{max_calls} waiting for response...")
    CONSOLE.print(line)
