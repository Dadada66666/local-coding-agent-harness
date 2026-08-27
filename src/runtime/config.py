from __future__ import annotations

from dataclasses import dataclass

from runtime.plan.models import PlanApprovalPolicy, PlanPolicy


@dataclass
class RunConfig:
    max_turns: int = 40
    max_repair_attempts: int = 3
    max_tool_result_chars: int = 18000
    max_tool_round_tokens: int = 12000
    grep_max_matches: int = 50
    context_window_tokens: int = 272000
    context_auto_compact_ratio: float = 0.90
    context_safety_margin_tokens: int = 4096
    context_recent_raw_tokens: int = 64000
    semantic_checkpoint_max_tokens: int = 8192
    deterministic_checkpoint_max_tokens: int = 4096
    context_post_rebase_ceiling_tokens: int = 136000
    max_context_recovery_attempts: int = 1
    artifact_read_max_chars: int = 6000
    permission_mode: str = "manual_approval"
    permission_prompt_policy: str = "interactive"
    sandbox_enabled: bool = False
    sandbox_auto_allow_bash: bool = True
    sandbox_fail_if_unavailable: bool = False
    sandbox_settings_path: str | None = None
    bash_env_allowlist: tuple[str, ...] = ()
    mcp_config_path: str | None = None
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
        if self.max_repair_attempts < 0:
            raise ValueError("max_repair_attempts must be >= 0")
        if not 256 <= self.max_tool_result_chars <= 18000:
            raise ValueError("max_tool_result_chars must be between 256 and 18000")
        if not 0 < self.max_tool_round_tokens <= 12000:
            raise ValueError("max_tool_round_tokens must be between 1 and 12000")
        if self.grep_max_matches <= 0:
            raise ValueError("grep_max_matches must be > 0")
        if not 0 < self.context_window_tokens <= 272000:
            raise ValueError("context_window_tokens must be between 1 and 272000")
        if not 0 < self.context_auto_compact_ratio <= 0.90:
            raise ValueError("context_auto_compact_ratio must be between 0 and 0.90")
        if self.context_safety_margin_tokens < 4096:
            raise ValueError("context_safety_margin_tokens must be >= 4096")
        if not 0 < self.context_recent_raw_tokens <= 64000:
            raise ValueError("context_recent_raw_tokens must be between 1 and 64000")
        if not 0 < self.semantic_checkpoint_max_tokens <= 8192:
            raise ValueError("semantic_checkpoint_max_tokens must be between 1 and 8192")
        if not 0 < self.deterministic_checkpoint_max_tokens <= 4096:
            raise ValueError("deterministic_checkpoint_max_tokens must be between 1 and 4096")
        if not 0 < self.context_post_rebase_ceiling_tokens <= 136000:
            raise ValueError("context_post_rebase_ceiling_tokens must be between 1 and 136000")
        if self.max_context_recovery_attempts < 0:
            raise ValueError("max_context_recovery_attempts must be >= 0")
        if self.artifact_read_max_chars <= 0:
            raise ValueError("artifact_read_max_chars must be > 0")
        if self.permission_prompt_policy not in {"interactive", "deny"}:
            raise ValueError(
                'permission_prompt_policy must be either "interactive" or "deny"'
            )
