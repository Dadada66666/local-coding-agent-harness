from __future__ import annotations

import argparse
from collections import OrderedDict
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterable

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]
TRACE_NAME = "coding-agent-task"


class ExportError(RuntimeError):
    pass


@dataclass(frozen=True)
class Observation:
    order: int
    name: str
    kind: str
    metadata: dict[str, Any]
    usage_details: dict[str, int] = field(default_factory=dict)
    level: str = "DEFAULT"


@dataclass(frozen=True)
class TaskTrace:
    run_id: str
    task_id: str
    metadata: dict[str, Any]
    observations: tuple[Observation, ...]


def read_trace(path: Path) -> tuple[list[dict[str, Any]], int]:
    events: list[dict[str, Any]] = []
    skipped = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                skipped += 1
    return events, skipped


def project_trace(
    events: Iterable[dict[str, Any]],
    *,
    run_id_hint: str | None = None,
) -> tuple[TaskTrace, ...]:
    indexed = list(enumerate(events))
    run_id = next(
        (str(event["run_id"]) for _, event in indexed if event.get("run_id")),
        run_id_hint or "unknown-run",
    )
    groups: OrderedDict[str, list[tuple[int, dict[str, Any]]]] = OrderedDict()
    prefix: list[tuple[int, dict[str, Any]]] = []
    current: str | None = None
    for order, event in indexed:
        if event.get("task_id"):
            current = str(event["task_id"])
            groups.setdefault(current, [])
            if prefix and len(groups) == 1:
                groups[current].extend(prefix)
                prefix.clear()
        if current is None:
            prefix.append((order, event))
        else:
            groups[current].append((order, event))
    if not groups and indexed:
        groups["task-unknown"] = indexed
    return tuple(_project_task(run_id, task_id, rows) for task_id, rows in groups.items())


def _project_task(
    run_id: str,
    task_id: str,
    rows: list[tuple[int, dict[str, Any]]],
) -> TaskTrace:
    model_starts: dict[Any, tuple[int, dict[str, Any]]] = {}
    model_ends: dict[Any, tuple[int, dict[str, Any]]] = {}
    tool_uses: dict[str, tuple[int, dict[str, Any]]] = {}
    tool_results: dict[str, tuple[int, dict[str, Any]]] = {}
    permissions: dict[str, dict[str, Any]] = {}
    observations: list[Observation] = []
    status = "unknown"
    success: bool | None = None
    repairs: int | None = None

    for order, event in rows:
        event_type = event.get("type")
        turn_id = event.get("turn_id")
        call_id = _id(event.get("tool_call_id"))
        if event_type == "model_call_start" and turn_id is not None:
            model_starts[turn_id] = (order, event)
        elif event_type in MODEL_END_EVENTS and turn_id is not None:
            model_ends[turn_id] = (order, event)
        elif event_type == "tool_use" and call_id:
            tool_uses[call_id] = (order, event)
        elif event_type == "tool_result" and call_id:
            tool_results[call_id] = (order, event)
        elif event_type == "permission_decision":
            if call_id:
                permissions[call_id] = event
            observations.append(_lifecycle_observation(order, event))
        elif event_type == "test_result":
            observations.append(_verification_observation(order, event))
        elif event_type in LIFECYCLE_EVENTS:
            observations.append(_lifecycle_observation(order, event))

        if event_type == "task_transition" and event.get("after"):
            status = str(event["after"])
        elif event_type == "final_response":
            success = _bool(event.get("success"))
        elif event_type == "stop":
            status = str(event.get("task_status") or status)
            success = _bool(event.get("success"))
            repairs = _int(event.get("repair_attempts"))

    for sequence, turn_id in enumerate(_pair_keys(model_starts, model_ends), start=1):
        observations.append(
            _model_observation(
                sequence,
                turn_id,
                model_starts.get(turn_id),
                model_ends.get(turn_id),
            )
        )
    for call_id in _pair_keys(tool_uses, tool_results):
        observations.append(
            _tool_observation(
                call_id,
                tool_uses.get(call_id),
                tool_results.get(call_id),
                permissions.get(call_id),
            )
        )
    observations.sort(key=lambda item: item.order)

    first = rows[0][1] if rows else {}
    last = rows[-1][1] if rows else {}
    metadata = _clean(
        {
            "run_id": run_id,
            "task_id": task_id,
            "task_status": status,
            "runtime_success": success,
            "repair_attempts": repairs,
            "source_event_count": len(rows),
            "source_first_step": first.get("step"),
            "source_last_step": last.get("step"),
            "source_start_ts": first.get("ts_iso"),
            "source_end_ts": last.get("ts_iso"),
            "source_duration_ms": _elapsed_duration(first, last),
        }
    )
    return TaskTrace(run_id, task_id, metadata, tuple(observations))


