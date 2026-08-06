from __future__ import annotations

from pathlib import Path

from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.plan import PlanPhase, PlanPolicy


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


def make_runner(tmp_path: Path, policy: PlanPolicy, responses) -> tuple[AgentLoop, FakeModelClient]:
    model = FakeModelClient(list(responses))
    runner = AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", plan_policy=policy),
    )
    return runner, model


def replace_plan_call(identifier: str = "plan") -> ToolCall:
    return ToolCall(
        identifier,
        "update_plan",
        {
            "action": "replace_plan",
            "steps": [{"id": "step-1", "description": "Inspect the result"}],
            "ready_for_approval": True,
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


def test_auto_plan_continues_without_user_approval(tmp_path) -> None:
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
    )

    context = runner.run("inspect a multi-module concern")

    assert context.success is True
    assert context.plan_state.phase is PlanPhase.COMPLETED
    assert context.plan_state.approval_source == "auto_policy"
    assert len(model.calls) == 5
    assert "select_execution_mode" in model.calls[0]["tools"]
    assert "update_plan" in model.calls[1]["tools"]
    assert "update_plan" not in model.calls[-1]["tools"]


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
    runner.resume(context, "The user approved the current plan.")

    assert context.success is True
    assert context.task_id == first_task_id
    assert context.task_model_calls > calls_before_approval
    assert context.plan_state.phase is PlanPhase.COMPLETED


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
    )

    context = runner.run("complete a planned task")

    assert context.success is True
    assert context.final_text == "complete final"
    assert context.plan_state.phase is PlanPhase.COMPLETED
    assert len(model.calls) == 6
