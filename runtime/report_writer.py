from __future__ import annotations

from pathlib import Path

from agent.context import AgentContext


class ReportWriter:
    def write(self, context: AgentContext) -> Path:
        path = context.run_dir / "report.md"
        test_result = context.last_test_result or {}
        cost_path = context.run_dir / "cost.json"
        cost_summary = self._cost_summary(context)

        lines = [
            "# Agent Run Report",
            "",
            "## Task",
            context.task,
            "",
            "## Status",
            f"Success: {str(context.success).lower()}",
            "",
            "## Changed Files",
            *self._changed_files(context),
            "",
            "## Test Result",
            f"Command: {test_result.get('command', 'N/A')}",
            f"Result: {'passed' if test_result.get('ok') else 'failed' if test_result else 'not recorded'}",
            f"Verification: {self._verification_status(context)}",
            "",
            "## Failure Summary",
            self._failure_summary(context, test_result),
            "",
            "## Repair Attempts",
            str(context.repair_attempts),
            "",
            "## Cost",
            cost_summary,
            "",
            "## Sandbox",
            *self._sandbox_summary(context),
            "",
            "## Tool Efficiency",
            *self._tool_efficiency(context),
            "",
            "## Summary",
            context.final_text or "N/A",
            "",
            "## Artifacts",
            f"- trace: `{context.trace.path}`",
            f"- readable_trace: `{context.run_dir / 'readable_trace.md'}`",
            f"- diff: `{context.run_dir / 'diff.patch'}`",
            f"- cost: `{cost_path}`",
            f"- artifacts: `{context.run_dir / 'artifacts'}`",
            "",
        ]

        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def _verification_status(self, context: AgentContext) -> str:
        if context.last_test_result is None:
            return "not recorded"
        return "passed" if context.last_test_result.get("ok") else "failed"

    def _failure_summary(self, context: AgentContext, test_result: dict) -> str:
        if test_result.get("error"):
            return test_result["error"]
        if context.success is False and context.final_text:
            return context.final_text
        return "N/A"

    def _changed_files(self, context: AgentContext) -> list[str]:
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

        return warnings

    def _cost_summary(self, context: AgentContext) -> str:
        tracker = context.cost_tracker
        return (
            f"calls={tracker.calls}, "
            f"input_tokens={tracker.input_tokens}, "
            f"output_tokens={tracker.output_tokens}"
        )
