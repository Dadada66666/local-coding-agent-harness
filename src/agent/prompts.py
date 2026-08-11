from __future__ import annotations

import platform
from pathlib import Path

from runtime.plan import (
    ExecutionPath,
    PlanApprovalPolicy,
    PlanPhase,
    PlanPolicy,
    PlanStepStatus,
)


BASE_SYSTEM_PROMPT = """You are a local coding agent working inside {workdir}.

Runtime:
- OS: {os_name}
- Command shell: {shell_name}

Behavior:
- Inspect relevant context before making changes.
- Use the available tools according to their purpose.
- Use the least context sufficient to make a correct next action
- After code edits, run the smallest relevant check when available.
- Report honestly if verification was not possible.
- For commands that require stdin, use the bash tool input field.
- When running a command to validate behavior, set bash purpose to "verify".

Safety:
- Do not attempt broad or irreversible destructive operations.
- If permission is denied, treat the operation as cancelled.

Final answer:
- Summary
- Changed files
- Checks run
- Risks
"""


def detect_shell_name() -> str:
    if platform.system() == "Windows":
        return "PowerShell, preferring pwsh then powershell.exe, via subprocess shell=False"
    return "/bin/sh via subprocess shell=False"


def build_system_prompt(
    workdir: Path,
    plan_state=None,
    *,
    has_user_continuation: bool = False,
    source_context: list[str] | None = None,
    call_budget=None,
    planning_budget=None,
) -> str:
    base = BASE_SYSTEM_PROMPT.format(
        workdir=workdir.resolve(),
        os_name=platform.system(),
        shell_name=detect_shell_name(),
    )
    plan_instructions = build_plan_instructions(
        plan_state,
        has_user_continuation=has_user_continuation,
        call_budget=call_budget,
        planning_budget=planning_budget,
    )
    sections = [base.rstrip()]
    if plan_instructions:
        sections.append(plan_instructions)
    if source_context and getattr(plan_state, "phase", None) is not PlanPhase.AWAITING_APPROVAL:
        entries = "\n".join(f"- {value[:300]}" for value in source_context[:5])
        sections.append(
            "Source context:\n"
            f"{entries}\n"
            "Avoid restarting a full scan of unchanged files; use grep or narrow reads."
        )
    return "\n\n".join(sections) + "\n"


def build_plan_instructions(
    plan_state,
    *,
    has_user_continuation: bool = False,
    call_budget=None,
    planning_budget=None,
) -> str:
    if plan_state is None or plan_state.policy is PlanPolicy.OFF:
        return ""

    if plan_state.execution_path is ExecutionPath.UNDECIDED:
        return """Plan policy: auto; execution path is undecided.
- Decide whether this task is a small, local, low-risk direct change or needs a structured plan.
- You may inspect the repository with read-only tools first.
- Before Bash or any repository mutation, call select_execution_mode with a concrete reason.
- Prefer plan for multi-module, architectural, ambiguous, security-sensitive, or dependent work.
- A natural-language claim does not select an execution path; use the tool."""

    if plan_state.phase is PlanPhase.PLANNING:
        approval_rule = (
            "A submitted plan will wait for explicit user approval."
            if plan_state.approval_policy is PlanApprovalPolicy.MANUAL
            else "A submitted plan is authorized by auto policy and execution continues immediately."
        )
        revision = (
            f"\n- Revision constraint: {plan_state.revision_feedback[:500]}"
            if plan_state.revision_feedback
            else ""
        )
        budget = _planning_budget_instructions(plan_state, planning_budget)
        return f"""Plan phase: planning (read-only).
- Inspect actual files and modules; do not modify files or run Bash.
- Maintain an executable, verifiable structured plan with update_plan.
- Scope open-ended tasks to one coherent, high-value outcome and defer optional work.
- Use coarse work packages rather than a fine-grained todo list; include verification.
- Plan steps contain only id and description; Runtime owns execution status.
- Every replace_plan call requires explicit submit=true or submit=false.
- Use submit=false only while concrete repository questions remain unresolved.
- When complete, use replace_plan with submit=true in one call.
- Use stable unique step ids and concrete repository references.
- Submit only after the plan is complete. {approval_rule}
- Do not assume approval or describe a natural-language plan as submitted.{budget}{revision}"""

    if plan_state.phase is PlanPhase.AWAITING_APPROVAL:
        if has_user_continuation:
            return """Plan phase: awaiting user approval; a fresh user response is available.
- Interpret only that real user response with resolve_plan_response.
- Choose approve, revise, or cancel according to the user's intent.
- Do not modify files or run Bash until approval has been resolved."""
        return """Plan phase: awaiting user approval.
- Do not modify files or run Bash.
- The model cannot approve its own plan; the runtime will pause for real user input."""

    if plan_state.phase is PlanPhase.EXECUTING:
        current = next(
            (
                step
                for step in plan_state.steps
                if step.status is PlanStepStatus.IN_PROGRESS
            ),
            None,
        )
        next_pending = next(
            (step for step in plan_state.steps if step.status is PlanStepStatus.PENDING),
            None,
        )
        current_text = (
            f"{current.id}: {current.description[:300]}"
            if current is not None
            else (
                f"none selected; next pending is {next_pending.id}: "
                f"{next_pending.description[:240]}"
                if next_pending is not None
                else "none selected"
            )
        )
        budget = _execution_budget_instructions(call_budget)
        return f"""Plan phase: executing authorized plan version {plan_state.version}.
- Follow the approved plan and use update_plan to keep step status current.
- Current step: {current_text}
- Start a pending step in the same response as its first substantive tool work.
- When practical, batch routine step-status updates with substantive work; do not spend a turn only announcing progress.
- Repository tools still pass through the existing Permission Gate.
- Run the smallest relevant verification after changes.
- For a major deviation, request_replan instead of silently replacing the plan.
- Mark all steps completed, then use update_plan action complete before the final response.{budget}"""

    if plan_state.phase is PlanPhase.COMPLETED:
        return "Plan phase: completed. Provide the concise final task report now."

    if plan_state.phase is PlanPhase.CANCELLED:
        return "Plan phase: cancelled. Do not perform additional repository work."

    return f"Plan phase: {plan_state.phase.value}. Do not start unapproved repository work."


def _planning_budget_instructions(plan_state, planning_budget) -> str:
    if planning_budget is None:
        return ""
    if planning_budget.finalize_required:
        action = (
            "submit the current plan, replace it with submit=true, or cancel"
            if plan_state.steps
            else "create the final plan with submit=true, or cancel"
        )
        return f"\n- Planning finalization is required: {action}; do not investigate further."
    if planning_budget.soft_limit_reached:
        return (
            "\n- Planning budget: "
            f"{planning_budget.used_calls}/{planning_budget.hard_limit_calls} calls used; "
            "resolve only concrete blockers and finalize the plan."
        )
    return ""


def _execution_budget_instructions(call_budget) -> str:
    if call_budget is None or not call_budget.nearing_reserve:
        return ""
    if call_budget.reserve_active:
        return (
            f"\n- Call budget: {call_budget.remaining_calls} remain; verify current "
            "mutations and finalize now."
        )
    return (
        f"\n- Call budget: {call_budget.remaining_calls} remain; finish current work, "
        "then verify; avoid optional scope."
    )


SYSTEM_PROMPT = build_system_prompt(Path.cwd())


def build_initial_messages(task: str) -> list[dict]:
    return [{"role": "user", "content": task}]
