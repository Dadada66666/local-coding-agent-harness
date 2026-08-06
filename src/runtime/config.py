from __future__ import annotations

from dataclasses import dataclass

from runtime.plan.models import PlanPolicy


@dataclass
class RunConfig:
    max_turns: int = 40
    max_repair_attempts: int = 3
    max_tool_result_chars: int = 8000
    max_tool_round_tokens: int = 6000
    grep_max_matches: int = 50
    compact_threshold_chars: int = 120000
    context_window_tokens: int | None = None
    context_target_tokens: int | None = 32000
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

    def __post_init__(self) -> None:
        try:
            self.plan_policy = PlanPolicy(self.plan_policy)
        except ValueError as exc:
            allowed = ", ".join(policy.value for policy in PlanPolicy)
            raise ValueError(f"plan_policy must be one of: {allowed}") from exc
        if self.max_turns <= 0:
            raise ValueError("max_turns must be > 0")
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
