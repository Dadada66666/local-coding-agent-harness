from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from runtime.config import RunConfig
from runtime.context.source_state import (
    SourceReadMetrics,
    SourceReadState,
    merge_ranges,
    overlap_length,
)
from runtime.plan import ExecutionPath, PlanPhase
from runtime.plan.capabilities import context_tool_is_visible
from runtime.security.access_policy import AccessPolicy
from runtime.security.permission_rules import PermissionRuleStore
from runtime.task import (
    TaskStatus,
    TaskTransitionError,
    is_terminal_task_status,
    validate_task_transition,
)


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
    plan_state: Any
    plan_controller: Any
    plan_store: Any
    plan_gate: Any
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
    task_sequence: int = 0
    task_id: str | None = None
    task_status: TaskStatus = TaskStatus.IDLE
    task_waiting_reason: str | None = None
    task_archived: bool = False
    user_continuation_sequence: int = 0
    pending_user_continuation_id: int | None = None
    pending_user_continuation: str | None = None
    task_cost_start: dict[str, int] = field(default_factory=dict)
    task_tool_failures: list[dict[str, Any]] = field(default_factory=list)
    task_failure_fingerprint: str | None = None
    task_failure_repeat_count: int = 0
    task_saturated_invalid_calls: int = 0
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
    read_file_segments: dict[str, SourceReadState] = field(default_factory=dict)
    source_read_metrics: SourceReadMetrics = field(default_factory=SourceReadMetrics)
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
    tool_result_provenance: dict[str, str] = field(default_factory=dict)
    tool_result_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    eager_projection_active: bool = False
    completed_tasks: list[dict[str, Any]] = field(default_factory=list)

    def add_user_message(self, message: dict) -> None:
        self.messages.append(message)
        self.conversation_messages.append(message)

    def safe_path(self, path: str) -> Path:
        resolved = (self.repo_path / path).resolve()
        if not resolved.is_relative_to(self.repo_path.resolve()):
            raise ValueError(f"Path escapes WORKDIR: {path}")
        return resolved

    def record_file_snapshot(
        self,
        target: Path,
        raw: bytes,
        *,
        partial: bool,
        reuse_hash: bool = False,
    ) -> ReadFileSnapshot:
        stat = target.stat()
        key = str(target)
        previous = self.read_file_state.get(key)
        sha256 = (
            previous.sha256
            if reuse_hash
            and previous is not None
            and previous.mtime_ns == stat.st_mtime_ns
            and previous.size == stat.st_size
            else hashlib.sha256(raw).hexdigest()
        )
        snapshot = ReadFileSnapshot(
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            sha256=sha256,
            partial=partial,
        )
        if previous is not None and previous.sha256 != snapshot.sha256:
            self.read_file_segments.pop(key, None)
        self.read_file_state[key] = snapshot
        return snapshot

    def source_read_state(
        self,
        target: Path,
        *,
        requested_path: str,
        sha256: str,
        total_lines: int,
    ) -> SourceReadState:
        key = str(target)
        state = self.read_file_segments.get(key)
        if (
            state is None
            or state.sha256 != sha256
            or state.total_lines != total_lines
        ):
            try:
                source_path = target.relative_to(self.repo_path).as_posix()
            except ValueError:
                source_path = requested_path
            state = SourceReadState(
                source_path=source_path,
                sha256=sha256,
                total_lines=total_lines,
            )
            self.read_file_segments[key] = state
        return state

    def is_tool_visible(self, tool_name: str) -> bool:
        return context_tool_is_visible(self, tool_name)

    def record_source_observation(self, tool_call_id: str, metadata: dict[str, Any]) -> None:
        if metadata.get("projection_kind") != "source_slice":
            return
        state = self.read_file_segments.get(str(metadata.get("resolved_path") or ""))
        if state is None or state.sha256 != metadata.get("source_sha256"):
            return
        identifier = str(tool_call_id)
        if identifier not in state.observation_ids:
            state.observation_ids.append(identifier)

    def source_resident_overlap(
        self,
        state: SourceReadState,
        start: int,
        end: int,
    ) -> int:
        observation_ids = set(state.observation_ids)
        projected_ids = state.projected_observation_ids
        resident_ids: set[str] = set()
        for message in self.messages:
            content = message.get("content")
            if message.get("role") != "user" or not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict) or block.get("type") != "tool_result":
                    continue
                identifier = str(block.get("tool_use_id", ""))
                if identifier in observation_ids and identifier not in projected_ids:
                    resident_ids.add(identifier)

        ranges: list[tuple[int, int]] = []
        for identifier in resident_ids:
            metadata = self.tool_result_metadata.get(identifier, {})
            if (
                metadata.get("projection_kind") != "source_slice"
                or metadata.get("source_sha256") != state.sha256
                or metadata.get("source_path") != state.source_path
            ):
                continue
            line_start = metadata.get("returned_line_start")
            line_end = metadata.get("returned_line_end")
            if not isinstance(line_start, int) or not isinstance(line_end, int):
                continue
            ranges = merge_ranges(ranges, (max(line_start - 1, 0), line_end))
        return overlap_length(ranges, start, end)

    def mark_source_observation_projected(
        self,
        tool_call_id: str,
        metadata: dict[str, Any],
    ) -> None:
        if metadata.get("projection_kind") != "source_slice":
            return
        state = self.read_file_segments.get(str(metadata.get("resolved_path") or ""))
        identifier = str(tool_call_id)
        if state is not None:
            state.projected_observation_ids.add(identifier)
        self.source_read_metrics.source_observations_projected += 1

    def source_context_manifest(self, *, limit: int = 12) -> list[dict[str, Any]]:
        states = sorted(
            self.read_file_segments.values(),
            key=lambda state: state.last_read_turn or -1,
        )[-limit:]
        return [
            {
                "path": state.source_path,
                "sha": state.sha256[:16],
                "total_lines": state.total_lines,
                "fully_scanned": state.fully_scanned,
                "covered_ranges": state.covered_ranges[:8],
                "ranges_omitted": max(len(state.covered_ranges) - 8, 0),
            }
            for state in states
        ]

    def source_efficiency_snapshot(self) -> dict[str, Any]:
        metrics = self.source_read_metrics
        returned = (
            metrics.unique_source_lines_returned
            + metrics.duplicate_source_lines_returned
        )
        non_rehydration_overlap = max(
            metrics.duplicate_source_lines_returned
            - metrics.rehydrated_source_lines,
            0,
        )
        return {
            "read_file_calls": metrics.read_file_calls,
            "unique_files_read": len(metrics.files_read),
            "unique_source_lines_returned": metrics.unique_source_lines_returned,
            "duplicate_source_lines_returned": metrics.duplicate_source_lines_returned,
            "rehydration_reads": metrics.rehydration_reads,
            "rehydrated_source_lines": metrics.rehydrated_source_lines,
            "non_rehydration_overlap_lines": non_rehydration_overlap,
            "overlap_ratio": round(
                metrics.duplicate_source_lines_returned / returned,
                4,
            )
            if returned
            else 0.0,
            "files_fully_scanned": len(metrics.fully_scanned_files),
            "high_overlap_rereads": metrics.high_overlap_rereads,
            "redundant_reads_avoided": metrics.redundant_reads_avoided,
            "source_observations_projected": metrics.source_observations_projected,
        }

    def record_changed_file(self, path: str) -> None:
        self.read_file_segments.pop(str((self.repo_path / path).resolve()), None)
        self.changed_files.add(path)
        self.task_changed_files.add(path)
        self.record_mutation()

    def record_created_file(self, path: str) -> None:
        self.created_files.add(path)
        self.task_created_files.add(path)
        self.record_changed_file(path)

    def record_deleted_file(self, path: str) -> None:
        resolved = str((self.repo_path / path).resolve())
        self.read_file_state.pop(resolved, None)
        self.read_file_segments.pop(resolved, None)

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
        self.task_failure_fingerprint = None
        self.task_failure_repeat_count = 0
        self.task_saturated_invalid_calls = 0
        self.repair_attempts = 0
        self.context_recovery_attempts = 0
        self.context_compaction_failures = 0
        snapshot = getattr(self.cost_tracker, "snapshot", None)
        self.task_cost_start = snapshot() if callable(snapshot) else {}
        self.task_tool_failures.clear()
        self.task_changed_files.clear()
        self.task_created_files.clear()
        self.read_file_segments.clear()
        self.source_read_metrics = SourceReadMetrics()
        self.eager_projection_active = False
        self.pending_user_continuation_id = None
        self.pending_user_continuation = None

    def begin_task(self, task: str) -> None:
        if self.task_id is not None and not is_terminal_task_status(self.task_status):
            raise TaskTransitionError(
                f"task {self.task_id} is still active ({self.task_status.value})"
            )
        self.archive_terminal_task()
        self.task = task
        self.task_sequence += 1
        self.task_id = f"task-{self.task_sequence}"
        self.task_archived = False
        self.reset_task_state()
        self.transition_task(
            TaskStatus.RUNNING,
            trigger="task_started",
            new_task=True,
            persist_plan_snapshot=False,
        )
        self.plan_controller.reset(
            goal=task,
            policy=self.config.plan_policy,
            approval_policy=self.config.plan_approval_policy,
        )

    def transition_task(
        self,
        status: TaskStatus | str,
        *,
        trigger: str,
        waiting_reason: str | None = None,
        new_task: bool = False,
        persist_plan_snapshot: bool = True,
    ) -> None:
        before, after = validate_task_transition(
            self.task_status,
            status,
            new_task=new_task,
        )
        if before is after and self.task_waiting_reason == waiting_reason:
            return
        self.task_status = after
        self.task_waiting_reason = (
            waiting_reason if after is TaskStatus.WAITING_USER else None
        )
        self.trace.log(
            {
                "type": "task_transition",
                "task_id": self.task_id,
                "before": before.value,
                "after": after.value,
                "trigger": trigger,
                "waiting_reason": self.task_waiting_reason,
                "plan_phase": getattr(getattr(self, "plan_state", None), "phase", None).value
                if getattr(getattr(self, "plan_state", None), "phase", None) is not None
                else None,
            }
        )
        self._synchronize_terminal_plan(after, trigger=trigger)
        self.validate_lifecycle_invariants()
        if persist_plan_snapshot and getattr(self, "plan_store", None) is not None:
            self.plan_store.save(self.plan_state, task=self.task)

    def on_plan_transition(self, action: str, before: dict | None, after: dict) -> None:
        if self.task_id is None:
            return
        phase = after.get("phase")
        if phase == "awaiting_approval":
            self.transition_task(
                TaskStatus.WAITING_USER,
                trigger=action,
                waiting_reason="plan_approval",
                persist_plan_snapshot=False,
            )
        elif phase in {"planning", "executing"} and self.task_status is TaskStatus.WAITING_USER:
            self.transition_task(
                TaskStatus.RUNNING,
                trigger=action,
                persist_plan_snapshot=False,
            )
        elif phase == "cancelled" and not is_terminal_task_status(self.task_status):
            self.transition_task(
                TaskStatus.CANCELLED,
                trigger=action,
                persist_plan_snapshot=False,
            )
        self.validate_lifecycle_invariants()

    def validate_lifecycle_invariants(self) -> None:
        if self.task_id is None:
            return
        phase = getattr(getattr(self, "plan_state", None), "phase", None)
        phase_value = getattr(phase, "value", phase)
        if (
            phase_value == "awaiting_approval"
            and self.task_status is not TaskStatus.WAITING_USER
            and not is_terminal_task_status(self.task_status)
        ):
            raise TaskTransitionError(
                "an awaiting-approval plan requires task status waiting_user"
            )
        if self.task_status is TaskStatus.WAITING_USER and phase_value != "awaiting_approval":
            raise TaskTransitionError(
                "task status waiting_user requires an awaiting-approval plan"
            )
        if (
            is_terminal_task_status(self.task_status)
            and getattr(self.plan_state, "execution_path", None) is ExecutionPath.PLAN
            and phase_value not in {"completed", "cancelled"}
        ):
            raise TaskTransitionError(
                "a terminal task requires a terminal plan phase"
            )

    def _synchronize_terminal_plan(self, status: TaskStatus, *, trigger: str) -> None:
        if status not in {TaskStatus.CANCELLED, TaskStatus.FAILED}:
            return
        state = getattr(self, "plan_state", None)
        if (
            state is None
            or state.execution_path is not ExecutionPath.PLAN
            or state.phase not in {
                PlanPhase.PLANNING,
                PlanPhase.AWAITING_APPROVAL,
                PlanPhase.EXECUTING,
            }
        ):
            return
        self.plan_controller.cancel(f"Task became {status.value}: {trigger}")

    def add_user_continuation(self, text: str) -> int:
        if self.task_status is not TaskStatus.WAITING_USER:
            raise ValueError("the current task is not waiting for user input")
        if self.has_pending_user_continuation():
            raise TaskTransitionError(
                "the previous user continuation is still pending; resolve or cancel it first"
            )
        normalized = str(text).strip()
        if not normalized:
            raise ValueError("user continuation must not be empty")
        self.user_continuation_sequence += 1
        self.pending_user_continuation_id = self.user_continuation_sequence
        self.pending_user_continuation = normalized
        self.add_user_message({"role": "user", "content": normalized})
        self.trace.log(
            {
                "type": "user_continuation",
                "task_id": self.task_id,
                "continuation_id": self.pending_user_continuation_id,
                "waiting_reason": self.task_waiting_reason,
                "content_preview": normalized[:500],
            }
        )
        return self.pending_user_continuation_id

    def has_pending_user_continuation(self) -> bool:
        return bool(
            self.pending_user_continuation_id is not None
            and self.pending_user_continuation
        )

    def consume_user_continuation(self, continuation_id: int | None = None) -> None:
        if not self.has_pending_user_continuation():
            raise ValueError("there is no pending user continuation")
        if continuation_id is not None and continuation_id != self.pending_user_continuation_id:
            raise ValueError("the user continuation is no longer current")
        consumed_id = self.pending_user_continuation_id
        self.pending_user_continuation_id = None
        self.pending_user_continuation = None
        self.trace.log(
            {
                "type": "user_continuation_consumed",
                "task_id": self.task_id,
                "continuation_id": consumed_id,
            }
        )

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
        audit_message = dict(message)
        audit_message["runtime_origin"] = True
        self.conversation_messages.append(audit_message)

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

    def record_tool_result_provenance(self, tool_call_id: str, value: str | None) -> None:
        if not value:
            return
        self.tool_result_provenance[str(tool_call_id)] = str(value)[:500]
        while len(self.tool_result_provenance) > 100:
            self.tool_result_provenance.pop(next(iter(self.tool_result_provenance)))

    def record_tool_result_metadata(
        self,
        tool_call_id: str,
        metadata: dict[str, Any] | None,
    ) -> None:
        if not metadata:
            return
        allowed = {
            "projection_kind",
            "source_path",
            "source_sha256",
            "resolved_path",
            "returned_line_start",
            "returned_line_end",
            "total_lines",
            "fully_scanned",
            "reconstructible",
            "next_offset",
        }
        bounded = {key: metadata[key] for key in allowed if key in metadata}
        if not bounded:
            return
        identifier = str(tool_call_id)
        self.tool_result_metadata[identifier] = bounded
        self.record_source_observation(identifier, bounded)
        while len(self.tool_result_metadata) > 200:
            self.tool_result_metadata.pop(next(iter(self.tool_result_metadata)))

    def archive_terminal_task(self) -> bool:
        if (
            self.task_id is None
            or self.task_archived
            or not is_terminal_task_status(self.task_status)
        ):
            return False

        test_result = self.task_test_result or {}
        summary = {
            "task_id": self.task_id,
            "task": self.task,
            "status": self.task_status.value,
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
            "cost": self.cost_tracker.delta(self.task_cost_start),
            "failures": list(self.task_tool_failures),
        }
        plan_summary = self.plan_state.checkpoint_summary()
        if plan_summary is not None:
            summary["plan"] = plan_summary
        self.completed_tasks.append(summary)
        del self.completed_tasks[:-5]
        self.task_archived = True
        return True

    def capture_completed_task(self) -> None:
        self.archive_terminal_task()
