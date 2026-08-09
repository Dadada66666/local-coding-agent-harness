from __future__ import annotations

from pathlib import Path

from agent.messages import ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.plan import ExecutionPath, PlanPhase, PlanPolicy
from runtime.session_factory import create_agent_session


def make_context(tmp_path: Path, policy: PlanPolicy):
    return create_agent_session(
        repo_path=tmp_path,
        task="plan the change",
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", plan_policy=policy),
        initial_messages=[],
        system_prompt="system",
        include_initial_message=False,
    )


def schema_names(runtime, context=None) -> set[str]:
    return {schema["name"] for schema in runtime.tool_registry.schemas(context)}


def test_plan_tool_discoverability_tracks_context_state(tmp_path) -> None:
    runtime = build_runtime()
    off = make_context(tmp_path / "off", PlanPolicy.OFF)
    auto = make_context(tmp_path / "auto", PlanPolicy.AUTO)
    required = make_context(tmp_path / "required", PlanPolicy.REQUIRED)

    assert "select_execution_mode" not in schema_names(runtime, off)
    assert "update_plan" not in schema_names(runtime, off)
    assert "select_execution_mode" in schema_names(runtime, auto)
    assert "update_plan" not in schema_names(runtime, auto)
    assert "select_execution_mode" not in schema_names(runtime, required)
    assert "update_plan" in schema_names(runtime, required)
    assert "resolve_plan_response" not in schema_names(runtime, required)
    assert "read_file" in schema_names(runtime, off)
    assert "write_file" in schema_names(runtime, required)
    assert "select_execution_mode" not in schema_names(runtime)


def test_select_plan_replaces_selector_with_update_tool(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.AUTO)

    result = runtime.executor.execute(
        ToolCall(
            "select",
            "select_execution_mode",
            {"mode": "plan", "reason": "multiple dependent modules"},
        ),
        context,
    )

    assert result.ok is True
    assert context.plan_state.execution_path is ExecutionPath.PLAN
    assert context.plan_state.phase is PlanPhase.PLANNING
    assert "select_execution_mode" not in schema_names(runtime, context)
    assert "update_plan" in schema_names(runtime, context)


def test_select_direct_hides_both_plan_tools(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.AUTO)
    runtime.executor.execute(
        ToolCall(
            "select",
            "select_execution_mode",
            {"mode": "direct", "reason": "single local change"},
        ),
        context,
    )

    assert "select_execution_mode" not in schema_names(runtime, context)
    assert "update_plan" not in schema_names(runtime, context)


def test_plan_tools_reject_unknown_fields_and_model_approval(tmp_path) -> None:
    runtime = build_runtime()
    auto = make_context(tmp_path / "auto", PlanPolicy.AUTO)
    required = make_context(tmp_path / "required", PlanPolicy.REQUIRED)

    selector = runtime.executor.execute(
        ToolCall(
            "bad-select",
            "select_execution_mode",
            {"mode": "direct", "reason": "small", "approved": True},
        ),
        auto,
    )
    updater = runtime.executor.execute(
        ToolCall(
            "bad-update",
            "update_plan",
            {"action": "submit", "approved": True},
        ),
        required,
    )

    assert selector.ok is False
    assert selector.metadata["validation_error"] is True
    assert updater.ok is False
    assert updater.metadata["validation_error"] is True
    assert required.plan_state.phase is PlanPhase.PLANNING


def test_update_plan_combines_replace_and_submission(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.REQUIRED)

    result = runtime.executor.execute(
        ToolCall(
            "plan",
            "update_plan",
            {
                "action": "replace_plan",
                "explanation": "Checked the real runtime modules",
                "steps": [
                    {
                        "id": "step-1",
                        "description": "Add the state controller",
                    }
                ],
                "submit": True,
            },
        ),
        context,
    )

    assert result.ok is True
    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL
    assert context.plan_state.approved_version is None


def test_update_plan_schema_uses_action_specific_contracts() -> None:
    schema = build_runtime().tool_registry.get("update_plan").input_schema

    assert "oneOf" in schema
    assert len(schema["oneOf"]) == 6
    replace_contract = next(
        contract
        for contract in schema["oneOf"]
        if contract["properties"]["action"].get("const") == "replace_plan"
    )
    assert set(replace_contract["required"]) == {"action", "steps"}
    assert "submit" in replace_contract["properties"]


def test_plan_response_tool_requires_fresh_user_continuation(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.REQUIRED)
    context.begin_task("plan the change")
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    context.plan_controller.submit_for_execution()

    assert "resolve_plan_response" not in schema_names(runtime, context)

    context.add_user_continuation("approve the plan")
    assert "resolve_plan_response" in schema_names(runtime, context)
    result = runtime.executor.execute(
        ToolCall("resolve", "resolve_plan_response", {"action": "approve"}),
        context,
    )

    assert result.ok is True
    assert context.plan_state.phase is PlanPhase.EXECUTING
    assert context.has_pending_user_continuation() is False
    assert "resolve_plan_response" not in schema_names(runtime, context)


def test_hidden_plan_tool_cannot_be_invoked_by_name(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.OFF)

    result = runtime.executor.execute(
        ToolCall(
            "hidden",
            "select_execution_mode",
            {"mode": "plan", "reason": "attempt to bypass visibility"},
        ),
        context,
    )

    assert result.ok is False
    assert result.metadata["unavailable_tool"] is True
