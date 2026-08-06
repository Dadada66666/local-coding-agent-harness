from __future__ import annotations

from pathlib import Path
import sys

import typer

from agent.factory import build_agent_runner
from cli.interactive import resolve_permission, run_interactive
from cli.replay import render_replay
from runtime.config import RunConfig
from runtime.plan import PlanPolicy


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
    plan_mode: str | None = typer.Option(
        None,
        "--plan-mode",
        help="Plan policy: off, auto, or required. Defaults to off for compatibility.",
    ),
    force_plan: bool = typer.Option(
        False,
        "--plan",
        help="Require a user-approved plan before execution.",
    ),
    no_plan: bool = typer.Option(
        False,
        "--no-plan",
        help="Disable plan capabilities.",
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
        plan_policy=resolve_plan_policy(plan_mode, force_plan, no_plan),
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
    plan_mode: str | None = typer.Option(
        None,
        "--plan-mode",
        help="Plan policy: off, auto, or required. Defaults to off for compatibility.",
    ),
    force_plan: bool = typer.Option(False, "--plan", help="Require plan mode."),
    no_plan: bool = typer.Option(False, "--no-plan", help="Disable plan mode."),
) -> None:
    configure_stdio()
    workdir = Path.cwd()
    if task:
        mode = resolve_permission(permission)
        policy = resolve_plan_policy(plan_mode, force_plan, no_plan)
        config = build_run_config(
            mode,
            sandbox,
            sandbox_auto_allow,
            sandbox_fail_if_unavailable,
            sandbox_settings,
            bash_env,
            policy,
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
        plan_policy=resolve_plan_policy(plan_mode, force_plan, no_plan),
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
    plan_policy: PlanPolicy | str = PlanPolicy.OFF,
) -> RunConfig:
    return RunConfig(
        permission_mode=permission_mode,
        sandbox_enabled=sandbox,
        sandbox_auto_allow_bash=sandbox_auto_allow,
        sandbox_fail_if_unavailable=sandbox_fail_if_unavailable,
        sandbox_settings_path=str(sandbox_settings) if sandbox_settings else None,
        bash_env_allowlist=tuple(bash_env or ()),
        plan_policy=plan_policy,
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
    plan_policy: PlanPolicy = PlanPolicy.OFF,
) -> None:
    mode = resolve_permission(permission)
    config = build_run_config(
        mode,
        sandbox,
        sandbox_auto_allow,
        sandbox_fail_if_unavailable,
        sandbox_settings,
        bash_env,
        plan_policy,
    )
    run_interactive(workdir=workdir, permission_mode=mode, config=config)


def resolve_plan_policy(
    plan_mode: str | None,
    force_plan: bool = False,
    no_plan: bool = False,
) -> PlanPolicy:
    if force_plan and no_plan:
        raise typer.BadParameter("--plan and --no-plan cannot be used together")
    if plan_mode is not None and (force_plan or no_plan):
        raise typer.BadParameter(
            "--plan-mode cannot be combined with --plan or --no-plan"
        )
    if force_plan:
        return PlanPolicy.REQUIRED
    if no_plan:
        return PlanPolicy.OFF
    if plan_mode is None:
        return PlanPolicy.OFF
    try:
        return PlanPolicy(plan_mode.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(policy.value for policy in PlanPolicy)
        raise typer.BadParameter(f"plan mode must be one of: {allowed}") from exc


if __name__ == "__main__":
    app()
