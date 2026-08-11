from __future__ import annotations

import json
from pathlib import Path

from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.plan import PlanApprovalPolicy, PlanPhase, PlanPolicy
from runtime.task import TaskStatus


class FakeModelClient:
    max_tokens = 4096
    context_window_tokens = 64000

    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = responses
        self.calls = []

    def call(self, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        self.calls.append(
            {
                "system": system,
                "messages": messages,
                "tools": {tool["name"] for tool in tools},
            }
        )
        return self.responses.pop(0)


def tool_response(*calls: ToolCall) -> ModelResponse:
    return ModelResponse(
        message={
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
                for call in calls
            ],
        },
        tool_calls=list(calls),
        usage=TokenUsage(),
    )


def final_response(text: str = "done") -> ModelResponse:
    return ModelResponse(
        message={"role": "assistant", "content": [{"type": "text", "text": text}]},
        text=text,
        usage=TokenUsage(),
    )


def make_runner(
    tmp_path: Path,
    policy: PlanPolicy,
    responses,
    *,
    approval_policy: PlanApprovalPolicy = PlanApprovalPolicy.MANUAL,
    config_overrides: dict | None = None,
) -> tuple[AgentLoop, FakeModelClient]:
    model = FakeModelClient(list(responses))
    config_values = {
        "permission_mode": "accept_edits",
        "plan_policy": policy,
        "plan_approval_policy": approval_policy,
        **(config_overrides or {}),
    }
    runner = AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(**config_values),
    )
    return runner, model


def replace_plan_call(identifier: str = "plan") -> ToolCall:
    return ToolCall(
        identifier,
        "update_plan",
        {
            "action": "replace_plan",
            "steps": [{"id": "step-1", "description": "Inspect the result"}],
            "submit": True,
        },
    )


def test_off_mode_preserves_original_prompt_and_tools(tmp_path) -> None:
    runner, model = make_runner(tmp_path, PlanPolicy.OFF, [final_response("done")])

    context = runner.run("answer without changes")

    assert context.success is True
    assert "Plan policy:" not in model.calls[0]["system"]
    assert "Call budget:" not in model.calls[0]["system"]
    assert "select_execution_mode" not in model.calls[0]["tools"]
    assert "update_plan" not in model.calls[0]["tools"]
    assert not (context.run_dir / "plan.json").exists()


def test_required_noninteractive_run_stops_for_approval_without_changes(tmp_path) -> None:
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [tool_response(replace_plan_call())],
    )

    context = runner.run("plan a repository change")

    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL
    assert context.finished is True
    assert context.success is False
    assert context.abort_reason is None
    assert context.task_changed_files == set()
    assert "awaiting user approval" in context.final_text
    assert "1) Approve and execute" in context.final_text
    assert "2) Revise the plan" in context.final_text
    assert "3) Reject and cancel" in context.final_text
    assert "update_plan" in model.calls[0]["tools"]
    assert "Planning budget:" not in model.calls[0]["system"]
    assert "Every replace_plan call requires explicit submit=true" in model.calls[0]["system"]
    assert (context.run_dir / "plan.json").is_file()
    report = (context.run_dir / "report.md").read_text(encoding="utf-8")
    assert "Status: waiting_user" in report
    assert "Success: pending" in report
    assert "Waiting reason: plan_approval" in report
    assert "## Model Call Budget" in report
    assert "- attempted_model_calls: 1/40" in report
    assert "- planning_calls: 1" in report


def test_existing_draft_converges_to_finalize_only_tool_contract(tmp_path) -> None:
    source = tmp_path / "demo.py"
    source.write_text("value = 1\n", encoding="utf-8")
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(
                ToolCall(
                    "draft",
                    "update_plan",
                    {
                        "action": "replace_plan",
                        "steps": [
                            {"id": "step-1", "description": "Implement the change"}
                        ],
                        "submit": False,
                    },
                )
            ),
            tool_response(ToolCall("read-once", "read_file", {"path": "demo.py"})),
            tool_response(
                ToolCall("inspect-once", "grep", {"path": ".", "pattern": "value"})
            ),
            tool_response(ToolCall("blocked-read", "read_file", {"path": "demo.py"})),
            tool_response(ToolCall("submit", "update_plan", {"action": "submit"})),
        ],
        config_overrides={"plan_draft_grace_calls": 2},
    )

    context = runner.run("plan a repository change")

    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL
    assert model.calls[1]["tools"] != {"update_plan"}
    assert model.calls[2]["tools"] != {"update_plan"}
    assert model.calls[3]["tools"] == {"update_plan"}
    assert model.calls[4]["tools"] == {"update_plan"}
    assert context.source_read_metrics.read_file_calls == 1
    events = [
        json.loads(line)
        for line in context.trace.path.read_text(encoding="utf-8").splitlines()
    ]
    blocked = next(
        event
        for event in events
        if event.get("type") == "tool_result"
        and event.get("tool_call_id") == "blocked-read"
    )
    assert blocked["ok"] is False
    assert blocked["metadata"]["blocked_by"] == "tool_capability"
    assert context.planning_progress.last_episode_calls == 5
    assert context.planning_progress.finalize_reason == "plan_draft_grace_exhausted"


