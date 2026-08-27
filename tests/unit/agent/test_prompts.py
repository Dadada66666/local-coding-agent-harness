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


def auto_state(execution_path: ExecutionPath):
    return SimpleNamespace(
        policy=PlanPolicy.AUTO,
        approval_policy=PlanApprovalPolicy.MANUAL,
        execution_path=execution_path,
        phase=PlanPhase.INACTIVE,
        version=0,
        steps=[],
        revision_feedback=None,
    )


def test_auto_undecided_prompt_is_a_bounded_path_decision_phase() -> None:
    prompt = build_plan_instructions(auto_state(ExecutionPath.UNDECIDED))

    assert "execution-path decision phase" in prompt
    assert "not open-ended task execution or capability enumeration" in prompt
    assert "only until you have enough information to choose Direct or Plan" in prompt
    assert "Execution-only tools are intentionally hidden" in prompt
    assert "call select_execution_mode before continuing task work" in prompt
    assert "further synonymous capability discovery" in prompt
    assert "Do not exhaustively discover every tool needed later" in prompt


def test_auto_direct_has_no_plan_restriction_prompt() -> None:
    prompt = build_plan_instructions(auto_state(ExecutionPath.DIRECT))

    assert prompt == ""
    assert "Do not start unapproved repository work" not in prompt


def test_planning_prompt_preserves_model_strategy_and_explicit_protocol() -> None:
    prompt = build_plan_instructions(plan_state(PlanPhase.PLANNING))

    assert "Inspect the repository as needed" in prompt
    assert "only enough remote capability to make the plan executable" in prompt
    assert "defer detailed discovery to execution unless ambiguity blocks planning" in prompt
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
    assert "approved plan is the execution scope" in prompt
    assert "validation/inspection-only plan does not authorize fixing" in prompt
    assert "Material scope expansion requires replanning" in prompt
    assert "stated outcome has been achieved or observed" in prompt
    assert "completing a prerequisite is insufficient" in prompt
    assert "Request replanning for material deviations" in prompt
    assert "make update_plan action complete the final ToolCall" in prompt
    assert "verification reserve" not in prompt.lower()
    assert "calls remain" not in prompt.lower()


def test_base_prompt_keeps_validation_only_tasks_read_only_without_repair_authority(
    tmp_path,
) -> None:
    prompt = build_system_prompt(tmp_path, plan_state(PlanPhase.INACTIVE))

    assert "validation, inspection, audit, review, or test-only tasks" in prompt
    assert "report discovered defects instead of modifying repository files" in prompt
    assert "unless the user explicitly requested repair or modification" in prompt
    assert 'purpose "verify" only for authoritative final task verification' in prompt
    assert 'use "probe" for environment, setup, or availability diagnostics' in prompt


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
