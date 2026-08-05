from __future__ import annotations

from runtime.permission import BashRisk
from runtime.console import print_tool_call
from runtime.readable_trace_writer import ReadableTraceWriter
from runtime.text_preview import head_tail_preview
from tools.base import ToolResult
from tools.bash import DEFAULT_TIMEOUT_SECONDS
from tools.read_file import DEFAULT_LIMIT as READ_FILE_DEFAULT_LIMIT


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


def pre_tool_trace_hook(tool_call, tool, context) -> None:
    context.trace.log(
        {
            "type": "tool_use",
            "turn_id": _turn_id(context),
            "tool_call_id": getattr(tool_call, "id", None),
            "tool": tool_call.name,
            "args": tool_call.arguments,
            "normalized_args": _normalized_args(tool_call.name, tool_call.arguments, context),
            "read_only": getattr(tool, "read_only", False),
            "dangerous": getattr(tool, "dangerous", False),
        }
    )

    print_tool_call(tool_call.name, tool_call.arguments)

    return None


def permission_hook(tool_call, tool, context):
    decision = context.permission_gate.check(
        tool=tool,
        args=tool_call.arguments,
        context=context,
    )
    _log_permission_decision(tool_call, decision, context, phase="check")

    resolved = context.permission_gate.resolve(decision, tool, tool_call.arguments, context)
    if _permission_changed(decision, resolved):
        _log_permission_decision(tool_call, resolved, context, phase="resolved")

    if resolved.behavior == "allow":
        return None

    if resolved.behavior == "deny" and resolved.terminal_on_deny:
        _cancel_task_for_terminal_deny(tool_call, resolved, context)

    metadata = {
        "denied": True,
        "permission_denied": True,
        "tool": tool_call.name,
        "blocked_by": "permission_hook",
        "permission_behavior": resolved.behavior,
        "risk": resolved.risk,
        "proposed_scope": resolved.proposed_scope,
        "terminal_on_deny": resolved.terminal_on_deny,
        "decision_reason": resolved.decision_reason,
    }
    if resolved.operation is not None:
        metadata["operation"] = resolved.operation.to_metadata()
    metadata.update(resolved.metadata)

    return ToolResult(
        ok=False,
        content=resolved.message,
        error=resolved.message,
        metadata=metadata,
    )


def secret_redaction_hook(tool_call, tool, result, context) -> None:
    redactor = getattr(context, "redactor", None)
    if redactor is None:
        return None

    result.content, content_replacements = redactor.redact_with_count(result.content or "")
    error_replacements = 0
    if result.error:
        result.error, error_replacements = redactor.redact_with_count(result.error)
    result.metadata = redactor.redact_value(result.metadata)

    replacements = content_replacements + error_replacements
    if replacements:
        result.metadata["secret_redacted"] = True
        result.metadata["secret_redaction_count"] = replacements
    return None


def large_output_hook(tool_call, tool, result, context) -> None:
    if result.artifact_id:
        _register_artifact_reference(context, tool_call.id, result.artifact_id)
    if not result.content:
        return None

    max_chars = context.config.max_tool_result_chars
    if len(result.content) <= max_chars:
        return None

    full_content = result.content
    try:
        reference = context.artifacts.persist(
            tool_call_id=tool_call.id,
            content=full_content,
        )
    except (OSError, UnicodeError) as exc:
        result.content = _bounded_output_message(
            prefix=(
                "<persisted-output-error>\n"
                f"Output too large ({len(full_content)} chars) and artifact persistence failed.\n"
                "Only this preview is available:\n"
            ),
            content=full_content,
            suffix="\n</persisted-output-error>",
            max_chars=max_chars,
        )
        result.metadata.update(
            {
                "persisted": False,
                "artifact_persist_failed": True,
                "original_chars": len(full_content),
                "truncated": True,
            }
        )
        context.trace.log(
            {
                "type": "artifact_persist_error",
                "tool_call_id": getattr(tool_call, "id", None),
                "exception_type": exc.__class__.__name__,
                "exception": str(exc)[:500],
            }
        )
        return None

    result.artifact_id = reference.artifact_id
    result.artifact_path = str(reference.path)
    _register_artifact_reference(context, tool_call.id, reference.artifact_id)
    result.content = _bounded_output_message(
        prefix=(
            "<persisted-output>\n"
            f"Output too large ({len(full_content)} chars).\n"
            f"artifact_id: {reference.artifact_id}\n"
            "Use read_artifact with this id for additional slices.\n"
            "Preview (head and tail):\n"
        ),
        content=full_content,
        suffix="\n</persisted-output>",
        max_chars=max_chars,
    )

    result.metadata["persisted"] = True
    result.metadata["original_chars"] = len(full_content)
    result.metadata["artifact_id"] = reference.artifact_id
    result.metadata["truncated"] = True

    return None