def _model_observation(
    sequence: int,
    turn_id: Any,
    start: tuple[int, dict[str, Any]] | None,
    end: tuple[int, dict[str, Any]] | None,
) -> Observation:
    order, first = start or end or (0, {})
    _, last = end or start or (order, first)
    end_type = last.get("type")
    status = {
        "model_call_end": "completed",
        "model_call_error": "failed",
        "model_call_interrupted": "interrupted",
    }.get(end_type, "incomplete")
    metadata = _clean(
        {
            **_source(first),
            "source_end_step": last.get("step"),
            "turn_id": turn_id,
            "model_call_sequence": sequence,
            "status": status,
            "duration_ms": last.get("duration_ms") or _duration(first, last),
            "message_count": first.get("message_count"),
            "tool_schema_count": first.get("tool_schema_count"),
            "context_tokens": first.get("context_tokens"),
            "context_source": first.get("context_source"),
            "remaining_model_calls": first.get("remaining_model_calls"),
            "tool_call_count": last.get("tool_call_count"),
            "tool_names": last.get("tool_names")
            if isinstance(last.get("tool_names"), list)
            else None,
            "stop_reason": last.get("stop_reason"),
            "exception_type": last.get("exception_type"),
        }
    )
    usage = {
        name: value
        for name in TOKEN_FIELDS
        if isinstance((value := last.get(name)), int) and not isinstance(value, bool)
    }
    return Observation(
        order,
        "model-call",
        "generation",
        metadata,
        usage,
        "ERROR" if status == "failed" else "DEFAULT",
    )


def _tool_observation(
    call_id: str,
    use: tuple[int, dict[str, Any]] | None,
    result: tuple[int, dict[str, Any]] | None,
    permission: dict[str, Any] | None,
) -> Observation:
    order, first = use or result or (0, {})
    _, last = result or use or (order, first)
    result_meta = last.get("metadata") if isinstance(last.get("metadata"), dict) else {}
    name = str(first.get("tool") or last.get("tool") or "unknown-tool")
    ok = _bool(last.get("ok")) if result else None
    error_kind = _tool_error_kind(last, result_meta) if ok is False else None
    metadata = _clean(
        {
            **_source(first),
            "source_end_step": last.get("step"),
            "turn_id": first.get("turn_id") or last.get("turn_id"),
            "tool_call_id": call_id,
            "tool_name": name,
            "status": "succeeded" if ok is True else "failed" if ok is False else "incomplete",
            "duration_ms": _duration(first, last),
            "read_only": first.get("read_only"),
            "dangerous": first.get("dangerous"),
            "error_kind": error_kind,
            "blocked_by": result_meta.get("blocked_by"),
            "result_scope": result_meta.get("result_scope"),
            "mutation_outcome": result_meta.get("mutation_outcome"),
            "mcp_server_id": result_meta.get("mcp_server_id"),
            "mcp_remote_tool": result_meta.get("mcp_remote_tool"),
            "mcp_duration_ms": result_meta.get("mcp_duration_ms"),
            "mcp_search_result_count": result_meta.get("mcp_search_result_count"),
            "mcp_canonical_tool": _mcp_tool(first),
            "permission_behavior": permission.get("behavior") if permission else None,
            "permission_risk": permission.get("risk") if permission else None,
            "permission_reason": permission.get("decision_reason") if permission else None,
            "operation_kind": _operation(permission, "kind"),
            "operation_scope": _operation(permission, "scope_key"),
        }
    )
    return Observation(
        order,
        name,
        "tool",
        metadata,
        level="ERROR" if ok is False else "DEFAULT",
    )


def _verification_observation(order: int, event: dict[str, Any]) -> Observation:
    ok = _bool(event.get("ok"))
    metadata = _clean(
        {
            **_source(event),
            "tool_call_id": event.get("tool_call_id"),
            "command": event.get("command"),
            "purpose": event.get("purpose"),
            "status": "passed" if ok is True else "failed" if ok is False else "unknown",
            "verification_level": event.get("verification_level"),
            "mutation_version": event.get("mutation_version"),
            "repair_attempt": event.get("repair_attempt"),
        }
    )
    return Observation(
        order,
        "verification",
        "span",
        metadata,
        level="ERROR" if ok is False else "DEFAULT",
    )


