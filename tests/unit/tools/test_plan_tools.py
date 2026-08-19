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
    assert "read_file" in schema_names(runtime, required)
    assert "write_file" not in schema_names(runtime, required)
    assert "bash" not in schema_names(runtime, required)
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
    tool = build_runtime().tool_registry.get("update_plan")
    schema = tool.input_schema

    assert "oneOf" in schema
    assert len(schema["oneOf"]) == 6
    replace_contract = next(
        contract
        for contract in schema["oneOf"]
        if contract["properties"]["action"].get("const") == "replace_plan"
    )
    assert set(replace_contract["required"]) == {"action", "steps", "submit"}
    assert "submit" in replace_contract["properties"]
    step_properties = replace_contract["properties"]["steps"]["items"]["properties"]
    assert set(step_properties) == {"id", "description"}
    update_contract = next(
        contract
        for contract in schema["oneOf"]
        if contract["properties"]["action"].get("const") == "update_step"
    )
    status_description = update_contract["properties"]["status"]["description"]
    assert "pending may become completed directly" in status_description
    assert "in_progress is optional" in status_description
    complete_contract = next(
        contract
        for contract in schema["oneOf"]
        if contract["properties"]["action"].get("const") == "complete"
    )
    assert "final ToolCall" in complete_contract["properties"]["action"]["description"]
    assert "execution milestones" in tool.description


def test_update_plan_schema_is_narrowed_by_plan_phase(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.REQUIRED)

    planning_schema = next(
        schema for schema in runtime.tool_registry.schemas(context) if schema["name"] == "update_plan"
    )["input_schema"]
    planning_actions = {
        contract["properties"]["action"]["const"]
        for contract in planning_schema["oneOf"]
    }
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    context.plan_controller.submit_for_execution()
    context.plan_controller.approve()
    executing_schema = next(
        schema for schema in runtime.tool_registry.schemas(context) if schema["name"] == "update_plan"
    )["input_schema"]
    executing_actions = {
        contract["properties"]["action"]["const"]
        for contract in executing_schema["oneOf"]
    }

    assert planning_actions == {"replace_plan", "submit", "cancel"}
    assert executing_actions == {"update_step", "request_replan", "cancel", "complete"}


def test_update_plan_keeps_plan_size_flexible_but_requires_submit_intent(
    tmp_path,
) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.REQUIRED)
    context.task_model_calls = 9

    planning_schema = next(
        schema
        for schema in runtime.tool_registry.schemas(context)
        if schema["name"] == "update_plan"
    )["input_schema"]
    replace_contract = next(
        contract
        for contract in planning_schema["oneOf"]
        if contract["properties"]["action"]["const"] == "replace_plan"
    )

    assert replace_contract["properties"]["steps"]["maxItems"] == 100

    result = runtime.executor.execute(
        ToolCall(
            "detailed-plan",
            "update_plan",
            {
                "action": "replace_plan",
                "steps": [
                    {"id": f"step-{index}", "description": f"Work package {index}"}
                    for index in range(10)
                ],
                "submit": False,
            },
        ),
        context,
    )

    assert result.ok is True
    assert result.metadata["plan_submitted"] is False
    assert "Draft saved with submit=false" in result.content

    missing_submit = runtime.executor.execute(
        ToolCall(
            "missing-submit",
            "update_plan",
            {
                "action": "replace_plan",
                "steps": [{"id": "step-1", "description": "Focused change"}],
            },
        ),
        context,
    )

    assert missing_submit.ok is False
    assert missing_submit.metadata["validation_error"] is True

    oversized = runtime.executor.execute(
        ToolCall(
            "pathological-plan",
            "update_plan",
            {
                "action": "replace_plan",
                "steps": [
                    {"id": f"step-{index}", "description": f"Work package {index}"}
                    for index in range(101)
                ],
                "submit": False,
            },
        ),
        context,
    )

    assert oversized.ok is False
    assert oversized.metadata["plan_error"] is True
    assert "at most 100 steps" in oversized.error


def test_planning_steps_cannot_claim_execution_status(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.REQUIRED)

    result = runtime.executor.execute(
        ToolCall(
            "forged-status",
            "update_plan",
            {
                "action": "replace_plan",
                "steps": [
                    {
                        "id": "step-1",
                        "description": "Implement the change",
                        "status": "completed",
                    }
                ],
                "submit": True,
            },
        ),
        context,
    )

    assert result.ok is False
    assert result.metadata["validation_error"] is True
    assert "cannot set execution status" in result.error


def test_submit_accepts_model_selected_plan_size(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.REQUIRED)
    context.plan_controller.replace_plan(
        [
            {"id": f"step-{index}", "description": f"Work package {index}"}
            for index in range(7)
        ]
    )
    context.task_model_calls = 30

    result = runtime.executor.execute(
        ToolCall("submit-over-budget", "update_plan", {"action": "submit"}),
        context,
    )

    assert result.ok is True
    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL


def test_plan_response_tool_requires_fresh_user_continuation(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.REQUIRED)
    context.begin_task("plan the change")
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    context.plan_controller.submit_for_execution()

    assert schema_names(runtime, context) == set()

    context.add_user_continuation("approve the plan")
    assert schema_names(runtime, context) == {"resolve_plan_response"}
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
    assert result.metadata["known_tool"] is True
    assert result.metadata["blocked_by"] == "tool_capability"
    assert result.metadata["model_contract_violation"] is True


def test_registry_schema_and_executor_capabilities_stay_in_sync(tmp_path) -> None:
    runtime = build_runtime()
    contexts = [
        make_context(tmp_path / "off", PlanPolicy.OFF),
        make_context(tmp_path / "auto", PlanPolicy.AUTO),
        make_context(tmp_path / "required", PlanPolicy.REQUIRED),
    ]
    required = contexts[-1]
    required.begin_task("plan the change")
    required.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    required.plan_controller.submit_for_execution()
    required.add_user_continuation("review the plan response")

    for context in contexts:
        schema_tools = schema_names(runtime, context)
        callable_tools = {
            name
            for name in runtime.tool_registry.all_names()
            if runtime.tool_registry.resolve(name, context).available
        }
        assert callable_tools == schema_tools


def test_hidden_valid_edit_is_rejected_before_tool_validation(tmp_path) -> None:
    runtime = build_runtime()
    context = make_context(tmp_path, PlanPolicy.REQUIRED)
    context.begin_task("plan the change")
    context.plan_controller.replace_plan(
        [{"id": "step-1", "description": "Implement the change"}]
    )
    context.plan_controller.submit_for_execution()
    context.add_user_continuation("review the plan response")
    target = tmp_path / "demo.py"
    target.write_text("before\n", encoding="utf-8")

    result = runtime.executor.execute(
        ToolCall(
            "hidden-edit",
            "edit_file",
            {"path": "demo.py", "old_text": "before", "new_text": "after"},
        ),
        context,
    )

    assert result.ok is False
    assert result.metadata["blocked_by"] == "tool_capability"
    assert result.metadata["model_contract_violation"] is True
    assert "validation_error" not in result.metadata
    assert target.read_text(encoding="utf-8") == "before\n"