def record_tool_budget_hook(tool_call, tool, result, context) -> None:
    budget = context.tool_budget
    name = tool_call.name

    if name == "read_file":
        budget.read_file_calls += 1
    elif name == "grep":
        budget.grep_calls += 1
    elif name == "list_dir":
        budget.list_dir_calls += 1
    elif name == "bash":
        budget.bash_calls += 1

    budget.chars_returned += len(result.content or "")

    if result.metadata.get("truncated"):
        budget.truncated_results += 1

    return None


def mutation_result_hook(tool_call, tool, result, context) -> None:
    if tool.name != "bash":
        return None
    if result.metadata.get("denied") or result.metadata.get("blocked_by_hook"):
        return None

    command = str(tool_call.arguments.get("command", ""))
    decision = context.permission_gate.risk_classifier.classify_bash(command)
    if decision.risk != BashRisk.FILE_WRITE_VIA_BASH and "file_write" not in decision.effects:
        return None

    recorded_paths = []
    for requested_path in decision.target_paths:
        try:
            target = context.safe_path(requested_path)
            relative = target.relative_to(context.repo_path)
        except (OSError, ValueError):
            continue
        if target.exists() and not target.is_file():
            continue
        if not target.exists() and not result.ok:
            continue
        normalized_path = relative.as_posix()
        context.record_changed_file(normalized_path)
        recorded_paths.append(normalized_path)

    if not recorded_paths:
        context.record_mutation()

    result.metadata["mutation_recorded"] = True
    result.metadata["mutation_paths"] = recorded_paths
    result.metadata["mutation_version"] = context.mutation_version
    return None


def failure_history_hook(tool_call, tool, result, context) -> None:
    if result.ok or result.metadata.get("denied"):
        return None
    failures = getattr(context, "task_tool_failures", None)
    if failures is None:
        return None
    failures.append(
        {
            "turn_id": _turn_id(context),
            "tool": tool_call.name,
            "error": result.error or "tool failed",
            "output_preview": head_tail_preview(result.content or "", 300),
        }
    )
    del failures[:-20]
    return None


def mutation_outcome_hook(tool_call, tool, result, context) -> None:
    operation_kind = _mutation_operation_kind(tool_call, tool, result, context)
    if operation_kind not in {"fs.write", "fs.delete"}:
        return None

    had_failure = getattr(context, "task_unresolved_mutation_failure", False)
    context.task_unresolved_mutation_failure = not result.ok
    result.metadata["mutation_outcome"] = "succeeded" if result.ok else "failed"
    if result.ok and had_failure:
        result.metadata["mutation_failure_recovered"] = True
    return None


def test_result_hook(tool_call, tool, result, context) -> None:
    if tool.name != "bash":
        return None

    if result.metadata.get("denied") or result.metadata.get("blocked_by_hook"):
        return None

    command = str(tool_call.arguments.get("command", ""))
    is_test_command = _is_test_command(command)
    is_verification_command = _is_verification_command(tool_call, result)

    if (
        (is_test_command or is_verification_command)
        and _mutation_operation_kind(tool_call, tool, result, context)
        in {"fs.write", "fs.delete"}
    ):
        _record_verification_ignored(
            tool_call,
            result,
            context,
            command=command,
            reason="explicit_mutation_command",
        )
        return None

    if is_verification_command and _is_discovery_command(command):
        _record_verification_ignored(
            tool_call,
            result,
            context,
            command=command,
            reason="read_only_discovery_command",
        )
        return None

    if not is_test_command and not is_verification_command:
        return None

    test_result = {
        "command": command,
        "ok": result.ok,
        "error": result.error,
        "output_preview": result.content[:2000],
        "metadata": result.metadata,
        "mutation_version": getattr(context, "mutation_version", 0),
        "verification_level": _verification_level(command),
    }
    context.last_test_result = test_result
    context.task_test_result = test_result
    context.task_verification_version = test_result["mutation_version"]

    result.metadata["verification_command"] = True
    if is_test_command:
        result.metadata["test_command"] = True
    context.trace.log(
        {
            "type": "test_result",
            "turn_id": _turn_id(context),
            "tool_call_id": getattr(tool_call, "id", None),
            "command": command,
            "ok": result.ok,
            "error": result.error,
            "purpose": _verification_purpose(tool_call, result),
            "mutation_version": test_result["mutation_version"],
            "verification_level": test_result["verification_level"],
        }
    )

    return None


