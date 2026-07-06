from __future__ import annotations

from types import SimpleNamespace

from agent.context import RunConfig
from runtime.context_manager import ContextManager


class DummyTrace:
    def __init__(self) -> None:
        self.events = []

    def log(self, event: dict) -> None:
        self.events.append(event)


def tool_use_message(call_id: str) -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": call_id, "name": "read_file", "input": {"path": "demo.py"}}],
    }


def tool_result_message(call_id: str) -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": call_id, "content": "result"}],
    }


def test_context_compaction_drops_leading_orphan_tool_result() -> None:
    messages = [{"role": "user", "content": "start"}]
    for index in range(6):
        call_id = f"call_{index}"
        messages.append(tool_use_message(call_id))
        messages.append(tool_result_message(call_id))
    messages.append({"role": "assistant", "content": [{"type": "text", "text": "done"}]})

    context = SimpleNamespace(
        messages=messages,
        config=RunConfig(compact_threshold_chars=1),
        trace=DummyTrace(),
    )

    ContextManager().prepare_context(context)

    assert context.messages[0]["content"].startswith("[Compacted history]")
    assert not _is_tool_result_message(context.messages[1])
    assert any(event["type"] == "context_compact" for event in context.trace.events)


def _is_tool_result_message(message: dict) -> bool:
    content = message.get("content")
    return (
        isinstance(content, list)
        and len(content) == 1
        and isinstance(content[0], dict)
        and content[0].get("type") == "tool_result"
    )
