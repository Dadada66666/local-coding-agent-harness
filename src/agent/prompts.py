from __future__ import annotations

import platform
from pathlib import Path

from runtime.plan import ExecutionPath, PlanPhase, PlanPolicy, PlanStepStatus


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


def build_system_prompt(workdir: Path, plan_state=None) -> str:
    base = BASE_SYSTEM_PROMPT.format(
        workdir=workdir.resolve(),
        os_name=platform.system(),
        shell_name=detect_shell_name(),
    )
    plan_instructions = build_plan_instructions(plan_state)
    if not plan_instructions:
        return base
    return f"{base.rstrip()}\n\n{plan_instructions}\n"


def build_plan_instructions(plan_state) -> str:
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
            if plan_state.policy is PlanPolicy.REQUIRED
            else "A submitted plan is authorized by auto policy and execution continues immediately."
        )
        revision = (
            f"\n- Revision constraint: {plan_state.revision_feedback[:500]}"
            if plan_state.revision_feedback
            else ""
        )
        return f"""Plan phase: planning (read-only).
- Inspect actual files and modules; do not modify files or run Bash.
- Maintain an executable, verifiable structured plan with update_plan.
- Use stable unique step ids and concrete repository references.
- Submit only after the plan is complete. {approval_rule}
- Do not assume approval or describe a natural-language plan as submitted.{revision}"""

    if plan_state.phase is PlanPhase.AWAITING_APPROVAL:
        return """Plan phase: awaiting user approval.
- Do not modify files or run Bash.
- The model cannot approve its own plan; only the runtime user command can continue execution."""

    if plan_state.phase is PlanPhase.EXECUTING:
        current = next(
            (
                step
                for step in plan_state.steps
                if step.status is PlanStepStatus.IN_PROGRESS
            ),
            None,
        )
        current_text = (
            f"{current.id}: {current.description[:300]}"
            if current is not None
            else "none selected"
        )
        return f"""Plan phase: executing authorized plan version {plan_state.version}.
- Follow the approved plan and use update_plan to keep step status current.
- Current step: {current_text}
- Repository tools still pass through the existing Permission Gate.
- Run the smallest relevant verification after changes.
- For a major deviation, request_replan instead of silently replacing the plan.
- Mark all steps completed, then use update_plan action complete before the final response."""

    if plan_state.phase is PlanPhase.COMPLETED:
        return "Plan phase: completed. Provide the concise final task report now."

    if plan_state.phase is PlanPhase.CANCELLED:
        return "Plan phase: cancelled. Do not perform additional repository work."

    return f"Plan phase: {plan_state.phase.value}. Do not start unapproved repository work."


SYSTEM_PROMPT = build_system_prompt(Path.cwd())


def build_initial_messages(task: str) -> list[dict]:
    return [{"role": "user", "content": task}]
