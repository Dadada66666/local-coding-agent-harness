from types import SimpleNamespace

from agent.prompts import build_plan_instructions
from runtime.call_budget import PlanningCallBudget, TaskCallBudget
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


def planning_budget(*, used: int, finalize: bool = False) -> PlanningCallBudget:
    return PlanningCallBudget(
        used_calls=used,
        soft_limit_calls=6,
        hard_limit_calls=8,
        calls_until_hard_limit=max(8 - used, 0),
        soft_limit_reached=used >= 6,
        hard_limit_reached=used >= 8,
        finalize_required=finalize,
    )


def test_planning_prompt_keeps_normal_turns_compact() -> None:

    prompt = build_plan_instructions(
        plan_state(PlanPhase.PLANNING),
        planning_budget=planning_budget(used=4),
    )

    assert "one coherent, high-value outcome" in prompt
    assert "Every replace_plan call requires explicit submit=true or submit=false" in prompt
    assert "Planning budget:" not in prompt


def test_planning_prompt_surfaces_phase_budget_only_under_pressure() -> None:

    prompt = build_plan_instructions(
        plan_state(PlanPhase.PLANNING),
        planning_budget=planning_budget(used=6),
    )

    assert "Planning budget: 6/8 calls used" in prompt
    assert "resolve only concrete blockers and finalize the plan" in prompt


def test_planning_prompt_requires_control_plane_finalization() -> None:
    prompt = build_plan_instructions(
        plan_state(PlanPhase.PLANNING),
        planning_budget=planning_budget(used=8, finalize=True),
    )

    assert "Planning finalization is required" in prompt
    assert "submit the current plan, replace it with submit=true, or cancel" in prompt
    assert "do not investigate further" in prompt


def test_execution_prompt_activates_verification_reserve() -> None:
    budget = TaskCallBudget.from_limits(
        max_calls=40,
        used_calls=36,
        configured_reserve=4,
    )

    prompt = build_plan_instructions(
        plan_state(PlanPhase.EXECUTING),
        call_budget=budget,
    )

    assert "Call budget: 4 remain" in prompt
    assert "do not spend a turn only announcing progress" in prompt
    assert "verify current mutations and finalize now" in prompt


def test_execution_prompt_omits_budget_until_reserve_is_near() -> None:
    budget = TaskCallBudget.from_limits(
        max_calls=40,
        used_calls=20,
        configured_reserve=4,
    )

    prompt = build_plan_instructions(
        plan_state(PlanPhase.EXECUTING),
        call_budget=budget,
    )

    assert "Call budget:" not in prompt
