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
            test_result.get("error") or "N/A",
            "",
            "## Repair Attempts",
            str(context.repair_attempts),
            "",
            "## Cost",
            cost_summary,
            "",
            "## Summary",
            context.final_text or "N/A",
            "",
            "## Artifacts",
            f"- trace: `{context.trace.path}`",
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

    def _changed_files(self, context: AgentContext) -> list[str]:
        if not context.changed_files:
            return ["- N/A"]
        return [f"- {path}" for path in sorted(context.changed_files)]

    def _cost_summary(self, context: AgentContext) -> str:
        tracker = context.cost_tracker
        return (
            f"calls={tracker.calls}, "
            f"input_tokens={tracker.input_tokens}, "
            f"output_tokens={tracker.output_tokens}"
        )