def _lifecycle_observation(order: int, event: dict[str, Any]) -> Observation:
    event_type = str(event.get("type"))
    metadata = {**_source(event)}
    if event_type == "plan_transition":
        before = event.get("before") if isinstance(event.get("before"), dict) else {}
        after = event.get("after") if isinstance(event.get("after"), dict) else {}
        metadata.update(
            _clean(
                {
                    "action": event.get("action"),
                    "before_phase": before.get("phase"),
                    "after_phase": after.get("phase"),
                    "before_execution_path": before.get("execution_path"),
                    "after_execution_path": after.get("execution_path"),
                    "version": after.get("version"),
                    "approved_version": after.get("approved_version"),
                    "approval_source": after.get("approval_source"),
                }
            )
        )
    elif event_type == "permission_decision":
        metadata.update(
            _pick(
                event,
                "tool_call_id",
                "tool",
                "phase",
                "behavior",
                "risk",
                "decision_reason",
                "terminal_on_deny",
            )
        )
        metadata["operation_kind"] = _operation(event, "kind")
        metadata["operation_scope"] = _operation(event, "scope_key")
    elif event_type == "task_transition":
        metadata.update(_pick(event, "before", "after", "trigger", "waiting_reason", "plan_phase"))
    elif event_type in CONTEXT_EVENTS:
        metadata.update(_pick(event, *CONTEXT_FIELDS))
    elif event_type in {"tool_progress_retry", "tool_progress_stalled"}:
        metadata.update(_pick(event, *PROGRESS_FIELDS))
    elif event_type == "final_response":
        metadata.update(_pick(event, "success", "message_count"))
    elif event_type == "run_aborted":
        metadata.update(_pick(event, "reason", "exception_type"))
    metadata = _clean(metadata)
    name = (
        "completed"
        if event_type == "final_response" and event.get("success") is True
        else "failed"
        if event_type == "final_response" and event.get("success") is False
        else event_type.replace("_", "-")
    )
    error = event_type in {"context_rebase_failure", "model_context_overflow", "run_aborted"}
    warning = event_type == "permission_decision" and event.get("behavior") == "deny"
    return Observation(
        order,
        name,
        "span",
        metadata,
        level="ERROR" if error else "WARNING" if warning else "DEFAULT",
    )


def export_tasks(tasks: Iterable[TaskTrace]) -> list[str]:
    config = _langfuse_config()
    try:
        from langfuse import Langfuse, propagate_attributes
    except ImportError as exc:
        raise ExportError(
            'Langfuse is not installed. Run: pip install -e ".[observability]"'
        ) from exc

    client = Langfuse(
        public_key=config["LANGFUSE_PUBLIC_KEY"],
        secret_key=config["LANGFUSE_SECRET_KEY"],
        base_url=config["LANGFUSE_BASE_URL"],
    )
    trace_ids: list[str] = []
    try:
        for task in tasks:
            trace_id = client.create_trace_id(seed=f"{task.run_id}:{task.task_id}")
            trace_ids.append(trace_id)
            root_error = (
                task.metadata.get("runtime_success") is False
                or task.metadata.get("task_status") == "failed"
            )
            with client.start_as_current_observation(
                name=TRACE_NAME,
                as_type="agent",
                trace_context={"trace_id": trace_id},
                metadata=task.metadata,
                level="ERROR" if root_error else "DEFAULT",
            ):
                with propagate_attributes(
                    trace_name=TRACE_NAME,
                    session_id=task.run_id,
                    metadata={"run_id": task.run_id, "task_id": task.task_id},
                    tags=["offline-trace-export"],
                ):
                    for observation in task.observations:
                        kwargs = {
                            "name": observation.name,
                            "as_type": observation.kind,
                            "metadata": observation.metadata,
                            "level": observation.level,
                        }
                        if observation.usage_details:
                            kwargs["usage_details"] = observation.usage_details
                        with client.start_as_current_observation(**kwargs):
                            pass
        client.flush()
    finally:
        client.shutdown()
    return trace_ids


def _langfuse_config() -> dict[str, str]:
    load_dotenv(REPO_ROOT / ".env", override=False)
    names = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_BASE_URL")
    config = {name: os.environ.get(name, "").strip() for name in names}
    missing = [name for name, value in config.items() if not value]
    if missing:
        raise ExportError("Missing Langfuse configuration: " + ", ".join(missing))
    return config


