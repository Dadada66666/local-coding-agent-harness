from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import sys

import typer

from agent.factory import build_agent_runner
from runtime.config import RunConfig
from runtime.plan import PlanApprovalPolicy, PlanError, PlanPolicy
from runtime.security import PermissionMode
from runtime.task import TaskStatus

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
    typer.echo(f"Plan mode: {config.plan_policy.value}")
    typer.echo(f"Plan approval: {config.plan_approval_policy.value}")
    typer.echo("Enter a task and press Enter. Type q or exit to quit; use /plan-status for plans.")

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

            if handle_interactive_command(query, runner, context):
                if context.abort_reason and context.abort_reason != "plan_cancelled":
                    break
                continue

            if context.task_status is TaskStatus.WAITING_USER:
                runner.continue_task(context, query)
            else:
                runner.start_task(context, query)
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


def handle_interactive_command(query: str, runner, context) -> bool:
    if not query.startswith("/"):
        return False

    command, _, argument = query.partition(" ")
    argument = argument.strip()
    try:
        if command == "/plan-mode":
            if not argument:
                raise PlanError("usage: /plan-mode auto|required|off")
            try:
                policy = PlanPolicy(argument.lower())
            except ValueError as exc:
                raise PlanError("plan mode must be auto, required, or off") from exc
            context.config.plan_policy = policy
            typer.echo(
                f"Plan mode for future tasks: {policy.value}. "
                "The current task state was not rewritten."
            )
            return True

        if command == "/plan-status":
            typer.echo(context.plan_controller.status_text())
            return True

        if command == "/plan-approval":
            if not argument:
                raise PlanError("usage: /plan-approval manual|auto")
            try:
                approval_policy = PlanApprovalPolicy(argument.lower())
            except ValueError as exc:
                raise PlanError("plan approval must be manual or auto") from exc
            context.config.plan_approval_policy = approval_policy
            typer.echo(
                f"Plan approval for future tasks: {approval_policy.value}. "
                "The current task state was not rewritten."
            )
            return True

        if command == "/plan":
            if argument:
                raise PlanError("usage: /plan")
            if context.task_id is None:
                raise PlanError("there is no current task; use /plan-mode required first")
            if context.task_status is not TaskStatus.RUNNING:
                raise PlanError("/plan requires a currently running task")
            context.plan_controller.force_plan(
                reason="User forced plan mode from the interactive CLI.",
                has_mutations=context.has_task_mutations(),
            )
            runner.resume_runtime(
                context,
                "Runtime notice: the user forced plan mode for this task. Inspect the "
                "repository read-only and submit a structured plan.",
            )
            _echo_current_result(context)
            return True

        if command == "/approve":
            if argument:
                raise PlanError("usage: /approve")
            context.plan_controller.approve()
            runner.resume_runtime(
                context,
                "Runtime notice: the user approved the current plan version. Execute it, "
                "update step status, and verify the result.",
            )
            _echo_current_result(context)
            return True

        if command == "/revise":
            if not argument:
                raise PlanError("usage: /revise <feedback>")
            context.plan_controller.revise(argument)
            runner.resume_runtime(
                context,
                "Runtime notice: the user requested a plan revision. Stay read-only and "
                f"update the structured plan using this feedback: {argument}",
            )
            _echo_current_result(context)
            return True

        if command == "/cancel-plan":
            if argument:
                raise PlanError("usage: /cancel-plan")
            context.plan_controller.cancel("Cancelled by the user from the interactive CLI.")
            context.finished = True
            context.success = False
            context.abort_reason = "plan_cancelled"
            context.final_text = "Stopped: the current plan was cancelled by the user."
            typer.echo(context.final_text)
            return True

        typer.echo(f"Unknown command: {command}")
        return True
    except PlanError as exc:
        typer.echo(f"Plan command failed: {exc}", err=True)
        return True


def _echo_current_result(context) -> None:
    if context.final_text:
        typer.echo(context.final_text)
