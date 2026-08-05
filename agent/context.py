from __future__ import annotations

import hashlib
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
    max_tool_round_tokens: int = 6000
    grep_max_matches: int = 50
    compact_threshold_chars: int = 120000
    context_window_tokens: int | None = None
    context_target_tokens: int | None = 32000
    context_soft_limit_ratio: float = 0.8
    context_safety_margin_tokens: int = 4096
    context_recent_target_tokens: int = 8000
    context_recent_max_tokens: int = 16000
    context_min_recent_rounds: int = 2
    context_checkpoint_max_chars: int = 6000
    max_context_recovery_attempts: int = 1
    max_context_compaction_failures: int = 3
    artifact_read_max_chars: int = 6000
    permission_mode: str = "manual_approval"
    sandbox_enabled: bool = False
    sandbox_auto_allow_bash: bool = True
    sandbox_fail_if_unavailable: bool = False
    sandbox_settings_path: str | None = None
    bash_env_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.max_turns <= 0:
            raise ValueError("max_turns must be > 0")
        if self.max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be >= 0")
        if self.max_tool_result_chars < 256:
            raise ValueError("max_tool_result_chars must be >= 256")
        if self.max_tool_round_tokens < 0:
            raise ValueError("max_tool_round_tokens must be >= 0")
        if self.grep_max_matches <= 0:
            raise ValueError("grep_max_matches must be > 0")
        if self.compact_threshold_chars <= 0:
            raise ValueError("compact_threshold_chars must be > 0")
        if self.context_window_tokens is not None and self.context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be > 0")
        if self.context_target_tokens is not None and self.context_target_tokens <= 0:
            raise ValueError("context_target_tokens must be > 0")
        if not 0 < self.context_soft_limit_ratio <= 1:
            raise ValueError("context_soft_limit_ratio must be between 0 and 1")
        if self.context_safety_margin_tokens < 0:
            raise ValueError("context_safety_margin_tokens must be >= 0")
        if self.context_recent_target_tokens <= 0:
            raise ValueError("context_recent_target_tokens must be > 0")
        if self.context_recent_max_tokens < self.context_recent_target_tokens:
            raise ValueError(
                "context_recent_max_tokens must be >= context_recent_target_tokens"
            )
        if self.context_min_recent_rounds <= 0:
            raise ValueError("context_min_recent_rounds must be > 0")
        if self.context_checkpoint_max_chars < 512:
            raise ValueError("context_checkpoint_max_chars must be >= 512")
        if self.max_context_recovery_attempts < 0:
            raise ValueError("max_context_recovery_attempts must be >= 0")
        if self.max_context_compaction_failures <= 0:
            raise ValueError("max_context_compaction_failures must be > 0")
        if self.artifact_read_max_chars <= 0:
            raise ValueError("artifact_read_max_chars must be > 0")


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
    environment_policy: Any | None = None
    redactor: Any | None = None

    conversation_messages: list[dict] = field(default_factory=list)
    finished: bool = False
    success: bool = False
    final_text: str = ""
    abort_reason: str | None = None
    stop_recorded: bool = False
    turn_count: int = 0
    current_turn_id: int = 0
    task_model_calls: int = 0
    task_tool_rounds: int = 0
    repair_attempts: int = 0
    last_test_result: dict | None = None
    task_test_result: dict | None = None
    mutation_version: int = 0
    task_start_mutation_version: int = 0
    task_verification_version: int | None = None
    task_unresolved_mutation_failure: bool = False
    changed_files: set[str] = field(default_factory=set)
    task_changed_files: set[str] = field(default_factory=set)
    created_files: set[str] = field(default_factory=set)
    task_created_files: set[str] = field(default_factory=set)
    approved_permission_scopes: set[str] = field(default_factory=set)
    denied_permission_scopes: set[str] = field(default_factory=set)
    access_policy: AccessPolicy = field(default_factory=AccessPolicy)
    permission_rules: PermissionRuleStore = field(default_factory=PermissionRuleStore)
    read_file_state: dict[str, ReadFileSnapshot] = field(default_factory=dict)
    tool_budget: ToolBudget = field(default_factory=ToolBudget)
    sandbox_auto_allowed_unknown_bash_count: int = 0
    context_generation: int = 0
    context_compactions: int = 0
    context_compaction_failures: int = 0
    context_recovery_attempts: int = 0
    last_model_usage: Any | None = None
    last_model_usage_message_index: int | None = None
    last_model_usage_generation: int | None = None
    last_model_consumed_message_count: int = 0
    tool_result_artifacts: dict[str, str] = field(default_factory=dict)
    completed_tasks: list[dict[str, Any]] = field(default_factory=list)

    def add_user_message(self, message: dict) -> None:
        self.messages.append(message)
        self.conversation_messages.append(message)

    def safe_path(self, path: str) -> Path:
        resolved = (self.repo_path / path).resolve()
        if not resolved.is_relative_to(self.repo_path.resolve()):
            raise ValueError(f"Path escapes WORKDIR: {path}")
        return resolved

    def record_file_snapshot(self, target: Path, raw: bytes, *, partial: bool) -> ReadFileSnapshot:
        stat = target.stat()
        snapshot = ReadFileSnapshot(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            sha256=hashlib.sha256(raw).hexdigest(),
            partial=partial,
        )
        self.read_file_state[str(target)] = snapshot
        return snapshot

    def record_changed_file(self, path: str) -> None:
        self.changed_files.add(path)
        self.task_changed_files.add(path)
        self.record_mutation()

    def record_created_file(self, path: str) -> None:
        self.created_files.add(path)
        self.task_created_files.add(path)
        self.record_changed_file(path)

    def record_deleted_file(self, path: str) -> None:
        self.read_file_state.pop(str(self.repo_path / path), None)

        if path in self.task_created_files:
            self.task_created_files.discard(path)
            self.task_changed_files.discard(path)
            self.created_files.discard(path)
            self.changed_files.discard(path)
            return

        if path in self.created_files:
            self.created_files.discard(path)
            self.changed_files.discard(path)
        else:
            self.changed_files.add(path)
        self.task_changed_files.add(path)
        self.record_mutation()

    def record_mutation(self) -> None:
        self.mutation_version += 1

    def has_task_mutations(self) -> bool:
        return self.mutation_version != self.task_start_mutation_version

    def reset_task_state(self) -> None:
        self.task_test_result = None
        self.task_verification_version = None
        self.task_unresolved_mutation_failure = False
        self.task_start_mutation_version = self.mutation_version
        self.task_model_calls = 0
        self.task_tool_rounds = 0
        self.repair_attempts = 0
        self.context_recovery_attempts = 0
        self.context_compaction_failures = 0
        self.task_changed_files.clear()
        self.task_created_files.clear()

    def add_assistant_message(self, message: dict) -> None:
        self.messages.append(message)
        self.conversation_messages.append(message)

    def add_tool_result(self, tool_call_id: str, content: str) -> None:
        self.add_tool_results([(tool_call_id, content, False)])

    def add_tool_results(self, results: list[tuple[str, str, bool]]) -> None:
        if not results:
            return
        message = {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_call_id,
                    "content": content,
                    **({"is_error": True} if is_error else {}),
                }
                for tool_call_id, content, is_error in results
            ],
        }
        self.messages.append(message)
        self.conversation_messages.append(message)

    def add_runtime_message(self, message: dict) -> None:
        self.messages.append(message)
        self.conversation_messages.append(message)

    def mark_context_changed(self) -> None:
        self.context_generation += 1
        self.last_model_usage = None
        self.last_model_usage_message_index = None
        self.last_model_usage_generation = None

    def mark_model_request_consumed(self, message_count: int) -> None:
        self.last_model_consumed_message_count = max(message_count, 0)

    def record_model_usage(self, usage: Any, response_message_index: int) -> None:
        self.last_model_usage = usage
        self.last_model_usage_message_index = response_message_index
        self.last_model_usage_generation = self.context_generation

    def capture_completed_task(self) -> None:
        if self.task_model_calls <= 0:
            return

        test_result = self.task_test_result or {}
        summary = {
            "task": self.task,
            "result": (self.final_text or "")[:2000],
            "changed_files": sorted(self.task_changed_files),
            "verification": {
                "command": test_result.get("command"),
                "ok": test_result.get("ok"),
                "current": (
                    self.task_verification_version == self.mutation_version
                    if self.task_verification_version is not None
                    else None
                ),
            },
        }
        self.completed_tasks.append(summary)
        del self.completed_tasks[:-5]
