from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def make_run_id() -> str:
    prefix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{uuid4().hex[:8]}"


@dataclass
class RunConfig:
    max_turns: int = 30
    max_repair_attempts: int = 3
    max_tool_result_chars: int = 8000
    compact_threshold_chars: int = 120000
    permission_mode: str = "manual_approval"


@dataclass
class AgentContext:
    run_id: str
    task: str
    repo_path: Path
    run_dir: Path
    messages: list[dict]
    system_prompt: str
    config: RunConfig

    permission_mode: str
    permission_gate: Any
    trace: Any
    artifacts: Any
    cost_tracker: Any
    diff_manager: Any
    report_writer: Any

    finished: bool = False
    success: bool = False
    final_text: str = ""
    turn_count: int = 0
    repair_attempts: int = 0
    last_test_result: dict | None = None
    changed_files: set[str] = field(default_factory=set)

    def safe_path(self, path: str) -> Path:
        resolved = (self.repo_path / path).resolve()
        if not resolved.is_relative_to(self.repo_path.resolve()):
            raise ValueError(f"Path escapes repository: {path}")
        return resolved

    def add_assistant_message(self, message: dict) -> None:
        self.messages.append(message)

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_call_id,
                        "content": content,
                    }
                ],
            }
        )