def test_planning_hard_limit_requires_final_plan_when_no_draft_exists(tmp_path) -> None:
    (tmp_path / "demo.py").write_text("value = 1\n", encoding="utf-8")
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(ToolCall("inspect", "read_file", {"path": "demo.py"})),
            tool_response(replace_plan_call()),
        ],
        config_overrides={
            "planning_soft_limit_calls": 1,
            "planning_hard_limit_calls": 2,
        },
    )

    context = runner.run("plan a repository change")

    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL
    assert "read_file" in model.calls[0]["tools"]
    assert model.calls[1]["tools"] == {"update_plan"}
    assert "Planning finalization is required" in model.calls[1]["system"]


def test_auto_plan_decision_with_auto_approval_continues_without_user_input(tmp_path) -> None:
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.AUTO,
        [
            tool_response(
                ToolCall(
                    "select",
                    "select_execution_mode",
                    {"mode": "plan", "reason": "dependent runtime changes"},
                )
            ),
            tool_response(replace_plan_call()),
            tool_response(
                ToolCall(
                    "complete-step",
                    "update_plan",
                    {"action": "update_step", "step_id": "step-1", "status": "completed"},
                )
            ),
            tool_response(ToolCall("complete-plan", "update_plan", {"action": "complete"})),
            final_response("plan completed"),
        ],
        approval_policy=PlanApprovalPolicy.AUTO,
    )

    context = runner.run("inspect a multi-module concern")

    assert context.success is True
    assert context.plan_state.phase is PlanPhase.COMPLETED
    assert context.plan_state.approval_source == "auto_policy"
    assert len(model.calls) == 5
    assert "select_execution_mode" in model.calls[0]["tools"]
    assert "update_plan" in model.calls[1]["tools"]
    assert "update_plan" not in model.calls[-1]["tools"]


def test_auto_plan_decision_defaults_to_manual_approval(tmp_path) -> None:
    runner, _ = make_runner(
        tmp_path,
        PlanPolicy.AUTO,
        [
            tool_response(
                ToolCall(
                    "select",
                    "select_execution_mode",
                    {"mode": "plan", "reason": "dependent runtime changes"},
                )
            ),
            tool_response(replace_plan_call()),
        ],
    )

    context = runner.run("inspect a multi-module concern")

    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL
    assert context.task_status is TaskStatus.WAITING_USER


def test_required_approval_resumes_same_task_and_budget(tmp_path) -> None:
    runner, _ = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(replace_plan_call()),
            tool_response(
                ToolCall(
                    "complete-step",
                    "update_plan",
                    {"action": "update_step", "step_id": "step-1", "status": "completed"},
                )
            ),
            tool_response(ToolCall("complete-plan", "update_plan", {"action": "complete"})),
            final_response("approved plan completed"),
        ],
    )
    context = runner.start_interactive()

    runner.submit(context, "plan the task")
    first_task_id = context.task_id
    calls_before_approval = context.task_model_calls
    context.plan_controller.approve()
    runner.resume_runtime(context, "The user approved the current plan.")

    assert context.success is True
    assert context.task_id == first_task_id
    assert context.task_model_calls > calls_before_approval
    assert context.plan_state.phase is PlanPhase.COMPLETED


def test_natural_language_approval_continues_the_same_task(tmp_path) -> None:
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(replace_plan_call()),
            tool_response(
                ToolCall(
                    "resolve",
                    "resolve_plan_response",
                    {"action": "approve"},
                )
            ),
            tool_response(
                ToolCall(
                    "complete-step",
                    "update_plan",
                    {"action": "update_step", "step_id": "step-1", "status": "completed"},
                )
            ),
            tool_response(ToolCall("complete-plan", "update_plan", {"action": "complete"})),
            final_response("approved plan completed"),
        ],
    )
    context = runner.start_interactive()
    context.config.context_task_boundary_tokens = 1

    runner.start_task(context, "plan the task")
    task_id = context.task_id
    compactions = context.context_compactions

    runner.continue_task(context, "The plan looks correct; please proceed as proposed.")

    assert context.task_id == task_id
    assert context.task_sequence == 1
    assert context.task == "plan the task"
    assert context.completed_tasks == []
    assert context.context_compactions == compactions
    assert context.task_status is TaskStatus.COMPLETED
    assert context.plan_state.phase is PlanPhase.COMPLETED
    assert "resolve_plan_response" in model.calls[1]["tools"]
    snapshot = json.loads((context.run_dir / "plan.json").read_text(encoding="utf-8"))
    assert snapshot["task_id"] == task_id
    assert snapshot["task_status"] == "completed"


