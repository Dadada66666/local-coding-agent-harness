from __future__ import annotations

import json

from agent.loop import AgentLoop
from agent.messages import ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.plan import PlanPolicy
from runtime.plan.capabilities import READ_ONLY_INSPECTION_TOOLS


HISTORY_TOOL_NAMES = {
    "history_list_windows",
    "history_list_items",
    "history_search_contents",
    "history_read_item",
}


def make_context(tmp_path, *, plan_policy=PlanPolicy.OFF):
    runtime = build_runtime()
    runner = AgentLoop(
        model_client=object(),
        runtime=runtime,
        repo_path=tmp_path,
        permission_mode="read_only",
        config=RunConfig(permission_mode="read_only", plan_policy=plan_policy),
    )
    return runner.create_context("inspect", include_initial_message=True), runtime


def test_history_tools_have_frozen_names_and_read_only_capabilities(tmp_path) -> None:
    context, runtime = make_context(tmp_path, plan_policy=PlanPolicy.REQUIRED)

    assert HISTORY_TOOL_NAMES <= set(runtime.tool_registry.all_names())
    assert HISTORY_TOOL_NAMES <= READ_ONLY_INSPECTION_TOOLS
    assert HISTORY_TOOL_NAMES <= set(runtime.tool_registry.names(context))
    for name in HISTORY_TOOL_NAMES:
        tool = runtime.tool_registry.get(name)
        assert tool is not None
        assert tool.read_only is True
        assert "." not in tool.name


def test_history_windows_and_items_are_stable_and_bounded(tmp_path) -> None:
    context, runtime = make_context(tmp_path)
    context.add_assistant_message(
        {"role": "assistant", "content": [{"type": "text", "text": "first finding"}]}
    )
    context.mark_context_changed()
    context.add_user_message({"role": "user", "content": "second epoch"})

    windows = runtime.tool_registry.get("history_list_windows").call({}, context)
    payload = json.loads(windows.content)

    assert windows.ok is True
    assert [item["generation"] for item in payload["windows"]] == [1, 0]
    assert payload["windows"][0]["current"] is True
    assert payload["windows"][1]["closed"] is True

    old_window = payload["windows"][1]["window_id"]
    listing = runtime.tool_registry.get("history_list_items").call(
        {"window_id": old_window, "limit": 20, "max_chars_per_item": 2_000},
        context,
    )
    items = json.loads(listing.content)["items"]
    assert [item["item_id"] for item in items] == [
        context.audit_item_id(0),
        context.audit_item_id(1),
    ]
    assert all(len(item["preview"]) <= 2_000 for item in items)


def test_history_literal_search_and_read_append_only(tmp_path) -> None:
    context, runtime = make_context(tmp_path)
    context.add_assistant_message(
        {
            "role": "assistant",
            "content": [{"type": "text", "text": "CaseSensitiveNeedle"}],
        }
    )
    audit_before = list(context.conversation_messages)

    search = runtime.tool_registry.get("history_search_contents").call(
        {"query": "CaseSensitiveNeedle"},
        context,
    )
    matches = json.loads(search.content)["matches"]
    assert len(matches) == 1
    item_id = matches[0]["item_id"]

    read = runtime.tool_registry.get("history_read_item").call(
        {"item_id": item_id, "offset_chars": 0, "limit_chars": 20_000},
        context,
    )
    assert read.ok is True
    assert "CaseSensitiveNeedle" in read.content
    assert context.conversation_messages == audit_before

    context.add_tool_results([("history-call", read.content, False)])
    assert context.conversation_messages[:-1] == audit_before
    assert context.conversation_messages[-1]["content"][0]["tool_use_id"] == "history-call"


def test_history_errors_are_deterministic_and_non_terminal(tmp_path) -> None:
    context, runtime = make_context(tmp_path)
    result = runtime.tool_registry.get("history_read_item").call(
        {"item_id": "missing", "offset_chars": 0, "limit_chars": 100},
        context,
    )

    assert result.ok is False
    assert result.metadata["history_error"] == "unknown_item_id"
    assert context.finished is False


def test_history_results_never_exceed_ten_thousand_estimated_tokens(tmp_path) -> None:
    context, runtime = make_context(tmp_path)
    context.add_user_message({"role": "user", "content": "界" * 30_000})
    item_id = context.audit_item_id(len(context.conversation_messages) - 1)

    result = runtime.tool_registry.get("history_read_item").call(
        {"item_id": item_id, "offset_chars": 0, "limit_chars": 20_000},
        context,
    )

    assert result.ok is True
    assert result.metadata["estimated_tokens"] <= 10_000
    assert result.metadata["complete"] is False


def test_history_recovery_uses_ordinary_executor_path(tmp_path) -> None:
    context, runtime = make_context(tmp_path)
    call = ToolCall(
        "history-call",
        "history_read_item",
        {"item_id": context.audit_item_id(0), "limit_chars": 100},
    )

    result = runtime.executor.execute(call, context)

    assert result.ok is True
    assert result.metadata["history_recovery"] is True