def _resolve_trace_path(run_id: str | None, trace: str | None) -> Path:
    if bool(run_id) == bool(trace):
        raise ExportError("Provide exactly one of <run_id> or --trace PATH.")
    path = (
        Path(trace).expanduser()
        if trace
        else Path.cwd() / ".agent" / "runs" / str(run_id) / "trace.jsonl"
    ).resolve()
    if not path.is_file():
        raise ExportError(f"Trace file not found: {path}")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export an existing trace.jsonl to Langfuse without running the Agent."
    )
    parser.add_argument(
        "run_id", nargs="?", help="Run id under .agent/runs in the current directory."
    )
    parser.add_argument("--trace", help="Explicit path to a trace.jsonl file.")
    args = parser.parse_args(argv)
    try:
        trace_path = _resolve_trace_path(args.run_id, args.trace)
        events, skipped = read_trace(trace_path)
        tasks = project_trace(events, run_id_hint=trace_path.parent.name)
        if not tasks:
            raise ExportError("Trace contains no task-scoped events.")
        trace_ids = export_tasks(tasks)
    except ExportError as exc:
        print(f"export failed: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"export failed: {exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 1

    count = sum(len(task.observations) for task in tasks)
    print(
        f"exported {len(tasks)} task trace(s), {count} observation(s), "
        f"skipped {skipped} malformed line(s)"
    )
    for task, trace_id in zip(tasks, trace_ids, strict=True):
        print(f"{task.run_id}/{task.task_id}: {trace_id}")
    return 0


def _source(event: dict[str, Any]) -> dict[str, Any]:
    return _clean(
        {
            "source_event_id": event.get("event_id"),
            "source_step": event.get("step"),
            "source_ts": event.get("ts_iso"),
            "source_elapsed_ms": event.get("elapsed_ms"),
            "turn_id": event.get("turn_id"),
        }
    )


def _duration(first: dict[str, Any], last: dict[str, Any]) -> float | None:
    try:
        return round(max(float(last["ts"]) - float(first["ts"]), 0) * 1000, 3)
    except (KeyError, TypeError, ValueError):
        return None


def _elapsed_duration(first: dict[str, Any], last: dict[str, Any]) -> float | None:
    try:
        return round(max(float(last["elapsed_ms"]) - float(first["elapsed_ms"]), 0), 3)
    except (KeyError, TypeError, ValueError):
        return None


def _pair_keys(*mappings: dict[Any, tuple[int, dict[str, Any]]]) -> list[Any]:
    keys = set().union(*mappings)
    return sorted(
        keys,
        key=lambda key: min(mapping.get(key, (sys.maxsize, {}))[0] for mapping in mappings),
    )


def _mcp_tool(event: dict[str, Any]) -> str | None:
    if event.get("tool") != "mcp_tool_call":
        return None
    for field_name in ("normalized_args", "args"):
        values = event.get(field_name)
        if isinstance(values, dict) and isinstance(values.get("tool"), str):
            return values["tool"]
    return None


def _tool_error_kind(event: dict[str, Any], metadata: dict[str, Any]) -> str:
    for key in ("mcp_error_kind", "blocked_by"):
        if metadata.get(key):
            return str(metadata[key])
    for key in ("validation_error", "unknown_tool", "unavailable_tool", "tool_exception"):
        if metadata.get(key):
            return key
    return "tool_error" if event.get("error") else "unknown_error"


def _operation(event: dict[str, Any] | None, field_name: str) -> str | None:
    operation = event.get("operation") if event else None
    if not isinstance(operation, dict) or not operation.get(field_name):
        return None
    return str(operation[field_name])


def _pick(source: dict[str, Any], *names: str) -> dict[str, Any]:
    return {name: source[name] for name in names if source.get(name) is not None}


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in values.items() if value is not None}


def _id(value: Any) -> str | None:
    return str(value) if value not in {None, ""} else None


def _bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


MODEL_END_EVENTS = {"model_call_end", "model_call_error", "model_call_interrupted"}
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)
CONTEXT_EVENTS = {
    "context_measurement",
    "context_rebase",
    "context_rebase_failure",
    "context_recovery",
    "context_recovery_skipped",
    "model_context_overflow",
    "tool_result_budget",
}
CONTEXT_FIELDS = (
    "reason",
    "source",
    "failure_reason",
    "generation",
    "generation_before",
    "generation_after",
    "local_input_tokens",
    "local_input_tokens_before",
    "local_input_tokens_after",
    "pressure_input_tokens",
    "saved_tokens",
    "compacted",
    "recovered",
    "attempt",
    "attempts",
    "replaced_results",
)
PROGRESS_FIELDS = (
    "reason",
    "repeat_count",
    "saturated_invalid_calls",
    "output_budget_saturated",
    "output_tokens",
    "max_output_tokens",
    "tools",
)
LIFECYCLE_EVENTS = {
    "plan_transition",
    "task_transition",
    "final_response",
    "run_aborted",
    "tool_progress_retry",
    "tool_progress_stalled",
    *CONTEXT_EVENTS,
}


if __name__ == "__main__":
    raise SystemExit(main())