def test_exact_approval_uses_deterministic_fast_path(tmp_path) -> None:
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(replace_plan_call()),
            tool_response(
                ToolCall(
                    "complete-step",
                    "update_plan",
                    {"action": "update_step", "step_id": "step-1", "status": "completed"},
                )
            ),
            tool_response(ToolCall("complete-plan", "update_plan", {"action": "complete"})),
            final_response("approved plan completed"),
        ],
    )
    context = runner.start_interactive()
    runner.start_task(context, "plan the task")

    runner.continue_task(context, "  同意  ")

    assert context.success is True
    assert context.plan_state.phase is PlanPhase.COMPLETED
    assert context.plan_state.approval_source == "user"
    assert context.has_pending_user_continuation() is False
    assert model.calls[1]["tools"] != {"resolve_plan_response"}
    events = [
        json.loads(line)
        for line in context.trace.path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(event.get("type") == "plan_response_fast_path" for event in events) == 1


def test_negative_approval_phrase_is_not_fast_path(tmp_path) -> None:
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(replace_plan_call()),
            tool_response(
                ToolCall(
                    "resolve",
                    "resolve_plan_response",
                    {"action": "cancel", "reason": "The user did not approve."},
                )
            ),
        ],
    )
    context = runner.start_interactive()
    runner.start_task(context, "plan the task")

    runner.continue_task(context, "我不同意")

    assert model.calls[1]["tools"] == {"resolve_plan_response"}
    assert context.plan_state.phase is PlanPhase.CANCELLED
    assert context.plan_state.approved_version is None


def test_waiting_plan_without_fresh_continuation_does_not_call_model(tmp_path) -> None:
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [tool_response(replace_plan_call())],
    )
    context = runner.start_interactive()
    runner.start_task(context, "plan the task")
    calls_before_resume = len(model.calls)

    runner.resume_runtime(context, "Runtime status check only.")

    assert len(model.calls) == calls_before_resume
    assert context.task_status is TaskStatus.WAITING_USER
    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL


def test_control_plane_transition_cancels_remaining_tool_batch(tmp_path) -> None:
    source = tmp_path / "demo.py"
    source.write_text("value = 1\n", encoding="utf-8")
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(replace_plan_call()),
            tool_response(
                ToolCall("resolve", "resolve_plan_response", {"action": "approve"}),
                ToolCall("read", "read_file", {"path": "demo.py"}),
                ToolCall(
                    "edit",
                    "edit_file",
                    {"path": "demo.py", "old_text": "value = 1", "new_text": "value = 2"},
                ),
            ),
            tool_response(
                ToolCall("resolve-retry", "resolve_plan_response", {"action": "approve"})
            ),
            tool_response(
                ToolCall(
                    "complete-step",
                    "update_plan",
                    {"action": "update_step", "step_id": "step-1", "status": "completed"},
                )
            ),
            tool_response(ToolCall("complete-plan", "update_plan", {"action": "complete"})),
            final_response("approved plan completed"),
        ],
    )
    context = runner.start_interactive()
    runner.start_task(context, "plan the task")

    runner.continue_task(context, "Please proceed with the plan exactly as written.")

    assert context.success is True
    assert source.read_text(encoding="utf-8") == "value = 1\n"
    assert str(source) not in context.read_file_state
    cancelled = [
        block
        for message in context.messages
        if message.get("role") == "user" and isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict)
        and block.get("type") == "tool_result"
        and block.get("tool_use_id") in {"read", "edit"}
    ]
    assert len(cancelled) == 2
    assert all("plan_response_protocol_violation" in block["content"] for block in cancelled)
    assert model.calls[2]["tools"] == {"resolve_plan_response"}
    assert model.calls[3]["tools"] != {"resolve_plan_response"}


