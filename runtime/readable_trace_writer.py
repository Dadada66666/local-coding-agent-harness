from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReadableTraceWriter:
    """Write a developer-friendly conversation view without affecting runtime flow."""

    def write(self, context) -> Path:
        path = context.run_dir / "readable_trace.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Readable Trace",
            "",
            f"Run: `{context.run_id}`",
            f"Task: {context.task}",
            "",
        ]

        messages = getattr(context, "conversation_messages", None) or context.messages
        visible_index = 1
        for message in messages:
            rendered = self._render_message(message, visible_index)
            if not rendered:
                continue
            lines.extend(rendered)
            visible_index += 1

        if visible_index == 1:
            lines.extend(["No user/model messages recorded.", ""])

        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _render_message(self, message: dict[str, Any], index: int) -> list[str]:
        role = message.get("role")
        content = message.get("content")

        if role == "user":
            text = self._user_text(content)
            if text is None:
                return []
            return [
                f"## {index}. User",
                "",
                "```text",
                text,
                "```",
                "",
            ]

        if role == "assistant":
            return self._render_assistant(content, index)

        return []

    def _user_text(self, content) -> str | None:
        if not isinstance(content, str):
            return None
        if content.startswith("[Compacted history]"):
            return None
        if content.startswith("The previous test run failed."):
            return None
        return content

    def _render_assistant(self, content, index: int) -> list[str]:
        lines = [f"## {index}. Assistant", ""]

        if isinstance(content, str):
            lines.extend(["```text", content, "```", ""])
            return lines

        if not isinstance(content, list):
            lines.extend(["```text", str(content), "```", ""])
            return lines

        wrote_block = False
        for block in content:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text = str(block.get("text", ""))
                if text:
                    lines.extend(["```text", text, "```", ""])
                    wrote_block = True
            elif block_type == "tool_use":
                lines.extend(
                    [
                        f"- tool_use `{block.get('name', 'unknown')}`",
                        "",
                        "```json",
                        self._json(block.get("input") or {}),
                        "```",
                        "",
                    ]
                )
                wrote_block = True

        if not wrote_block:
            lines.extend(["```json", self._json(content), "```", ""])
        return lines

    def _json(self, value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)
