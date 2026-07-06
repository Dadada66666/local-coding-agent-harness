from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReadableTraceWriter:
    """Write a developer-friendly conversation view without affecting runtime flow."""

    def write(self, context) -> Path:
        path = context.run_dir / "readable_trace.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        events = self._read_trace_events(context)
        tool_outcomes = self._tool_outcomes(events)
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
            rendered = self._render_message(message, visible_index, tool_outcomes)
            if not rendered:
                continue
            lines.extend(rendered)
            visible_index += 1

        if visible_index == 1:
            lines.extend(["No user/model messages recorded.", ""])

        notes = self._runtime_notes(events)
        if notes:
            lines.extend(["## Runtime Notes", "", *notes, ""])

        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _render_message(
        self,
        message: dict[str, Any],
        index: int,
        tool_outcomes: dict[str, dict[str, Any]],
    ) -> list[str]:
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
            return self._render_assistant(content, index, tool_outcomes)

        return []

    def _user_text(self, content) -> str | None:
        if not isinstance(content, str):
            return None
        if content.startswith("[Compacted history]"):
            return None
        if content.startswith("The previous test run failed."):
            return None
        return content

    def _render_assistant(
        self,
        content,
        index: int,
        tool_outcomes: dict[str, dict[str, Any]],
    ) -> list[str]:
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
                outcome = tool_outcomes.get(str(block.get("id") or ""))
                if outcome:
                    lines.extend(self._render_tool_outcome(outcome))
                wrote_block = True

        if not wrote_block:
            lines.extend(["```json", self._json(content), "```", ""])
        return lines

    def _json(self, value) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    def _read_trace_events(self, context) -> list[dict[str, Any]]:
        trace = getattr(context, "trace", None)
        path = getattr(trace, "path", None)
        if path is None or not Path(path).exists():
            return []

        events = []
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def _tool_outcomes(self, events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        outcomes: dict[str, dict[str, Any]] = {}
        for event in events:
            tool_call_id = event.get("tool_call_id")
            if not tool_call_id:
                continue

            outcome = outcomes.setdefault(str(tool_call_id), {})
            event_type = event.get("type")
            if event_type == "permission_decision":
                outcome["permission"] = event
            elif event_type == "tool_result":
                outcome["result"] = event
            elif event_type == "task_cancelled":
                outcome["cancelled"] = event
        return outcomes

    def _render_tool_outcome(self, outcome: dict[str, Any]) -> list[str]:
        lines = []
        permission = outcome.get("permission") or {}
        result = outcome.get("result") or {}
        cancelled = outcome.get("cancelled") or {}

        if permission:
            lines.append(
                "- permission: "
                f"{permission.get('behavior', 'unknown')} "
                f"{permission.get('risk', 'unknown')}"
            )

        if result:
            status = "ok" if result.get("ok") else "failed"
            message = result.get("error") or result.get("output_preview") or ""
            lines.append(f"- result: {status}{self._suffix(message)}")

        if cancelled:
            decision = cancelled.get("decision") or {}
            message = decision.get("message") or ""
            lines.append(f"- task_cancelled{self._suffix(message)}")

        if not lines:
            return []
        return [*lines, ""]

    def _runtime_notes(self, events: list[dict[str, Any]]) -> list[str]:
        notes = []
        for event in events:
            event_type = event.get("type")
            if event_type == "test_result":
                status = "passed" if event.get("ok") else "failed"
                command = event.get("command") or ""
                notes.append(f"- verification {status}: `{command}`")
            elif event_type == "task_cancelled":
                decision = event.get("decision") or {}
                reason = decision.get("risk") or "unknown"
                message = decision.get("message") or ""
                notes.append(f"- task cancelled: {reason}{self._suffix(message)}")
            elif event_type == "stop_artifact_error":
                notes.append(
                    f"- artifact error: {event.get('artifact', 'unknown')}"
                    f"{self._suffix(event.get('error') or '')}"
                )
        return notes

    def _suffix(self, text: str) -> str:
        if not text:
            return ""
        normalized = " ".join(str(text).split())
        if len(normalized) > 160:
            normalized = f"{normalized[:160]}..."
        return f" - {normalized}"
