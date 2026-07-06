from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import sys

import typer

from agent.context import RunConfig
from agent.factory import build_agent_runner
from runtime.permission import PermissionMode

app = typer.Typer(
    help="Local Coding Agent Harness",
    invoke_without_command=True,
    no_args_is_help=False,
)

PROMPT_CYAN = "\033[36m"
PROMPT_RESET = "\033[0m"
READLINE_IGNORE_START = "\001"
READLINE_IGNORE_END = "\002"


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
) -> None:
    configure_stdio()
    if ctx.invoked_subcommand is not None:
        return

    run_interactive(
        workdir=Path.cwd(),
        permission=permission,
        sandbox=sandbox,
        sandbox_auto_allow=sandbox_auto_allow,
        sandbox_fail_if_unavailable=sandbox_fail_if_unavailable,
        sandbox_settings=sandbox_settings,
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
) -> None:
    configure_stdio()
    workdir = Path.cwd()
    if task:
        mode = resolve_permission(permission)
        config = build_run_config(mode, sandbox, sandbox_auto_allow, sandbox_fail_if_unavailable, sandbox_settings)
        runner = build_agent_runner(repo_path=workdir, permission_mode=mode, config=config)
        context = runner.run(task)
        typer.echo(f"Report saved to: {context.run_dir / 'report.md'}")
        return

    run_interactive(
        workdir=workdir,
        permission=permission,
        sandbox=sandbox,
        sandbox_auto_allow=sandbox_auto_allow,
        sandbox_fail_if_unavailable=sandbox_fail_if_unavailable,
        sandbox_settings=sandbox_settings,
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


def render_replay(path: Path) -> str:
    events = _read_trace_events(path)
    if not events:
        return "No trace events found."

    turns: dict[int, list[dict]] = {}
    for event in events:
        turn_id = event.get("turn_id")
        if turn_id is not None:
            turns.setdefault(int(turn_id), []).append(event)

    if not turns:
        return "No turn-scoped trace events found."

    lines: list[str] = []
    for turn_id in sorted(turns):
        turn_events = turns[turn_id]
        lines.append(f"Turn {turn_id}")
        model_event = _first_event(turn_events, "model_call_end")
        if model_event:
            lines.append(
                "  model_call: "
                f"input_tokens={model_event.get('input_tokens')} "
                f"output_tokens={model_event.get('output_tokens')} "
                f"tools={model_event.get('tool_names', [])}"
            )

        results = {
            event.get("tool_call_id"): event
            for event in turn_events
            if event.get("type") == "tool_result" and event.get("tool_call_id")
        }
        for event in turn_events:
            if event.get("type") != "tool_use":
                continue
            result = results.get(event.get("tool_call_id"))
            lines.append(_render_tool_replay(event, result))

    return "\n".join(lines)


def _read_trace_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _first_event(events: list[dict], event_type: str) -> dict | None:
    for event in events:
        if event.get("type") == event_type:
            return event
    return None


def _render_tool_replay(event: dict, result: dict | None) -> str:
    tool = event.get("tool")
    normalized = event.get("normalized_args") or event.get("args") or {}
    status = "pending" if result is None else "ok" if result.get("ok") else "failed"
    suffix = _tool_semantics(tool, result)
    return f"  tool: {tool} args={event.get('args', {})} normalized={normalized} -> {status}{suffix}"


def _tool_semantics(tool: str, result: dict | None) -> str:
    metadata = (result or {}).get("metadata") or {}
    if tool == "list_dir":
        return " (searches file names)"
    if tool == "grep":
        match_count = metadata.get("match_count")
        if match_count == 0:
            return " (no matches, searched file contents only)"
        return " (searches file contents)"
    return ""

def run_interactive(
    workdir: Path,
    permission: str | None,
    sandbox: bool = False,
    sandbox_auto_allow: bool = True,
    sandbox_fail_if_unavailable: bool = False,
    sandbox_settings: Path | None = None,
) -> None:
    mode = resolve_permission(permission)
    config = build_run_config(mode, sandbox, sandbox_auto_allow, sandbox_fail_if_unavailable, sandbox_settings)
    runner = build_agent_runner(repo_path=workdir, permission_mode=mode, config=config)
    context = runner.start_interactive()

    typer.echo("Local Coding Agent Harness")
    typer.echo(f"WORKDIR: {workdir.resolve()}")
    typer.echo(f"Permission: {mode}")
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


def build_run_config(
    permission_mode: str,
    sandbox: bool,
    sandbox_auto_allow: bool,
    sandbox_fail_if_unavailable: bool,
    sandbox_settings: Path | None,
) -> RunConfig:
    return RunConfig(
        permission_mode=permission_mode,
        sandbox_enabled=sandbox,
        sandbox_auto_allow_bash=sandbox_auto_allow,
        sandbox_fail_if_unavailable=sandbox_fail_if_unavailable,
        sandbox_settings_path=str(sandbox_settings) if sandbox_settings else None,
    )


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


if __name__ == "__main__":
    app()
