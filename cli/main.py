from __future__ import annotations

from pathlib import Path

import typer

from agent.factory import build_agent_runner
from runtime.permission import PermissionMode

app = typer.Typer(help="Local Coding Agent Harness")


@app.command()
def run(
    task: str = typer.Argument(..., help="Bug, issue, or development request."),
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Repository path to operate on."),
    permission: str = typer.Option(
        PermissionMode.MANUAL_APPROVAL,
        "--permission",
        "-p",
        help="Permission mode: read_only, accept_edits, manual_approval.",
    ),
) -> None:
    validate_permission(permission)
    runner = build_agent_runner(repo_path=repo, permission_mode=permission)
    context = runner.run(task)
    typer.echo(f"Report saved to: {context.run_dir / 'report.md'}")


@app.command()
def report(
    run_id: str = typer.Argument(..., help="Run id to read."),
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Repository path containing .agent/runs."),
) -> None:
    path = repo / ".agent" / "runs" / run_id / "report.md"
    typer.echo(path.read_text(encoding="utf-8"))


@app.command()
def replay(
    run_id: str = typer.Argument(..., help="Run id to replay."),
    repo: Path = typer.Option(Path("."), "--repo", "-r", help="Repository path containing .agent/runs."),
) -> None:
    path = repo / ".agent" / "runs" / run_id / "trace.jsonl"
    typer.echo(path.read_text(encoding="utf-8"))


def validate_permission(permission: str) -> None:
    allowed = {
        PermissionMode.READ_ONLY,
        PermissionMode.ACCEPT_EDITS,
        PermissionMode.MANUAL_APPROVAL,
    }
    if permission not in allowed:
        raise typer.BadParameter(f"permission must be one of: {', '.join(sorted(allowed))}")


if __name__ == "__main__":
    app()

