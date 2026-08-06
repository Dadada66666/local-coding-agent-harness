from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any


class HookEvent:
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    STOP = "Stop"


class HookManager:
    """
    Lightweight synchronous hook manager.

    Rules:
    - Hook returns None: continue.
    - Hook returns a value: stop current hook chain and return that value.
    - PreToolUse can return ToolResult to block tool execution.
    - PostToolUse should mutate ToolResult in place and return None.
    - Stop hooks should not affect final task result.
    """

    def __init__(self) -> None:
        self._hooks: dict[str, list[Callable[..., Any]]] = defaultdict(list)

    def register(self, event: str, fn: Callable[..., Any]) -> None:
        self._hooks[event].append(fn)

    def trigger(self, event: str, *args, **kwargs) -> Any | None:
        for fn in self._hooks.get(event, []):
            result = fn(*args, **kwargs)
            if result is not None:
                return result
        return None

    def trigger_all(self, event: str, *args, **kwargs) -> None:
        for fn in self._hooks.get(event, []):
            fn(*args, **kwargs)
