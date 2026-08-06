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
