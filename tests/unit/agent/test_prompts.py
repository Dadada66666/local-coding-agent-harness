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


def test_executing_prompt_has_no_dynamic_call_budget_warning(tmp_path) -> None:
    six_remaining = TaskCallBudget.from_limits(max_calls=40, used_calls=34)
    five_remaining = TaskCallBudget.from_limits(max_calls=40, used_calls=35)
    first = build_system_prompt(tmp_path, plan_state(PlanPhase.EXECUTING))
    second = build_system_prompt(tmp_path, plan_state(PlanPhase.EXECUTING))

    assert six_remaining.approaching_limit is False
    assert five_remaining.approaching_limit is True
    assert first == second
    assert "approaching its global model-call limit" not in first


def test_completed_prompt_exposes_failed_verification_fact(tmp_path) -> None:
    prompt = build_system_prompt(
        tmp_path,
        plan_state(PlanPhase.COMPLETED),
        task_test_result={
            "ok": False,
            "verification_level": "static",
            "command": "git diff --check",
            "error": "command exited 2\nsecond line",
        },
    )

    assert 'status: "failed"' in prompt
    assert 'level: "static"' in prompt
    assert 'command: "git diff --check"' in prompt
    assert 'error: "command exited 2\\nsecond line"' in prompt
    assert "Do not report failed or unavailable verification as passed" in prompt


def test_completed_prompt_exposes_passed_verification_fact(tmp_path) -> None:
    prompt = build_system_prompt(
        tmp_path,
        plan_state(PlanPhase.COMPLETED),
        task_test_result={
            "ok": True,
            "verification_level": "test_suite",
            "command": "pytest",
        },
    )

    assert 'status: "passed"' in prompt
    assert 'level: "test_suite"' in prompt
    assert 'command: "pytest"' in prompt


def test_completed_prompt_marks_missing_verification_unavailable(tmp_path) -> None:
    prompt = build_system_prompt(
        tmp_path,
        plan_state(PlanPhase.COMPLETED),
        task_test_result=None,
    )

    assert 'status: "unavailable"' in prompt
    assert 'level: "unavailable"' in prompt
    assert 'command: "unavailable"' in prompt
    assert 'status: "passed"' not in prompt


def test_base_prompt_does_not_assume_git_workspace(tmp_path) -> None:
    prompt = build_system_prompt(tmp_path)

    assert "Do not assume the workdir is a Git repository" in prompt
    assert "prefer view_diff for diff inspection" in prompt
    assert "edit_file, write_file, or delete_file—not Bash" in prompt