def post_tool_trace_hook(tool_call, tool, result, context) -> None:
    context.trace.log(
        {
            "type": "tool_result",
            "turn_id": _turn_id(context),
            "tool_call_id": getattr(tool_call, "id", None),
            "tool": tool_call.name,
            "ok": result.ok,
            "error": result.error,
            "output_preview": result.content[:500] if result.content else "",
            "artifact_id": result.artifact_id,
            "artifact_path": result.artifact_path,
            "metadata": result.metadata,
        }
    )

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


def _bounded_output_message(
    *,
    prefix: str,
    content: str,
    suffix: str,
    max_chars: int,
) -> str:
    preview_budget = max(max_chars - len(prefix) - len(suffix), 0)
    rendered = f"{prefix}{head_tail_preview(content, preview_budget)}{suffix}"
    return rendered[:max_chars]


def _register_artifact_reference(context, tool_call_id: str, artifact_id: str) -> None:
    artifact_map = getattr(context, "tool_result_artifacts", None)
    if artifact_map is None:
        artifact_map = {}
        context.tool_result_artifacts = artifact_map
    artifact_map[str(tool_call_id)] = artifact_id


def _turn_id(context) -> int:
    current_turn_id = getattr(context, "current_turn_id", None)
    if current_turn_id is not None:
        return int(current_turn_id)
    return int(getattr(context, "turn_count", 0) + 1)


def _cancel_task_for_terminal_deny(tool_call, decision, context) -> None:
    scope = decision.proposed_scope or (
        decision.operation.scope_key if decision.operation is not None else None
    )
    if scope and _should_cache_denied_scope(decision):
        context.denied_permission_scopes.add(scope)

    context.finished = True
    context.success = False
    context.final_text = _permission_cancelled_summary(decision, context)
    context.trace.log(
        {
            "type": "task_cancelled",
            "turn_id": _turn_id(context),
            "tool_call_id": getattr(tool_call, "id", None),
            "tool": tool_call.name,
            "scope": scope,
            "operation": decision.operation.to_metadata() if decision.operation else None,
            "decision": {
                "behavior": decision.behavior,
                "risk": decision.risk,
                "message": decision.message,
                "proposed_scope": decision.proposed_scope,
                "terminal_on_deny": decision.terminal_on_deny,
                "decision_reason": decision.decision_reason,
            },
        }
    )


def _should_cache_denied_scope(decision) -> bool:
    if decision.decision_reason in {
        "user_deny",
        "deny_rule",
        "path_escape",
        "access_policy_read",
        "access_policy_write",
        "access_policy_delete",
        "bash_destructive",
    }:
        return True

    return decision.risk in {
        "protected_read",
        "protected_write",
        "protected_delete",
        "destructive",
    }


def _permission_cancelled_summary(decision, context) -> str:
    scope = decision.proposed_scope or (
        decision.operation.scope_key if decision.operation is not None else "permission request"
    )
    changed_files = sorted(getattr(context, "task_changed_files", set()))
    changed_summary = (
        "\n".join(f"- {path}" for path in changed_files)
        if changed_files
        else "- None"
    )
    test_result = getattr(context, "task_test_result", None)
    if test_result:
        check_summary = (
            f"- `{test_result.get('command', 'unknown')}`: "
            f"{'passed' if test_result.get('ok') else 'failed'}"
        )
    else:
        check_summary = "- Not run."

    return (
        "Summary\n"
        f"- Permission denied for `{scope}`; this operation was cancelled.\n"
        "- The denied operation made no additional file changes.\n\n"
        "Changed files\n"
        f"{changed_summary}\n\n"
        "Checks run\n"
        f"{check_summary}\n\n"
        "Risks\n"
        "- Earlier task changes, if listed above, remain in the worktree."
    )


