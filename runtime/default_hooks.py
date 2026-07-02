from __future__ import annotations

from tools.base import ToolResult


def user_prompt_submit_hook(task: str, context) -> None:
    context.trace.log(
        {
            "type": "user_prompt",
            "task": task,
            "workdir": str(context.repo_path),
            "run_id": context.run_id,
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
            "tool": tool_call.name,
            "args": tool_call.arguments,
            "read_only": getattr(tool, "read_only", False),
            "dangerous": getattr(tool, "dangerous", False),
        }
    )

    print(f"[tool] {tool_call.name} {tool_call.arguments}")

    return None


def permission_hook(tool_call, tool, context):
    allowed, reason = context.permission_gate.check(
        tool=tool,
        args=tool_call.arguments,
        context=context,
    )

    if allowed:
        return None

    return ToolResult(
        ok=False,
        content=reason or "Permission denied.",
        error=reason,
        metadata={
            "denied": True,
            "tool": tool_call.name,
            "blocked_by": "permission_hook",
        },
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


def test_result_hook(tool_call, tool, result, context) -> None:
    if tool.name != "bash":
        return None

    command = str(tool_call.arguments.get("command", ""))
    is_test_command = "pytest" in command or "unittest" in command or "npm test" in command

    if not is_test_command:
        return None

    context.last_test_result = {
        "command": command,
        "ok": result.ok,
        "error": result.error,
        "output_preview": result.content[:2000],
        "metadata": result.metadata,
    }

    result.metadata["test_command"] = True
    context.trace.log(
        {
            "type": "test_result",
            "command": command,
            "ok": result.ok,
            "error": result.error,
        }
    )

    return None


def post_tool_trace_hook(tool_call, tool, result, context) -> None:
    context.trace.log(
        {
            "type": "tool_result",
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
            "run_id": context.run_id,
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

