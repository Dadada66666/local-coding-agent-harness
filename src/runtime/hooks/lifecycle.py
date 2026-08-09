from __future__ import annotations

from runtime.observability.console import print_model_call_start
from runtime.observability.readable_trace_writer import ReadableTraceWriter


def user_prompt_submit_hook(task: str, context) -> None:
    context.trace.log(
        {
            "type": "user_prompt",
            "task_id": getattr(context, "task_id", None),
            "task_sequence": getattr(context, "task_sequence", 0),
            "task": task,
            "workdir": str(context.repo_path),
        }
    )

    if not getattr(context, "run_banner_printed", False):
        print(f"[run] {context.run_id}")
        print(f"[task] {task}")
        print(f"[workdir] {context.repo_path}")
        context.run_banner_printed = True

    return None


def model_call_start_hook(context, task_model_call: int) -> None:
    print_model_call_start(task_model_call, context.config.max_turns)
    return None


def stop_report_hook(context) -> None:
    artifact_errors = []
    try:
        context.run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        artifact_errors.append({"artifact": "run_dir", "error": str(exc)})

    readable_trace_path, error = _write_stop_artifact(
        context,
        "readable_trace",
        lambda: ReadableTraceWriter().write(context),
    )
    if error:
        artifact_errors.append(error)

    report_path, error = _write_stop_artifact(
        context,
        "report",
        lambda: context.report_writer.write(context),
    )
    if error:
        artifact_errors.append(error)

    diff_path, error = _write_stop_artifact(
        context,
        "diff",
        lambda: context.diff_manager.write_patch(context),
    )
    if error:
        artifact_errors.append(error)

    cost_path, error = _write_stop_artifact(
        context,
        "cost",
        lambda: context.cost_tracker.write(context),
    )
    if error:
        artifact_errors.append(error)

    context.trace.log(
        {
            "type": "stop",
            "success": context.success,
            "task_id": getattr(context, "task_id", None),
            "task_status": getattr(
                getattr(context, "task_status", None),
                "value",
                None,
            ),
            "report_path": str(report_path) if report_path else None,
            "diff_path": str(diff_path) if diff_path else None,
            "cost_path": str(cost_path) if cost_path else None,
            "readable_trace_path": str(readable_trace_path) if readable_trace_path else None,
            "artifact_errors": artifact_errors,
            "repair_attempts": context.repair_attempts,
        }
    )

    _print_artifact_path("report", report_path)
    _print_artifact_path("readable-trace", readable_trace_path)
    _print_artifact_path("diff", diff_path)
    _print_artifact_path("cost", cost_path)
    for error in artifact_errors:
        print(f"[artifact-error] {error['artifact']}: {error['error']}")

    return None


def _write_stop_artifact(context, name: str, writer):
    try:
        return writer(), None
    except Exception as exc:
        error = {"artifact": name, "error": str(exc), "exception_type": exc.__class__.__name__}
        context.trace.log({"type": "stop_artifact_error", **error})
        return None, error


def _print_artifact_path(label: str, path) -> None:
    if path is not None:
        print(f"[{label}] {path}")
