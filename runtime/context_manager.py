from __future__ import annotations


class ContextManager:
    def __init__(self, max_messages: int = 40) -> None:
        self.max_messages = max_messages

    def prepare_context(self, messages: list[dict]) -> list[dict]:
        if len(messages) <= self.max_messages:
            return messages
        system = messages[:1]
        recent = messages[-self.max_messages + 1 :]
        return system + recent