def _is_test_command(command: str) -> bool:
    normalized = command.lower()
    return "pytest" in normalized or "unittest" in normalized or "npm test" in normalized


def _is_discovery_command(command: str) -> bool:
    stripped = command.strip().lower()
    discovery_prefixes = (
        "find ",
        "git status",
        "git diff",
        "git log",
        "ls",
        "dir",
        "tree",
        "pwd",
        "rg ",
        "grep ",
    )
    return any(stripped == prefix.strip() or stripped.startswith(prefix) for prefix in discovery_prefixes)


def _is_verification_command(tool_call, result) -> bool:
    return _verification_purpose(tool_call, result) == "verify"


def _verification_level(command: str) -> str:
    normalized = command.lower()
    if any(value in normalized for value in ("playwright", "selenium", "cypress")):
        return "integration"
    if _is_test_command(command):
        return "test_suite"
    if any(
        value in normalized
        for value in ("--check", "py_compile", "compileall", "sh -n", "bash -n", "ruff check")
    ):
        return "static"
    return "custom"


def _mutation_operation_kind(tool_call, tool, result, context) -> str | None:
    operation_metadata = result.metadata.get("operation")
    if isinstance(operation_metadata, dict):
        kind = operation_metadata.get("kind")
        if isinstance(kind, str):
            return kind

    if result.metadata.get("mutation_recorded"):
        return "fs.write"

    classify = getattr(tool, "classify_operation", None)
    if not callable(classify):
        return None

    try:
        operation = classify(getattr(tool_call, "arguments", {}), context)
    except Exception:
        return None
    return getattr(operation, "kind", None)


def _record_verification_ignored(
    tool_call,
    result,
    context,
    *,
    command: str,
    reason: str,
) -> None:
    result.metadata["verification_ignored"] = True
    result.metadata["verification_ignored_reason"] = reason
    context.trace.log(
        {
            "type": "verification_ignored",
            "turn_id": _turn_id(context),
            "tool_call_id": getattr(tool_call, "id", None),
            "command": command,
            "reason": reason,
            "purpose": _verification_purpose(tool_call, result),
        }
    )


def _verification_purpose(tool_call, result) -> str | None:
    values = [
        getattr(tool_call, "arguments", {}).get("purpose"),
        result.metadata.get("purpose"),
    ]
    for value in values:
        if value is None:
            continue
        purpose = str(value).strip().lower()
        if purpose:
            return purpose
    return None


def _normalized_args(tool_name: str, args: dict, context) -> dict:
    normalized = dict(args or {})

    if tool_name == "list_dir":
        normalized.setdefault("path", ".")
    elif tool_name == "grep":
        normalized.setdefault("path", ".")
    elif tool_name == "read_file":
        normalized.setdefault("offset", 0)
        normalized.setdefault("limit", READ_FILE_DEFAULT_LIMIT)
    elif tool_name == "bash":
        normalized.setdefault("timeout", DEFAULT_TIMEOUT_SECONDS)
        if "input" not in normalized:
            normalized["stdin"] = "devnull"

    return normalized


def _log_permission_decision(tool_call, decision, context, phase: str) -> None:
    context.trace.log(
        {
            "type": "permission_decision",
            "phase": phase,
            "turn_id": _turn_id(context),
            "tool_call_id": getattr(tool_call, "id", None),
            "tool": tool_call.name,
            "behavior": decision.behavior,
            "risk": decision.risk,
            "message": decision.message,
            "proposed_scope": decision.proposed_scope,
            "terminal_on_deny": decision.terminal_on_deny,
            "decision_reason": decision.decision_reason,
            "operation": decision.operation.to_metadata() if decision.operation else None,
            "metadata": decision.metadata,
        }
    )


def _permission_changed(first, second) -> bool:
    return (
        first.behavior != second.behavior
        or first.risk != second.risk
        or first.message != second.message
        or first.proposed_scope != second.proposed_scope
        or first.metadata != second.metadata
        or first.terminal_on_deny != second.terminal_on_deny
        or first.decision_reason != second.decision_reason
    )
