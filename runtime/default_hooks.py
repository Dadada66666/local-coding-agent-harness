from __future__ import annotations

from runtime.hooks import HookManager
from runtime.trace_logger import TraceLogger


def install_default_hooks(hooks: HookManager, trace_logger: TraceLogger) -> None:
    hooks.register("UserPromptSubmit", lambda event, payload: trace_logger.event(event, payload))
    hooks.register("PreToolUse", lambda event, payload: trace_logger.event(event, payload))
    hooks.register("PostToolUse", lambda event, payload: trace_logger.event(event, payload))
    hooks.register("Stop", lambda event, payload: trace_logger.event(event, payload))

