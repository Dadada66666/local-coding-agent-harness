from __future__ import annotations


class SimpleSummarizer:
    def summarize(self, messages: list[dict]) -> str:
        rendered = str(messages)
        if len(rendered) <= 4000:
            return rendered
        return rendered[:4000] + "\n... compacted history truncated"


class ContextManager:
    KEEP_RECENT_MESSAGES = 8

    def __init__(self, summarizer=None) -> None:
        self.summarizer = summarizer or SimpleSummarizer()

    def prepare_context(self, context) -> None:
        size = len(str(context.messages))

        if size < context.config.compact_threshold_chars:
            return

        old_messages = context.messages[: -self.KEEP_RECENT_MESSAGES]
        recent_messages = context.messages[-self.KEEP_RECENT_MESSAGES :]
        orphan_tool_results, recent_messages = self._split_leading_orphan_tool_results(recent_messages)
        old_messages = [*old_messages, *orphan_tool_results]

        if not old_messages:
            return

        summary = self.summarizer.summarize(old_messages)

        context.messages = [
            {
                "role": "user",
                "content": (
                    "[Compacted history]\n"
                    f"{summary}\n\n"
                    "Continue from this state."
                ),
            },
            *recent_messages,
        ]

        context.trace.log(
            {
                "type": "context_compact",
                "old_message_count": len(old_messages),
                "recent_message_count": len(recent_messages),
                "compacted_orphan_tool_results": len(orphan_tool_results),
            }
        )

    def _split_leading_orphan_tool_results(self, messages: list[dict]) -> tuple[list[dict], list[dict]]:
        remaining = list(messages)
        orphan_tool_results = []
        while remaining and self._is_tool_result_message(remaining[0]):
            orphan_tool_results.append(remaining.pop(0))
        return orphan_tool_results, remaining

    def _is_tool_result_message(self, message: dict) -> bool:
        content = message.get("content")
        return (
            isinstance(content, list)
            and len(content) == 1
            and isinstance(content[0], dict)
            and content[0].get("type") == "tool_result"
        )