def test_pending_approval_retries_once_after_forged_hidden_tool(tmp_path) -> None:
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(replace_plan_call()),
            tool_response(
                ToolCall(
                    "malformed-edit",
                    "edit_file",
                    {
                        "path": "demo.py",
                        "old_string": "before",
                        "new_string": "after",
                    },
                )
            ),
            tool_response(
                ToolCall("resolve", "resolve_plan_response", {"action": "approve"})
            ),
            tool_response(
                ToolCall(
                    "complete-step",
                    "update_plan",
                    {"action": "update_step", "step_id": "step-1", "status": "completed"},
                )
            ),
            tool_response(ToolCall("complete-plan", "update_plan", {"action": "complete"})),
            final_response("approved plan completed"),
        ],
    )
    context = runner.start_interactive()
    runner.start_task(context, "plan the task")

    runner.continue_task(context, "Proceed with the plan as currently written.")

    assert context.success is True
    assert context.plan_state.phase is PlanPhase.COMPLETED
    assert context.has_pending_user_continuation() is False
    assert model.calls[1]["tools"] == {"resolve_plan_response"}
    assert model.calls[2]["tools"] == {"resolve_plan_response"}
    events = [
        json.loads(line)
        for line in context.trace.path.read_text(encoding="utf-8").splitlines()
    ]
    malformed = next(
        event
        for event in events
        if event.get("type") == "tool_result"
        and event.get("tool_call_id") == "malformed-edit"
    )
    assert malformed["metadata"]["model_contract_violation"] is True
    assert malformed["metadata"]["reason"] == "plan_response_protocol_violation"
    assert sum(event.get("type") == "plan_response_retry" for event in events) == 1


def test_pending_approval_retries_empty_tool_use_protocol_response(tmp_path) -> None:
    empty_tool_use = ModelResponse(
        message={"role": "assistant", "content": []},
        usage=TokenUsage(),
        stop_reason="tool_use",
    )
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(replace_plan_call()),
            empty_tool_use,
            tool_response(
                ToolCall("resolve", "resolve_plan_response", {"action": "approve"})
            ),
            tool_response(
                ToolCall(
                    "complete-step",
                    "update_plan",
                    {"action": "update_step", "step_id": "step-1", "status": "completed"},
                )
            ),
            tool_response(ToolCall("complete-plan", "update_plan", {"action": "complete"})),
            final_response("approved plan completed"),
        ],
    )
    context = runner.start_interactive()
    runner.start_task(context, "plan the task")

    runner.continue_task(context, "Proceed with the plan as currently written.")

    assert context.success is True
    assert context.plan_state.phase is PlanPhase.COMPLETED
    assert model.calls[1]["tools"] == {"resolve_plan_response"}
    assert model.calls[2]["tools"] == {"resolve_plan_response"}


def test_pending_approval_retry_is_bounded(tmp_path) -> None:
    malformed = ToolCall(
        "malformed-edit-1",
        "edit_file",
        {"path": "demo.py", "old_string": "before", "new_string": "after"},
    )
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            tool_response(replace_plan_call()),
            tool_response(malformed),
            tool_response(
                ToolCall(
                    "malformed-edit-2",
                    "edit_file",
                    malformed.arguments,
                )
            ),
        ],
    )
    context = runner.start_interactive()
    runner.start_task(context, "plan the task")

    runner.continue_task(context, "Proceed with the plan as currently written.")

    assert len(model.calls) == 3
    assert context.task_status is TaskStatus.WAITING_USER
    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL
    assert context.has_pending_user_continuation() is True
    assert "could not be resolved" in context.final_text
    events = [
        json.loads(line)
        for line in context.trace.path.read_text(encoding="utf-8").splitlines()
    ]
    assert sum(event.get("type") == "plan_response_retry" for event in events) == 1
    assert sum(
        event.get("type") == "plan_response_retry_exhausted" for event in events
    ) == 1


def test_natural_language_plan_cannot_bypass_structured_submission(tmp_path) -> None:
    runner, _ = make_runner(
        tmp_path,
        PlanPolicy.REQUIRED,
        [
            final_response("I have a plan in prose."),
            tool_response(replace_plan_call()),
        ],
    )

    context = runner.run("plan the task")

    assert context.task_model_calls == 2
    assert context.plan_state.phase is PlanPhase.AWAITING_APPROVAL
    assert context.abort_reason is None


def test_final_response_waits_for_explicit_plan_completion(tmp_path) -> None:
    runner, model = make_runner(
        tmp_path,
        PlanPolicy.AUTO,
        [
            tool_response(
                ToolCall(
                    "select",
                    "select_execution_mode",
                    {"mode": "plan", "reason": "dependent work"},
                )
            ),
            tool_response(replace_plan_call()),
            tool_response(
                ToolCall(
                    "complete-step",
                    "update_plan",
                    {"action": "update_step", "step_id": "step-1", "status": "completed"},
                )
            ),
            final_response("premature final"),
            tool_response(ToolCall("complete-plan", "update_plan", {"action": "complete"})),
            final_response("complete final"),
        ],
        approval_policy=PlanApprovalPolicy.AUTO,
    )

    context = runner.run("complete a planned task")

    assert context.success is True
    assert context.final_text == "complete final"
    assert context.plan_state.phase is PlanPhase.COMPLETED
    assert len(model.calls) == 6
