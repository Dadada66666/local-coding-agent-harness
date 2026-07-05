from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.access_policy import AccessPolicy
from runtime.permission_rules import PermissionRuleStore


def make_run_id() -> str:
    prefix = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{uuid4().hex[:8]}"


@dataclass(frozen=True)
class ReadFileSnapshot:
    mtime_ns: int
    size: int
    sha256: str
    partial: bool


@dataclass
class ToolBudget:
    read_file_calls: int = 0
    grep_calls: int = 0
    list_dir_calls: int = 0
    bash_calls: int = 0
    chars_returned: int = 0
    truncated_results: int = 0


@dataclass
class RunConfig:
    max_turns: int = 30
    max_repair_attempts: int = 3
    max_tool_result_chars: int = 8000
    grep_max_matches: int = 50
    compact_threshold_chars: int = 120000
    permission_mode: str = "manual_approval"
    sandbox_enabled: bool = False
    sandbox_auto_allow_bash: bool = True
    sandbox_fail_if_unavailable: bool = False
    sandbox_settings_path: str | None = None


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
    sandbox: Any | None = None

    conversation_messages: list[dict] = field(default_factory=list)
    finished: bool = False
    success: bool = False
    final_text: str = ""
    abort_reason: str | None = None
    stop_recorded: bool = False
    turn_count: int = 0
    current_turn_id: int = 0
    repair_attempts: int = 0
    last_test_result: dict | None = None
    changed_files: set[str] = field(default_factory=set)
    approved_permission_scopes: set[str] = field(default_factory=set)
    denied_permission_scopes: set[str] = field(default_factory=set)
    access_policy: AccessPolicy = field(default_factory=AccessPolicy)
    permission_rules: PermissionRuleStore = field(default_factory=PermissionRuleStore)
    read_file_state: dict[str, ReadFileSnapshot] = field(default_factory=dict)
    tool_budget: ToolBudget = field(default_factory=ToolBudget)
    sandbox_auto_allowed_unknown_bash_count: int = 0

    def add_user_message(self, message: dict) -> None:
        self.messages.append(message)
        self.conversation_messages.append(message)

    def safe_path(self, path: str) -> Path:
        resolved = (self.repo_path / path).resolve()
        if not resolved.is_relative_to(self.repo_path.resolve()):
            raise ValueError(f"Path escapes WORKDIR: {path}")
        return resolved

    def add_assistant_message(self, message: dict) -> None:
        self.messages.append(message)
        self.conversation_messages.append(message)

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
