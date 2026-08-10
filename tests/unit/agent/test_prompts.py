from types import SimpleNamespace

from agent.prompts import build_plan_instructions
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


def test_planning_prompt_exposes_bounded_call_budget() -> None:
    budget = TaskCallBudget.from_limits(
        max_calls=40,
        used_calls=9,
        configured_reserve=4,
    )

    prompt = build_plan_instructions(
        plan_state(PlanPhase.PLANNING),
        call_budget=budget,
    )

    assert "31/40 remain" in prompt
    assert "reserve 4 for verify/finalize" in prompt
    assert "use outcome-level steps" in prompt
    assert "choose their count by actual dependencies" in prompt
    assert "one coherent, high-value outcome" in prompt


def test_planning_prompt_tightens_granularity_without_a_step_limit() -> None:
    budget = TaskCallBudget.from_limits(
        max_calls=40,
        used_calls=34,
        configured_reserve=4,
    )

    prompt = build_plan_instructions(
        plan_state(PlanPhase.PLANNING),
        call_budget=budget,
    )

    assert "6/40 remain" in prompt
    assert "minimum implementation and verification path" in prompt
    assert "at most" not in prompt


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
