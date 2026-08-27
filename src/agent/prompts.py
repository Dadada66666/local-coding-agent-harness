from __future__ import annotations

import json
import platform
from pathlib import Path

from runtime.plan import (
    ExecutionPath,
    PlanApprovalPolicy,
    PlanPhase,
    PlanPolicy,
)


BASE_SYSTEM_PROMPT = """You are a local coding agent working inside {workdir}.

Runtime:
- OS: {os_name}
- Command shell: {shell_name}

Behavior:
- Inspect relevant context before making changes.
- Use edit_file, write_file, or delete_file—not Bash—for repository mutations.
- Use the least context sufficient to make a correct next action
- After code edits, run the smallest relevant check when available.
- Do not assume the workdir is a Git repository; prefer view_diff for diff inspection.
- Report honestly if verification was not possible.
- For validation, inspection, audit, review, or test-only tasks, report discovered defects instead of modifying repository files unless the user explicitly requested repair or modification.
- For commands that require stdin, use the bash tool input field.
- Use bash purpose "verify" only for authoritative final task verification; use "probe" for environment, setup, or availability diagnostics and "run" for ordinary execution.

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
    task_test_result: dict | None = None,
) -> str:
    base = BASE_SYSTEM_PROMPT.format(
        workdir=workdir.resolve(),
        os_name=platform.system(),
        shell_name=detect_shell_name(),
    )
    plan_instructions = build_plan_instructions(
        plan_state,
        has_user_continuation=has_user_continuation,
    )
    sections = [base.rstrip()]
    if plan_instructions:
        sections.append(plan_instructions)
    if getattr(plan_state, "phase", None) is PlanPhase.COMPLETED:
        sections.append(_authoritative_verification_prompt(task_test_result))
    return "\n\n".join(sections) + "\n"


def _authoritative_verification_prompt(task_test_result: dict | None) -> str:
    result = task_test_result or {}
    ok = result.get("ok") if task_test_result is not None else None
    status = "passed" if ok is True else "failed" if ok is False else "unavailable"
    values = {
        "status": status,
        "level": result.get("verification_level") or "unavailable",
        "command": result.get("command") or "unavailable",
    }
    if result.get("error"):
        values["error"] = result["error"]

    facts = "\n".join(
        f"{key}: {json.dumps(str(value), ensure_ascii=False)}" for key, value in values.items()
    )
    return (
        f"Authoritative verification:\n{facts}\n"
        "Only this Runtime fact is authoritative for the current task verification status. "
        "Do not report failed or unavailable verification as passed."
    )


def build_plan_instructions(
    plan_state,
    *,
    has_user_continuation: bool = False,
) -> str:
    if plan_state is None or plan_state.policy is PlanPolicy.OFF:
        return ""

    if plan_state.execution_path is ExecutionPath.UNDECIDED:
        return """Plan policy: auto; execution path is undecided.
- This is an execution-path decision phase, not open-ended task execution or capability enumeration.
- Decide whether this task is a small, local, low-risk direct change or needs a structured plan.
- Use read-only repository inspection or capability discovery only until you have enough information to choose Direct or Plan.
- Execution-only tools are intentionally hidden until an execution path is selected.
- Once a relevant capability is identified and the path can be chosen, call select_execution_mode before continuing task work or further synonymous capability discovery.
- Do not exhaustively discover every tool needed later; after selection, the Runtime exposes the existing surface for the chosen path.
- Before Bash or any repository mutation, call select_execution_mode with a concrete reason.
- Prefer plan for multi-module, architectural, ambiguous, security-sensitive, or dependent work.
- A natural-language claim does not select an execution path; use the tool."""

    if plan_state.execution_path is ExecutionPath.DIRECT:
        return ""

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
        return f"""Plan phase: planning (read-only).
- Inspect the repository as needed; do not modify files or run Bash.
- Discover only enough remote capability to make the plan executable; once a suitable capability is clear, defer detailed discovery to execution unless ambiguity blocks planning.
- Use update_plan when you have an executable, verifiable plan.
- Prefer coarse outcome-level steps over tiny implementation todos.
- Plan steps contain only id and description; Runtime owns execution status.
- Every replace_plan call requires explicit submit=true or submit=false.
- Use stable unique step ids and concrete repository references.
- Submit only after the plan is complete. {approval_rule}
- Do not assume approval or describe a natural-language plan as submitted.{revision}"""

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
        return f"""Plan phase: executing authorized plan version {plan_state.version}.
- Follow the approved plan and use repository tools normally.
- The approved plan is the execution scope. A validation/inspection-only plan does not authorize fixing discovered issues; report findings unless repair is explicit in the user request or approved plan.
- Material scope expansion requires replanning.
- Mark a step completed only after its stated outcome has been achieved or observed; completing a prerequisite is insufficient.
- Plan status is milestone bookkeeping, not a turn boundary: pending steps may be completed directly; use in_progress only for work spanning calls.
- Batch routine update_step with the next known repository tool call; wait only when its result determines that action.
- Repository tools still pass through the existing Permission Gate.
- Request replanning for material deviations instead of silently replacing the plan.
- Verify relevant changes before completion when practical.
- Finish repository work and verification first; after all steps are completed, make update_plan action complete the final ToolCall."""

    if plan_state.phase is PlanPhase.COMPLETED:
        return "Plan phase: completed. Provide the concise final task report now."

    if plan_state.phase is PlanPhase.CANCELLED:
        return "Plan phase: cancelled. Do not perform additional repository work."

    return f"Plan phase: {plan_state.phase.value}. Do not start unapproved repository work."


SYSTEM_PROMPT = build_system_prompt(Path.cwd())


def build_initial_messages(task: str) -> list[dict]:
    return [{"role": "user", "content": task}]
