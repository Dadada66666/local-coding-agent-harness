from types import SimpleNamespace

from agent.prompts import build_plan_instructions, build_system_prompt
from runtime.call_budget import TaskCallBudget
from runtime.plan import (
    ExecutionPath,
    PlanApprovalPolicy,
    PlanPhase,
    PlanPolicy,
    PlanStep,
    PlanStepStatus,
)


def plan_state(phase: PlanPhase):
    return SimpleNamespace(
        policy=PlanPolicy.REQUIRED,
        approval_policy=PlanApprovalPolicy.MANUAL,
        execution_path=ExecutionPath.PLAN,
        phase=phase,
        version=1,
        steps=[
            PlanStep(
                "step-1",
                "Implement and verify the focused change",
                PlanStepStatus.IN_PROGRESS,
            )
        ],
        revision_feedback=None,
    )


def test_planning_prompt_preserves_model_strategy_and_explicit_protocol() -> None:
    prompt = build_plan_instructions(plan_state(PlanPhase.PLANNING))

    assert "Inspect the repository as needed" in prompt
    assert "explicit submit=true or submit=false" in prompt
    assert "Runtime owns execution status" in prompt
    assert "Planning budget:" not in prompt
    assert "finalization is required" not in prompt


def test_execution_prompt_keeps_only_lifecycle_guidance() -> None:
    prompt = build_plan_instructions(plan_state(PlanPhase.EXECUTING))

    assert "Follow the approved plan" in prompt
    assert "milestone bookkeeping, not a turn boundary" in prompt
    assert "pending steps may be completed directly" in prompt
    assert "in_progress only for work spanning calls" in prompt
    assert "Batch routine update_step with the next known repository tool call" in prompt
    assert "Request replanning for material deviations" in prompt
    assert "make update_plan action complete the final ToolCall" in prompt
    assert "verification reserve" not in prompt.lower()
    assert "calls remain" not in prompt.lower()


def test_global_call_limit_hint_appears_only_near_hard_limit(tmp_path) -> None:
    normal = build_system_prompt(
        tmp_path,
        call_budget=TaskCallBudget.from_limits(max_calls=40, used_calls=34),
    )
    near_limit = build_system_prompt(
        tmp_path,
        call_budget=TaskCallBudget.from_limits(max_calls=40, used_calls=35),
    )

    assert "approaching its global model-call limit" not in normal
    assert "approaching its global model-call limit" in near_limit


def test_base_prompt_does_not_assume_git_workspace(tmp_path) -> None:
    prompt = build_system_prompt(tmp_path)

    assert "Do not assume the workdir is a Git repository" in prompt
    assert "prefer view_diff for diff inspection" in prompt
    assert "edit_file, write_file, or delete_file—not Bash" in prompt
