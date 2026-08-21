from __future__ import annotations

from copy import deepcopy

from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig


class FinalOnlyModel:
    def __init__(self) -> None:
        self.max_tokens = 16_000
        self.context_window_tokens = 272_000
        self.calls = 0

    def call(self, system, messages, tools, *, max_tokens=None):
        assert max_tokens is None
        self.calls += 1
        text = f"completed-{self.calls}"
        return ModelResponse(
            message={"role": "assistant", "content": [{"type": "text", "text": text}]},
            text=text,
            usage=TokenUsage(input_tokens=100, output_tokens=10),
            stop_reason="end_turn",
        )


def test_task_boundary_is_append_only_below_pressure(tmp_path) -> None:
    model = FinalOnlyModel()
    runner = AgentLoop(
        model_client=model,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits"),
    )
    context = runner.start_interactive()

    runner.start_task(context, "first task")
    first_epoch = deepcopy(context.messages)
    generation = context.context_generation
    runner.start_task(context, "second task")

    assert context.messages[: len(first_epoch)] == first_epoch
    assert context.context_generation == generation == 0
    assert context.context_compactions == 0
    assert model.calls == 2


def test_frozen_context_config_has_no_removed_policy_fields() -> None:
    config = RunConfig()

    assert config.context_window_tokens == 272_000
    assert config.context_auto_compact_ratio == 0.90
    assert config.context_recent_raw_tokens == 64_000
    assert config.semantic_checkpoint_max_tokens == 8_192
    assert config.deterministic_checkpoint_max_tokens == 4_096
    assert config.context_post_rebase_ceiling_tokens == 136_000
    for removed in (
        "context_target_tokens",
        "context_soft_limit_ratio",
        "compact_threshold_chars",
        "context_recent_target_tokens",
        "context_recent_max_tokens",
        "context_min_recent_rounds",
        "context_checkpoint_max_chars",
        "context_task_boundary_tokens",
        "max_context_compaction_failures",
    ):
        assert not hasattr(config, removed)
