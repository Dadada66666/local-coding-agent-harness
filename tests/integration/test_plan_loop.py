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
) -> tuple[AgentLoop, FakeModelClient]:
    model = FakeModelClient(list(responses))
    runner = AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(
            permission_mode="accept_edits",
            plan_policy=policy,
            plan_approval_policy=approval_policy,
        ),
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
    assert "update_plan" in model.calls[0]["tools"]
    assert (context.run_dir / "plan.json").is_file()
    report = (context.run_dir / "report.md").read_text(encoding="utf-8")
    assert "Status: waiting_user" in report
    assert "Success: pending" in report
    assert "Waiting reason: plan_approval" in report


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

    runner.continue_task(context, "同意执行")

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

    runner.continue_task(context, "approve")

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
    assert all("control_plane_transition" in block["content"] for block in cancelled)
    assert model.calls[2]["tools"] != {"resolve_plan_response"}


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
