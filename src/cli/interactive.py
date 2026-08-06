from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sys

import typer

from agent.factory import build_agent_runner
from runtime.config import RunConfig
from runtime.security import PermissionMode

PROMPT_CYAN = "\033[36m"
PROMPT_RESET = "\033[0m"
READLINE_IGNORE_START = "\001"
READLINE_IGNORE_END = "\002"


def run_interactive(
    workdir: Path,
    permission_mode: str,
    config: RunConfig,
) -> None:
    runner = build_agent_runner(
        repo_path=workdir,
        permission_mode=permission_mode,
        config=config,
    )
    context = runner.start_interactive()

    typer.echo("Local Coding Agent Harness")
    typer.echo(f"WORKDIR: {workdir.resolve()}")
    typer.echo(f"Permission: {permission_mode}")
    typer.echo(f"Sandbox: {context.sandbox.prompt_status() if context.sandbox else 'disabled'}")
    typer.echo("Enter a task and press Enter. Type q or exit to quit.")

    try:
        while True:
            try:
                query = input(interactive_prompt())
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
            if context.abort_reason:
                break
    finally:
        runner.finish(context)

    typer.echo(f"Report saved to: {context.run_dir / 'report.md'}")


def interactive_prompt() -> str:
    return f"{_prompt_control(PROMPT_CYAN)}s01 >> {_prompt_control(PROMPT_RESET)}"


def _prompt_control(sequence: str) -> str:
    if not _readline_prompt_markers_supported():
        return sequence
    return f"{READLINE_IGNORE_START}{sequence}{READLINE_IGNORE_END}"


@lru_cache(maxsize=1)
def _readline_prompt_markers_supported() -> bool:
    if sys.platform == "win32":
        return False

    try:
        import readline  # noqa: F401
    except ImportError:
        return False

    return True


def resolve_permission(permission: str | None) -> str:
    if permission:
        validate_permission(permission)
        return permission
    return choose_permission()


def choose_permission() -> str:
    typer.echo("Choose permission mode:")
    typer.echo("  1) read_only       allow reads and searches only")
    typer.echo("  2) accept_edits    allow edits and safe commands; risky commands are still gated")
    typer.echo("  3) manual_approval ask before edits and command execution")

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
        typer.echo("Enter 1, 2, 3, or read_only / accept_edits / manual_approval.")


def validate_permission(permission: str) -> None:
    allowed = {
        PermissionMode.READ_ONLY,
        PermissionMode.ACCEPT_EDITS,
        PermissionMode.MANUAL_APPROVAL,
    }
    if permission not in allowed:
        raise typer.BadParameter(f"permission must be one of: {', '.join(sorted(allowed))}")
