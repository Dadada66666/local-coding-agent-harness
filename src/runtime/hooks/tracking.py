from __future__ import annotations

from runtime.observability.console import print_tool_call, print_tool_validation_failure
from runtime.observability.text_preview import head_tail_preview
from runtime.security import BashRisk
from tools.bash import DEFAULT_TIMEOUT_SECONDS
from tools.read_file import DEFAULT_LIMIT as READ_FILE_DEFAULT_LIMIT


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
    if result.metadata.get("track_mutation_failure") is False:
        result.metadata["mutation_outcome"] = "not_executed"
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

    if result.metadata.get("validation_error") or result.metadata.get("unknown_tool"):
        print_tool_validation_failure(tool_call.name, result.error or "invalid tool call")

    return None


def _turn_id(context) -> int:
    current_turn_id = getattr(context, "current_turn_id", None)
    if current_turn_id is not None:
        return int(current_turn_id)
    return int(getattr(context, "turn_count", 0) + 1)


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
