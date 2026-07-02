from __future__ import annotations

from agent.context import AgentContext


class ReportWriter:
    def write(self, context: AgentContext) -> None:
        path = context.run_dir / "report.md"
        path.write_text(
            "\n".join(
                [
                    "# Agent Run Report",
                    "",
                    f"- Run ID: `{context.run_id}`",
                    f"- Repository: `{context.repo_path}`",
                    f"- Task: {context.config.task}",
                    f"- Stop reason: `{context.state.stop_reason}`",
                    f"- Iterations: {context.state.iteration}",
                    "",
                    "## Artifacts",
                    "",
                    "- `trace.jsonl`",
                    "- `diff.patch`",
                    "- `cost.json`",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

