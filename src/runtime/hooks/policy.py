from __future__ import annotations

from runtime.observability.text_preview import head_tail_preview
from tools.base import ToolResult
from runtime.task import TaskStatus, is_terminal_task_status


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
    metadata["track_mutation_failure"] = False

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
            creation_reason="large_output",
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
    context.trace.log(
        {
            "type": "artifact_persisted",
            "tool_call_id": tool_call.id,
            "tool": tool_call.name,
            "artifact_id": reference.artifact_id,
            "chars_persisted": reference.chars,
            "creation_reason": reference.creation_reason,
        }
    )

    return None


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
    context.abort_reason = "permission_denied"
    context.final_text = _permission_cancelled_summary(decision, context)
    transition = getattr(context, "transition_task", None)
    if callable(transition) and not is_terminal_task_status(
        getattr(context, "task_status", TaskStatus.IDLE)
    ):
        transition(TaskStatus.CANCELLED, trigger="permission_denied")
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
