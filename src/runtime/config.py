from __future__ import annotations

from dataclasses import dataclass

from runtime.plan.models import PlanApprovalPolicy, PlanPolicy


@dataclass
class RunConfig:
    max_turns: int = 40
    verification_reserve_calls: int = 4
    planning_soft_limit_calls: int | None = None
    planning_hard_limit_calls: int | None = None
    plan_draft_grace_calls: int = 2
    plan_step_stall_calls: int = 4
    max_repair_attempts: int = 3
    max_tool_result_chars: int = 8000
    max_tool_round_tokens: int = 6000
    grep_max_matches: int = 50
    compact_threshold_chars: int = 120000
    context_window_tokens: int | None = None
    context_target_tokens: int | None = 32000
    context_eager_projection_tokens: int | None = 0
    source_working_set_max_tokens: int | None = None
    context_soft_limit_ratio: float = 0.8
    context_safety_margin_tokens: int = 4096
    context_recent_target_tokens: int = 8000
    context_recent_max_tokens: int = 16000
    context_min_recent_rounds: int = 2
    context_checkpoint_max_chars: int = 6000
    context_task_boundary_tokens: int = 12000
    max_context_recovery_attempts: int = 1
    max_context_compaction_failures: int = 3
    artifact_read_max_chars: int = 6000
    permission_mode: str = "manual_approval"
    sandbox_enabled: bool = False
    sandbox_auto_allow_bash: bool = True
    sandbox_fail_if_unavailable: bool = False
    sandbox_settings_path: str | None = None
    bash_env_allowlist: tuple[str, ...] = ()
    plan_policy: PlanPolicy | str = PlanPolicy.OFF
    plan_approval_policy: PlanApprovalPolicy | str = PlanApprovalPolicy.MANUAL

    def __post_init__(self) -> None:
        try:
            self.plan_policy = PlanPolicy(self.plan_policy)
        except ValueError as exc:
            allowed = ", ".join(policy.value for policy in PlanPolicy)
            raise ValueError(f"plan_policy must be one of: {allowed}") from exc
        try:
            self.plan_approval_policy = PlanApprovalPolicy(self.plan_approval_policy)
        except ValueError as exc:
            allowed = ", ".join(policy.value for policy in PlanApprovalPolicy)
            raise ValueError(
                f"plan_approval_policy must be one of: {allowed}"
            ) from exc
        if self.max_turns <= 0:
            raise ValueError("max_turns must be > 0")
        if self.verification_reserve_calls < 0:
            raise ValueError("verification_reserve_calls must be >= 0")
        if (
            self.planning_soft_limit_calls is not None
            and self.planning_soft_limit_calls <= 0
        ):
            raise ValueError("planning_soft_limit_calls must be > 0 when set")
        if (
            self.planning_hard_limit_calls is not None
            and self.planning_hard_limit_calls <= 0
        ):
            raise ValueError("planning_hard_limit_calls must be > 0 when set")
        if (
            self.planning_soft_limit_calls is not None
            and self.planning_hard_limit_calls is not None
            and self.planning_soft_limit_calls >= self.planning_hard_limit_calls
        ):
            raise ValueError(
                "planning_soft_limit_calls must be less than planning_hard_limit_calls"
            )
        if self.plan_draft_grace_calls <= 0:
            raise ValueError("plan_draft_grace_calls must be > 0")
        if self.plan_step_stall_calls <= 0:
            raise ValueError("plan_step_stall_calls must be > 0")
        if self.max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be >= 0")
        if self.max_tool_result_chars < 256:
            raise ValueError("max_tool_result_chars must be >= 256")
        if self.max_tool_round_tokens < 0:
            raise ValueError("max_tool_round_tokens must be >= 0")
        if self.grep_max_matches <= 0:
            raise ValueError("grep_max_matches must be > 0")
        if self.compact_threshold_chars <= 0:
            raise ValueError("compact_threshold_chars must be > 0")
        if self.context_window_tokens is not None and self.context_window_tokens <= 0:
            raise ValueError("context_window_tokens must be > 0")
        if self.context_target_tokens is not None and self.context_target_tokens <= 0:
            raise ValueError("context_target_tokens must be > 0")
        if (
            self.context_eager_projection_tokens is not None
            and self.context_eager_projection_tokens < 0
        ):
            raise ValueError("context_eager_projection_tokens must be >= 0")
        if (
            self.source_working_set_max_tokens is not None
            and self.source_working_set_max_tokens <= 0
        ):
            raise ValueError("source_working_set_max_tokens must be > 0 when set")
        if not 0 < self.context_soft_limit_ratio <= 1:
            raise ValueError("context_soft_limit_ratio must be between 0 and 1")
        if self.context_safety_margin_tokens < 0:
            raise ValueError("context_safety_margin_tokens must be >= 0")
        if self.context_recent_target_tokens <= 0:
            raise ValueError("context_recent_target_tokens must be > 0")
        if self.context_recent_max_tokens < self.context_recent_target_tokens:
            raise ValueError(
                "context_recent_max_tokens must be >= context_recent_target_tokens"
            )
        if self.context_min_recent_rounds <= 0:
            raise ValueError("context_min_recent_rounds must be > 0")
        if self.context_checkpoint_max_chars < 512:
            raise ValueError("context_checkpoint_max_chars must be >= 512")
        if self.context_task_boundary_tokens < 0:
            raise ValueError("context_task_boundary_tokens must be >= 0")
        if self.max_context_recovery_attempts < 0:
            raise ValueError("max_context_recovery_attempts must be >= 0")
        if self.max_context_compaction_failures <= 0:
            raise ValueError("max_context_compaction_failures must be > 0")
        if self.artifact_read_max_chars <= 0:
            raise ValueError("artifact_read_max_chars must be > 0")
