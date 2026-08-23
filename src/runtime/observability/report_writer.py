from __future__ import annotations

from pathlib import Path

from runtime.call_budget import TaskCallBudget
from runtime.plan import PlanPolicy
from runtime.session import AgentContext


class ReportWriter:
    def write(self, context: AgentContext) -> Path:
        path = context.run_dir / "report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        test_result = self._task_test_result(context)
        cost_path = context.run_dir / "cost.json"
        cost_summary = self._cost_summary(context)

        lines = [
            "# Agent Run Report",
            "",
            "## Session",
            f"Run ID: {context.run_id}",
            f"Status: {self._task_status(context)}",
            "",
            "## Task",
            self._task_summary(context),
            f"ID: {getattr(context, 'task_id', None) or 'N/A'}",
            f"Status: {self._task_status(context)}",
            f"Waiting reason: {getattr(context, 'task_waiting_reason', None) or 'N/A'}",
            "",
            "## Status",
            f"Success: {self._success_status(context)}",
            "",
            *self._plan_section(context),
            "## Changed Files",
            *self._changed_files(context),
            "",
            "## Session Changed Files",
            *self._session_changed_files(context),
            "",
            "## Test Result",
            f"Command: {test_result.get('command', 'N/A')}",
            f"Result: {'passed' if test_result.get('ok') else 'failed' if test_result else 'not recorded'}",
            f"Verification: {self._verification_status(context)}",
            f"Level: {test_result.get('verification_level', 'not recorded')}",
            "",
            "## Failure Summary",
            self._failure_summary(context, test_result),
            "",
            "## Tool Failures",
            *self._tool_failures(context),
            "",
            "## Repair Attempts",
            str(context.repair_attempts),
            "",
            "## Model Call Budget",
            *self._call_budget(context),
            "",
            "## Task Cost",
            self._cost_summary(context, task_only=True),
            "",
            "## Session Cost",
            cost_summary,
            "",
            "## Context Management",
            *self._context_management_summary(context),
            "",
            "## Source Read Efficiency",
            *self._source_read_efficiency(context),
            "",
            "## Artifact Persistence",
            *self._artifact_summary(context),
            "",
            "## Sandbox",
            *self._sandbox_summary(context),
            "",
            "## Tool Efficiency",
            *self._tool_efficiency(context),
            "",
            "## Diff",
            *self._diff_summary(context),
            "",
            *self._completed_tasks_section(context),
            "## Model-authored Summary",
            "Verification authority: see the structured `Test Result` section above.",
            self._final_summary(context),
            "",
            "## Artifacts",
            f"- trace: `{context.trace.path}`",
            f"- readable_trace: `{context.run_dir / 'readable_trace.md'}`",
            f"- diff: `{context.run_dir / 'diff.patch'}`",
            f"- cost: `{cost_path}`",
            f"- artifacts: `{context.run_dir / 'artifacts'}`",
            *self._plan_artifact(context),
            "",
        ]

        content = "\n".join(lines)
        redactor = getattr(context, "redactor", None)
        if redactor is not None:
            content = redactor.redact(content)
        path.write_text(content, encoding="utf-8")
        return path

    def _verification_status(self, context: AgentContext) -> str:
        test_result = self._task_test_result(context)
        if not test_result:
            return "not recorded"
        if not test_result.get("ok"):
            return "failed"
        if getattr(context, "task_unresolved_mutation_failure", False):
            return "invalidated by unresolved mutation failure"

        changed_files = getattr(context, "task_changed_files", None)
        if changed_files is None:
            changed_files = getattr(context, "changed_files", set())
        has_task_mutations = getattr(context, "has_task_mutations", None)
        task_mutated = (
            bool(has_task_mutations())
            if callable(has_task_mutations)
            else bool(changed_files)
        )
        if (
            task_mutated
            and hasattr(context, "task_verification_version")
            and context.task_verification_version != context.mutation_version
        ):
            return "stale"
        return "passed"

    def _task_test_result(self, context: AgentContext) -> dict:
        if hasattr(context, "task_test_result"):
            return context.task_test_result or {}
        return context.last_test_result or {}

    def _failure_summary(self, context: AgentContext, test_result: dict) -> str:
        if self._task_status(context) == "waiting_user":
            return "N/A (task is waiting for user input)"
        if context.success is True:
            return "N/A"
        if test_result.get("error"):
            return test_result["error"]
        if getattr(context, "task_unresolved_mutation_failure", False):
            return "A mutation operation failed and was not recovered."
        if context.success is False and context.final_text:
            return context.final_text
        return "N/A"

    def _tool_failures(self, context: AgentContext) -> list[str]:
        failures = getattr(context, "task_tool_failures", [])
        if not failures:
            return ["- N/A"]
        return [
            f"- turn {failure.get('turn_id', 'N/A')} {failure.get('tool', 'tool')}: "
            f"{failure.get('error', 'failed')}"
            for failure in failures
        ]

    def _final_summary(self, context: AgentContext) -> str:
        text = (context.final_text or "N/A").strip()
        if text.startswith("## Summary"):
            text = text[len("## Summary") :].lstrip("\r\n ")
        return text or "N/A"

    def _task_summary(self, context: AgentContext) -> str:
        return context.task

    def _task_status(self, context: AgentContext) -> str:
        return getattr(getattr(context, "task_status", None), "value", "unknown")

    def _success_status(self, context: AgentContext) -> str:
        if self._task_status(context) == "waiting_user":
            return "pending"
        return str(context.success).lower()

    def _completed_tasks_section(self, context: AgentContext) -> list[str]:
        tasks = getattr(context, "completed_tasks", [])
        if not tasks:
            return []
        lines = ["## Completed Tasks"]
        for task in tasks[-5:]:
            lines.append(
                f"- {task.get('task_id', 'N/A')} [{task.get('status', 'unknown')}]: "
                f"{task.get('task', '')}"
            )
        return [*lines, ""]

    def _diff_summary(self, context: AgentContext) -> list[str]:
        manager = context.diff_manager
        probe = getattr(manager, "probe_availability", None)
        if callable(probe):
            probe()
        return [
            f"- availability: {getattr(manager, 'availability', 'unknown')}",
            f"- reason: {getattr(manager, 'reason', 'unknown')}",
        ]

    def _changed_files(self, context: AgentContext) -> list[str]:
        changed_files = getattr(context, "task_changed_files", context.changed_files)
        if not changed_files:
            return ["- N/A"]
        return [f"- {path}" for path in sorted(changed_files)]

    def _session_changed_files(self, context: AgentContext) -> list[str]:
        if not context.changed_files:
            return ["- N/A"]
        return [f"- {path}" for path in sorted(context.changed_files)]

    def _sandbox_summary(self, context: AgentContext) -> list[str]:
        sandbox = getattr(context, "sandbox", None)
        if sandbox is None:
            return [
                "- enabled: false",
                "- available: false",
                "- strong_boundary: false",
                "- settings_applied: false",
                "- protected_reads_enforced: false",
                "- settings_path: N/A",
                "- executable_path: N/A",
                "- auto_allowed_unknown_bash: 0",
                "- reason: N/A",
            ]

        status = sandbox.status
        settings_path = str(status.settings_path) if status.settings_path else "N/A"
        executable_path = status.executable_path or "N/A"
        reason = status.reason or "N/A"
        return [
            f"- enabled: {str(status.enabled).lower()}",
            f"- available: {str(status.available).lower()}",
            f"- strong_boundary: {str(status.strong_boundary).lower()}",
            f"- settings_applied: {str(status.settings_applied).lower()}",
            f"- protected_reads_enforced: {str(status.protected_reads_enforced).lower()}",
            f"- settings_path: `{settings_path}`",
            f"- executable_path: `{executable_path}`",
            f"- auto_allowed_unknown_bash: {context.sandbox_auto_allowed_unknown_bash_count}",
            f"- reason: {reason}",
        ]

    def _tool_efficiency(self, context: AgentContext) -> list[str]:
        warnings = self.analyze_tool_efficiency(context)
        if not warnings:
            return ["- N/A"]
        return [f"- {warning}" for warning in warnings]

    def analyze_tool_efficiency(self, context: AgentContext) -> list[str]:
        budget = context.tool_budget
        warnings = []

        if budget.read_file_calls >= 8 and budget.grep_calls == 0:
            warnings.append(
                "Many files were read without repository search. "
                "This may indicate inefficient context discovery."
            )

        if budget.truncated_results >= 3:
            warnings.append(
                "Several tool results were truncated. "
                "Consider narrowing queries or improving pagination."
            )

        failures = getattr(context, "task_tool_failures", [])
        repeated = {}
        for failure in failures:
            key = (failure.get("tool"), failure.get("error"))
            repeated[key] = repeated.get(key, 0) + 1
        for (tool, error), count in repeated.items():
            if count >= 2:
                warnings.append(
                    f"Repeated deterministic failure {count} times: {tool} ({error})."
                )

        if getattr(context, "task_model_calls", 0) >= 10:
            warnings.append(
                f"Current task required {context.task_model_calls} model calls; "
                "inspect trace for avoidable retries or oversized tool payloads."
            )

        return warnings

    def _cost_summary(self, context: AgentContext, *, task_only: bool = False) -> str:
        tracker = context.cost_tracker
        values = (
            tracker.delta(getattr(context, "task_cost_start", None))
            if task_only
            else tracker.snapshot()
        )
        return (
            f"calls={values['calls']}, "
            f"input_tokens={values['input_tokens']}, "
            "cache_creation_input_tokens="
            f"{values['cache_creation_input_tokens']}, "
            f"cache_read_input_tokens={values['cache_read_input_tokens']}, "
            f"output_tokens={values['output_tokens']}"
        )

    def _call_budget(self, context: AgentContext) -> list[str]:
        budget = TaskCallBudget.from_context(context)
        task_cost = context.cost_tracker.delta(context.task_cost_start)
        return [
            f"- attempted_model_calls: {budget.used_calls}/{budget.max_calls}",
            f"- completed_provider_calls: {task_cost['calls']}",
            f"- remaining: {budget.remaining_calls}",
        ]

    def _context_management_summary(self, context: AgentContext) -> list[str]:
        tracker = context.cost_tracker
        events = getattr(tracker, "context_events", [])
        saved_tokens = sum(max(int(event.get("saved_tokens", 0)), 0) for event in events)
        rebase_events = [event for event in events if event.get("type") == "context_rebase"]
        round_budget_events = [
            event for event in events if event.get("type") == "tool_result_budget"
        ]
        lines = [
            "- scope: session",
            f"- window_tokens: {getattr(context.config, 'context_window_tokens', None) or 'unknown'}",
            f"- auto_compact_ratio: {context.config.context_auto_compact_ratio}",
            f"- full_rebase_events: {len(rebase_events)}",
            f"- round_budget_projection_events: {len(round_budget_events)}",
            "- round_budget_results_projected: "
            f"{sum(max(int(event.get('replaced_results', 0)), 0) for event in round_budget_events)}",
            f"- overflow_recovery_attempts: {getattr(context, 'context_recovery_attempts', 0)}",
            f"- estimated_tokens_saved: {saved_tokens}",
        ]
        return lines

    def _source_read_efficiency(self, context: AgentContext) -> list[str]:
        snapshot = getattr(context, "source_efficiency_snapshot", None)
        values = snapshot() if callable(snapshot) else {}
        if not values:
            return ["- N/A"]
        return [
            f"- read_file_calls: {values['read_file_calls']}",
            f"- unique_files_read: {values['unique_files_read']}",
            f"- unique_source_lines_returned: {values['unique_source_lines_returned']}",
            f"- duplicate_source_lines_returned: {values['duplicate_source_lines_returned']}",
            f"- rehydration_reads: {values['rehydration_reads']}",
            f"- rehydrated_source_lines: {values['rehydrated_source_lines']}",
            "- non_rehydration_overlap_lines: "
            f"{values['non_rehydration_overlap_lines']}",
            f"- overlap_ratio: {values['overlap_ratio']:.2%}",
            f"- files_fully_scanned: {values['files_fully_scanned']}",
            f"- high_overlap_rereads: {values['high_overlap_rereads']}",
            f"- redundant_reads_avoided: {values['redundant_reads_avoided']}",
            "- source_observations_projected: "
            f"{values['source_observations_projected']}",
        ]

    def _artifact_summary(self, context: AgentContext) -> list[str]:
        artifacts = getattr(context, "artifacts", None)
        snapshot = getattr(artifacts, "snapshot", None)
        values = snapshot() if callable(snapshot) else {}
        if not values:
            return ["- N/A"]
        return [
            f"- created: {values['created']}",
            f"- chars_persisted: {values['chars_persisted']}",
            f"- large_output_artifacts: {values['large_output_artifacts']}",
        ]

    def _plan_section(self, context: AgentContext) -> list[str]:
        state = getattr(context, "plan_state", None)
        if state is None or state.policy is PlanPolicy.OFF:
            return []
        lines = [
            "## Plan",
            f"- policy: {state.policy.value}",
            f"- approval_policy: {state.approval_policy.value}",
            f"- execution_path: {state.execution_path.value}",
            f"- phase: {state.phase.value}",
            f"- version: {state.version}",
            f"- approved_version: {state.approved_version}",
            f"- approval_source: {state.approval_source or 'N/A'}",
            f"- selection_reason: {state.selection_reason or 'N/A'}",
        ]
        for step in state.steps[:50]:
            lines.append(f"- [{step.status.value}] {step.id}: {step.description}")
        if len(state.steps) > 50:
            lines.append(f"- ... {len(state.steps) - 50} steps omitted")
        return [*lines, ""]

    def _plan_artifact(self, context: AgentContext) -> list[str]:
        state = getattr(context, "plan_state", None)
        if state is None or state.policy is PlanPolicy.OFF:
            return []
        path = context.run_dir / "plan.json"
        if not path.is_file():
            return []
        return [f"- plan: `{path}`"]
