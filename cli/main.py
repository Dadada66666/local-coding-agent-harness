from __future__ import annotations

from pathlib import Path

import typer

from agent.factory import build_agent_runner
from runtime.permission import PermissionMode

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
) -> None:
    if ctx.invoked_subcommand is not None:
        return

    run_interactive(workdir=Path.cwd(), permission=permission)


@app.command()
def run(
    task: str | None = typer.Argument(None, help="Optional one-shot task. Omit it to enter interactive mode."),
    permission: str | None = typer.Option(
        None,
        "--permission",
        "-p",
        help="Optional permission mode: read_only, accept_edits, manual_approval.",
    ),
) -> None:
    workdir = Path.cwd()
    if task:
        mode = resolve_permission(permission)
        runner = build_agent_runner(repo_path=workdir, permission_mode=mode)
        context = runner.run(task)
        typer.echo(f"Report saved to: {context.run_dir / 'report.md'}")
        return

    run_interactive(workdir=workdir, permission=permission)


@app.command()
def report(run_id: str = typer.Argument(..., help="Run id to read from the current WORKDIR.")) -> None:
    path = Path.cwd() / ".agent" / "runs" / run_id / "report.md"
    typer.echo(path.read_text(encoding="utf-8"))


@app.command()
def replay(run_id: str = typer.Argument(..., help="Run id to replay from the current WORKDIR.")) -> None:
    path = Path.cwd() / ".agent" / "runs" / run_id / "trace.jsonl"
    typer.echo(path.read_text(encoding="utf-8"))


def run_interactive(workdir: Path, permission: str | None) -> None:
    mode = resolve_permission(permission)
    runner = build_agent_runner(repo_path=workdir, permission_mode=mode)
    context = runner.start_interactive()

    typer.echo("Local Coding Agent Harness")
    typer.echo(f"WORKDIR: {workdir.resolve()}")
    typer.echo(f"Permission: {mode}")
    typer.echo("输入问题，回车发送。输入 q 或 exit 退出。")

    while True:
        try:
            query = typer.prompt("agent")
        except (EOFError, KeyboardInterrupt):
            typer.echo("")
            break

        query = query.strip()
        if query.lower() in {"q", "quit", "exit"}:
            break
        if not query:
            continue

        runner.submit(context, query)
        if context.final_text:
            typer.echo(context.final_text)

    runner.finish(context)
    typer.echo(f"Report saved to: {context.run_dir / 'report.md'}")


def resolve_permission(permission: str | None) -> str:
    if permission:
        validate_permission(permission)
        return permission
    return choose_permission()


def choose_permission() -> str:
    typer.echo("选择权限模式:")
    typer.echo("  1) read_only       只允许读取和搜索")
    typer.echo("  2) accept_edits    允许编辑和运行命令，危险命令仍会被硬拦截")
    typer.echo("  3) manual_approval 编辑和命令执行前询问确认")

    mapping = {
        "1": PermissionMode.READ_ONLY,
        "read_only": PermissionMode.READ_ONLY,
        "2": PermissionMode.ACCEPT_EDITS,
        "accept_edits": PermissionMode.ACCEPT_EDITS,
        "3": PermissionMode.MANUAL_APPROVAL,
        "manual_approval": PermissionMode.MANUAL_APPROVAL,
        "": PermissionMode.MANUAL_APPROVAL,
    }

    while True:
        choice = typer.prompt("permission", default="3").strip().lower()
        if choice in mapping:
            return mapping[choice]
        typer.echo("请输入 1、2、3，或 read_only / accept_edits / manual_approval。")


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