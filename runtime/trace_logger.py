from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.messages import ModelResponse, ToolCall
from tools.base import ToolResult


class TraceLogger:
    def __init__(self, run_dir: Path) -> None:
        self.path = run_dir / "trace.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def event(self, event: str, payload: dict[str, Any]) -> None:
        self._write({"type": "event", "event": event, "payload": payload})

    def model_response(self, response: ModelResponse) -> None:
        self._write(
            {
                "type": "model_response",
                "message": response.message,
                "tool_calls": [tool_call.__dict__ for tool_call in response.tool_calls],
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "cost_usd": response.cost_usd,
            }
        )

    def tool_result(self, tool_call: ToolCall, result: ToolResult) -> None:
        self._write(
            {
                "type": "tool_result",
                "tool_call": tool_call.__dict__,
                "ok": result.ok,
                "content": result.content,
                "metadata": result.metadata,
            }
        )

    def _write(self, payload: dict[str, Any]) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), **payload}
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

