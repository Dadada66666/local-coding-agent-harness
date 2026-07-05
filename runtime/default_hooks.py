from __future__ import annotations

from tools.base import ToolResult
from tools.bash import DEFAULT_TIMEOUT_SECONDS
from tools.read_file import DEFAULT_LIMIT as READ_FILE_DEFAULT_LIMIT


def user_prompt_submit_hook(task: str, context) -> None:
    context.trace.log(
        {
            "type": "user_prompt",
            "task": task,
            "workdir": str(context.repo_path),
        }
    )

    print(f"[run] {context.run_id}")
    print(f"[task] {task}")
    print(f"[workdir] {context.repo_path}")

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

    print(f"[tool] {tool_call.name} {tool_call.arguments}")

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

    metadata = {
        "denied": True,
        "permission_denied": True,
        "tool": tool_call.name,
        "blocked_by": "permission_hook",
        "permission_behavior": resolved.behavior,
        "risk": resolved.risk,
        "proposed_scope": resolved.proposed_scope,
    }
    metadata.update(resolved.metadata)

    return ToolResult(
        ok=False,
        content=resolved.message,
        error=resolved.message,
        metadata=metadata,
    )


def large_output_hook(tool_call, tool, result, context) -> None:
    if not result.content:
        return None

    max_chars = context.config.max_tool_result_chars
    if len(result.content) <= max_chars:
        return None

    full_content = result.content
    path = context.artifacts.persist(
        tool_call_id=tool_call.id,
        content=full_content,
    )

    result.artifact_path = path
    result.content = (
        "<persisted-output>\n"
        f"Full output saved to: {path}\n"
        "Preview:\n"
        f"{full_content[:2000]}\n"
        "</persisted-output>"
    )

    result.metadata["persisted"] = True
    result.metadata["original_chars"] = len(full_content)

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


def test_result_hook(tool_call, tool, result, context) -> None:
    if tool.name != "bash":
        return None

    if result.metadata.get("denied") or result.metadata.get("blocked_by_hook"):
        return None

    command = str(tool_call.arguments.get("command", ""))
    is_test_command = _is_test_command(command)
    is_verification_command = _is_verification_command(tool_call, result)

    if not is_test_command and not is_verification_command:
        return None

    context.last_test_result = {
        "command": command,
        "ok": result.ok,
        "error": result.error,
        "output_preview": result.content[:2000],
        "metadata": result.metadata,
    }

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
            "artifact_path": result.artifact_path,
            "metadata": result.metadata,
        }
    )

    return None


def stop_report_hook(context) -> None:
    report_path = context.report_writer.write(context)
    diff_path = context.diff_manager.write_patch(context)
    cost_path = context.cost_tracker.write(context)

    context.trace.log(
        {
            "type": "stop",
            "success": context.success,
            "report_path": str(report_path),
            "diff_path": str(diff_path),
            "cost_path": str(cost_path),
            "repair_attempts": context.repair_attempts,
        }
    )

    print(f"[report] {report_path}")
    print(f"[diff] {diff_path}")
    print(f"[cost] {cost_path}")

    return None


def _turn_id(context) -> int:
    return int(getattr(context, "current_turn_id", context.turn_count + 1))


def _is_test_command(command: str) -> bool:
    normalized = command.lower()
    return "pytest" in normalized or "unittest" in normalized or "npm test" in normalized


def _is_verification_command(tool_call, result) -> bool:
    return _verification_purpose(tool_call, result) == "verify"


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
    )
