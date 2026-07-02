from __future__ import annotations

from pathlib import Path

import typer

from agent.factory import build_agent

app = typer.Typer(help="Local Coding Agent Harness")


@app.command()
def run(
    repo: Path = typer.Option(..., "--repo", "-r", help="Repository path to operate on."),
    task: str = typer.Option(..., "--task", "-t", help="Bug, issue, or development request."),
) -> None:
    runner = build_agent(repo_path=repo, task=task)
    runner.run()
    typer.echo(f"run_id={runner.context.run_id}")
    typer.echo(f"run_dir={runner.context.run_dir}")


if __name__ == "__main__":
    app()

