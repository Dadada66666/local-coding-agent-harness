from __future__ import annotations

from pathlib import Path
import sys

import typer

from agent.factory import build_agent_runner
from cli.interactive import resolve_permission, run_interactive
from cli.replay import render_replay
from runtime.config import RunConfig


app = typer.Typer(
    help="Local Coding Agent Harness",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback()
def main(
    ctx: typer.Context,
    permission: str | None = typer.Option(
        None,
        "--permission",
        "-p",
        help="Optional permission mode: read_only, accept_edits, manual_approval.",
    ),
    sandbox: bool = typer.Option(False, "--sandbox", help="Enable srt sandbox wrapping for bash commands."),
    sandbox_auto_allow: bool = typer.Option(
        True,
        "--sandbox-auto-allow/--no-sandbox-auto-allow",
        help="Auto-allow unknown bash only when a strong sandbox is available.",
    ),
    sandbox_fail_if_unavailable: bool = typer.Option(
        False,
        "--sandbox-fail-if-unavailable",
        help="Fail the run if sandbox was requested but srt is unavailable.",
    ),
    sandbox_settings: Path | None = typer.Option(None, "--sandbox-settings", help="Optional srt settings path."),
    bash_env: list[str] | None = typer.Option(
        None,
        "--bash-env",
        help="Explicit non-secret environment variable name to pass to Bash. Repeat as needed.",
    ),
) -> None:
    configure_stdio()
    if ctx.invoked_subcommand is not None:
        return

    start_interactive(
        workdir=Path.cwd(),
        permission=permission,
        sandbox=sandbox,
        sandbox_auto_allow=sandbox_auto_allow,
        sandbox_fail_if_unavailable=sandbox_fail_if_unavailable,
        sandbox_settings=sandbox_settings,
        bash_env=bash_env,
    )


@app.command()
def run(
    task: str | None = typer.Argument(None, help="Optional one-shot task. Omit it to enter interactive mode."),
    permission: str | None = typer.Option(
        None,
        "--permission",
        "-p",
        help="Optional permission mode: read_only, accept_edits, manual_approval.",
    ),
    sandbox: bool = typer.Option(False, "--sandbox", help="Enable srt sandbox wrapping for bash commands."),
    sandbox_auto_allow: bool = typer.Option(
        True,
        "--sandbox-auto-allow/--no-sandbox-auto-allow",
        help="Auto-allow unknown bash only when a strong sandbox is available.",
    ),
    sandbox_fail_if_unavailable: bool = typer.Option(
        False,
        "--sandbox-fail-if-unavailable",
        help="Fail the run if sandbox was requested but srt is unavailable.",
    ),
    sandbox_settings: Path | None = typer.Option(None, "--sandbox-settings", help="Optional srt settings path."),
    bash_env: list[str] | None = typer.Option(
        None,
        "--bash-env",
        help="Explicit non-secret environment variable name to pass to Bash. Repeat as needed.",
    ),
) -> None:
    configure_stdio()
    workdir = Path.cwd()
    if task:
        mode = resolve_permission(permission)
        config = build_run_config(
            mode,
            sandbox,
            sandbox_auto_allow,
            sandbox_fail_if_unavailable,
            sandbox_settings,
            bash_env,
        )
        runner = build_agent_runner(repo_path=workdir, permission_mode=mode, config=config)
        context = runner.run(task)
        typer.echo(f"Report saved to: {context.run_dir / 'report.md'}")
        return

    start_interactive(
        workdir=workdir,
        permission=permission,
        sandbox=sandbox,
        sandbox_auto_allow=sandbox_auto_allow,
        sandbox_fail_if_unavailable=sandbox_fail_if_unavailable,
        sandbox_settings=sandbox_settings,
        bash_env=bash_env,
    )


@app.command()
def report(run_id: str = typer.Argument(..., help="Run id to read from the current WORKDIR.")) -> None:
    configure_stdio()
    path = Path.cwd() / ".agent" / "runs" / run_id / "report.md"
    typer.echo(path.read_text(encoding="utf-8"))


@app.command()
def replay(run_id: str = typer.Argument(..., help="Run id to replay from the current WORKDIR.")) -> None:
    configure_stdio()
    path = Path.cwd() / ".agent" / "runs" / run_id / "trace.jsonl"
    typer.echo(render_replay(path))


def configure_stdio() -> None:
    if sys.platform == "win32":
        return

    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


def build_run_config(
    permission_mode: str,
    sandbox: bool,
    sandbox_auto_allow: bool,
    sandbox_fail_if_unavailable: bool,
    sandbox_settings: Path | None,
    bash_env: list[str] | None = None,
) -> RunConfig:
    return RunConfig(
        permission_mode=permission_mode,
        sandbox_enabled=sandbox,
        sandbox_auto_allow_bash=sandbox_auto_allow,
        sandbox_fail_if_unavailable=sandbox_fail_if_unavailable,
        sandbox_settings_path=str(sandbox_settings) if sandbox_settings else None,
        bash_env_allowlist=tuple(bash_env or ()),
    )


def start_interactive(
    *,
    workdir: Path,
    permission: str | None,
    sandbox: bool,
    sandbox_auto_allow: bool,
    sandbox_fail_if_unavailable: bool,
    sandbox_settings: Path | None,
    bash_env: list[str] | None,
) -> None:
    mode = resolve_permission(permission)
    config = build_run_config(
        mode,
        sandbox,
        sandbox_auto_allow,
        sandbox_fail_if_unavailable,
        sandbox_settings,
        bash_env,
    )
    run_interactive(workdir=workdir, permission_mode=mode, config=config)


if __name__ == "__main__":
    app()
