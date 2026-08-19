from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent.loop import AgentLoop
from agent.messages import TokenUsage, ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.context.budget import estimate_request_tokens
from runtime.context.checkpoint import RUNTIME_CHECKPOINT_PREFIX, RuntimeCheckpointBuilder
from runtime.context.manager import ContextManager


class DummyTrace:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def log(self, event: dict) -> None:
        self.events.append(event)


def tool_use_message(call_id: str, name: str = "read_file") -> dict:
    return {
        "role": "assistant",
        "content": [
            {
                "type": "tool_use",
                "id": call_id,
                "name": name,
                "input": {"path": "demo.py"},
            }
        ],
    }


def tool_result_message(call_id: str, content: str = "result", *, error=False) -> dict:
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": call_id,
                "content": content,
                **({"is_error": True} if error else {}),
            }
        ],
    }


def compact_config(**overrides) -> RunConfig:
    values = {
        "compact_threshold_chars": 1_000_000,
        "context_target_tokens": 4_000,
        "context_recent_target_tokens": 400,
        "context_recent_max_tokens": 3_000,
        "context_min_recent_rounds": 2,
        "context_checkpoint_max_chars": 6_000,
        "context_task_boundary_tokens": 0,
    }
    values.update(overrides)
    return RunConfig(**values)


def simple_context(messages: list[dict], **overrides) -> SimpleNamespace:
    return SimpleNamespace(
        task="repair the project",
        messages=messages,
        config=compact_config(**overrides),
        trace=DummyTrace(),
        task_changed_files={"demo.py"},
        task_created_files=set(),
        mutation_version=2,
        task_unresolved_mutation_failure=False,
        task_test_result={"command": "pytest", "ok": True, "mutation_version": 2},
        task_verification_version=2,
        completed_tasks=[],
        context_generation=0,
        context_compactions=0,
        context_compaction_failures=0,
        context_recovery_attempts=0,
        last_model_consumed_message_count=0,
        last_model_usage=None,
        last_model_usage_message_index=None,
        last_model_usage_generation=None,
        tool_result_artifacts={},
        tool_result_provenance={},
        tool_result_metadata={},
        read_file_segments={},
    )


def long_history(rounds: int = 8, *, result_chars: int = 4_000) -> list[dict]:
    messages = [{"role": "user", "content": "inspect and repair"}]
    for index in range(rounds):
        call_id = f"call-{index}"
        messages.append(tool_use_message(call_id))
        messages.append(tool_result_message(call_id, f"result-{index} " + ("x" * result_chars)))
    return messages


def checkpoint_payload(value: str) -> dict:
    return json.loads(value[value.index("{") :])


def test_below_pressure_normal_epoch_is_append_only() -> None:
    messages = [
        {"role": "user", "content": "inspect"},
        {"role": "assistant", "content": [{"type": "text", "text": "working"}]},
    ]
    context = simple_context(messages, context_target_tokens=100_000)
    before = deepcopy(messages)

    preparation = ContextManager().prepare_context(context)

    assert preparation.changed is False
    assert context.messages == before
    assert context.context_generation == 0
    assert not any(event["type"].startswith("context_compact") for event in context.trace.events)


def test_inclusive_cache_usage_does_not_create_false_context_pressure() -> None:
    context = simple_context(
        [{"role": "user", "content": "inspect"}],
        context_target_tokens=48_000,
    )
    context.last_model_usage = TokenUsage(
        input_tokens=26_061,
        output_tokens=33,
        cache_read_input_tokens=25_088,
    )
    context.last_model_usage_message_index = 0
    context.last_model_usage_generation = 0

    preparation = ContextManager().prepare_context(context)

    assert preparation.changed is False
    assert preparation.measurement.provider_tokens == 26_094
    assert preparation.measurement.trigger_reason is None


def test_admission_shapes_oversized_batch_before_context_visibility(tmp_path) -> None:
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", max_tool_round_tokens=300),
    )
    context = runner.create_context("inspect")
    calls = [ToolCall("one", "grep", {}), ToolCall("two", "grep", {})]
    results = [("one", "a" * 5_000, False), ("two", "b" * 5_000, True)]
    before = deepcopy(context.messages)

    admitted = runner.runtime.context_manager.admit_tool_results(context, calls, results)

    assert context.messages == before
    assert context.context_generation == 0
    assert all("artifact_id:" in content for _, content, _ in admitted)
    assert admitted[1][2] is True
    assert len(list((context.run_dir / "artifacts").glob("*.txt"))) == 2


def test_admission_prefers_non_source_and_uses_source_only_as_last_resort(tmp_path) -> None:
    source = tmp_path / "demo.py"
    source.write_text("\n".join(f"value_{line} = {line}" for line in range(500)))
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", max_tool_round_tokens=5_000),
    )
    context = runner.create_context("inspect")
    source_result = runner.runtime.executor.execute(
        ToolCall("source", "read_file", {"path": "demo.py"}),
        context,
    )
    calls = [ToolCall("source", "read_file", {}), ToolCall("grep", "grep", {})]
    admitted = runner.runtime.context_manager.admit_tool_results(
        context,
        calls,
        [("source", source_result.content, False), ("grep", "match\n" * 2_000, False)],
    )
    rendered = {identifier: content for identifier, content, _ in admitted}

    assert "Source observation compacted" not in rendered["source"]
    assert "artifact_id:" in rendered["grep"]

    context.config.max_tool_round_tokens = 100
    admitted = runner.runtime.context_manager.admit_tool_results(
        context,
        [ToolCall("source-2", "read_file", {})],
        [("source-2", source_result.content, False)],
    )
    assert "Source observation compacted" in admitted[0][1]


def test_pressure_commits_at_most_one_history_rewrite() -> None:
    context = simple_context(long_history())
    context.last_model_consumed_message_count = len(context.messages)

    preparation = ContextManager().prepare_context(context)

    rewrite_events = [
        event
        for event in context.trace.events
        if event.get("type") in {"context_tool_results_projected", "context_compact"}
    ]
    assert preparation.changed is True
    assert len(rewrite_events) == 1
    assert context.context_generation == 1


def test_full_rebase_preserves_complete_rounds_and_decisively_reduces_context() -> None:
    context = simple_context(long_history())
    before = estimate_request_tokens("", context.messages, [])

    preparation = ContextManager().prepare_context(context)
    after = estimate_request_tokens("", context.messages, [])

    assert preparation.compacted is True
    assert context.messages[0]["content"].startswith(RUNTIME_CHECKPOINT_PREFIX)
    assert _tool_protocol_is_paired(context.messages)
    assert after < before
    assert after <= int(context.config.context_target_tokens * 0.65)
    assert before - after >= int(before * 0.50)
    event = next(event for event in context.trace.events if event.get("type") == "context_compact")
    assert event["saved_tokens"] == event["before_tokens"] - event["after_tokens"]


def test_checkpoint_only_prefix_is_not_rebased_again() -> None:
    messages = [
        {"role": "user", "content": f"{RUNTIME_CHECKPOINT_PREFIX}\n{{}}"},
        {"role": "assistant", "content": [{"type": "text", "text": "recent"}]},
    ]
    context = simple_context(messages, context_target_tokens=1)
    before = deepcopy(messages)

    preparation = ContextManager().prepare_context(context, force=True)

    assert preparation.changed is False
    assert context.messages == before
    assert context.context_generation == 0


def test_rebase_without_local_gain_is_rejected() -> None:
    class LargeCheckpointBuilder:
        def build(self, context, old_messages) -> str:
            return f"{RUNTIME_CHECKPOINT_PREFIX}\n" + ("x" * 100_000)

    context = simple_context(long_history(rounds=3, result_chars=50))
    before = deepcopy(context.messages)

    preparation = ContextManager(checkpoint_builder=LargeCheckpointBuilder()).prepare_context(
        context,
        force=True,
    )

    assert preparation.changed is False
    assert context.messages == before
    assert context.context_generation == 0


@pytest.mark.parametrize(
    ("target", "maximum"),
    [(12_000, 24_000), (32_000, 64_000), (64_000, 96_000), (96_000, 128_000), (136_000, 160_000)],
    ids=("R0", "R1", "R2", "R3", "R4"),
)
def test_calibration_profiles_select_minimum_bounded_raw_tail(target, maximum) -> None:
    messages = [{"role": "user", "content": "start"}]
    for index in range(30):
        messages.extend(
            [
                tool_use_message(f"call-{index}"),
                tool_result_message(f"call-{index}", "x" * 20_000),
            ]
        )
    context = simple_context(
        messages,
        context_recent_target_tokens=target,
        context_recent_max_tokens=maximum,
        context_checkpoint_max_chars=12_000,
    )
    manager = ContextManager()
    groups = manager._group_messages_by_api_round(messages)

    selected = manager._select_recent_groups(groups, context.config)

    selected_tokens = sum(group.tokens for group in selected)
    assert len(selected) >= context.config.context_min_recent_rounds
    assert target <= selected_tokens <= maximum
    if len(selected) > context.config.context_min_recent_rounds:
        assert selected_tokens - selected[0].tokens < target


def test_recent_tail_rejects_required_boundary_group_above_maximum() -> None:
    messages = [
        {"role": "user", "content": "start"},
        tool_use_message("large"),
        tool_result_message("large", "x" * 20_000),
    ]
    context = simple_context(
        messages,
        context_recent_target_tokens=100,
        context_recent_max_tokens=200,
        context_min_recent_rounds=1,
    )
    manager = ContextManager()

    assert (
        manager._select_recent_groups(
            manager._group_messages_by_api_round(messages),
            context.config,
        )
        == []
    )


def test_three_checkpoint_generations_consolidate_semantic_state() -> None:
    context = simple_context([{"role": "user", "content": "initial constraint"}])
    plan_payload = {
        "phase": "executing",
        "current_step": {"id": "verify", "step": "verify changes"},
        "pending_steps": [{"id": "obsolete", "step": "obsolete work"}],
    }
    context.plan_state = SimpleNamespace(
        checkpoint_summary=lambda pending_limit: {
            **plan_payload,
            "pending_steps": plan_payload["pending_steps"][:pending_limit],
        }
    )
    builder = RuntimeCheckpointBuilder()
    checkpoint = builder.build(
        context,
        [
            {"role": "user", "content": "initial constraint"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "Choose deterministic parsing."},
                    {
                        "type": "tool_use",
                        "id": "edit-1",
                        "name": "edit_file",
                        "input": {"path": "demo.py"},
                    },
                ],
            },
            tool_result_message("edit-1", "old_text not found", error=True),
        ],
    )
    plan_payload.update(
        {
            "phase": "executing",
            "current_step": {"id": "verify", "step": "verify changes"},
            "pending_steps": [{"id": "docs", "step": "update docs"}],
        }
    )
    for correction, finding in (
        ("Keep the public API stable.", "The parser is shared by two callers."),
        ("Do not add a compatibility mode.", "Static verification now passes."),
    ):
        checkpoint = builder.build(
            context,
            [
                {"role": "user", "content": checkpoint},
                {"role": "user", "content": correction},
                {"role": "assistant", "content": [{"type": "text", "text": finding}]},
            ],
        )

    payload = checkpoint_payload(checkpoint)
    assert payload["user_constraints"] == ["initial constraint"]
    assert "Do not add a compatibility mode." in payload["user_corrections"]
    assert any("edit_file demo.py" in value for value in payload["decisions"])
    assert any("old_text not found" in value for value in payload["failures"])
    assert "Static verification now passes." in payload["findings"]
    assert payload["plan"]["phase"] == "executing"
    assert "update docs" in payload["pending_work"]
    assert "obsolete work" not in payload["pending_work"]
    assert len(checkpoint) <= context.config.context_checkpoint_max_chars


def test_checkpoint_reports_bounded_omissions_without_runtime_state() -> None:
    context = simple_context([{"role": "user", "content": "constraint"}])
    messages = [{"role": "user", "content": "constraint"}]
    messages.extend({"role": "user", "content": f"correction-{index}"} for index in range(12))

    payload = checkpoint_payload(RuntimeCheckpointBuilder().build(context, messages))

    assert len(payload["user_corrections"]) == 8
    assert payload["omitted_counts"]["user_corrections"] == 4
    assert not hasattr(context, "checkpoint_omitted_counts")


def test_checkpoint_hard_limit_survives_large_runtime_state() -> None:
    context = simple_context(
        [{"role": "user", "content": "initial prompt"}],
        context_checkpoint_max_chars=512,
    )
    context.task = "repair " + ("a detailed task " * 200)
    context.task_changed_files = {f"src/{index:04d}-{'x' * 100}.py" for index in range(200)}

    checkpoint = RuntimeCheckpointBuilder().build(context, context.messages)

    assert checkpoint.startswith(RUNTIME_CHECKPOINT_PREFIX)
    assert len(checkpoint) <= 512
    assert '"runtime_state"' in checkpoint


def test_compaction_circuit_breaker_contains_checkpoint_failures() -> None:
    class FailingCheckpointBuilder:
        def __init__(self) -> None:
            self.calls = 0

        def build(self, context, old_messages):
            self.calls += 1
            raise RuntimeError("checkpoint unavailable")

    context = simple_context(long_history(), max_context_compaction_failures=2)
    builder = FailingCheckpointBuilder()
    manager = ContextManager(checkpoint_builder=builder)
    before = deepcopy(context.messages)

    manager.prepare_context(context)
    manager.prepare_context(context)
    manager.prepare_context(context)

    assert builder.calls == 2
    assert context.context_compaction_failures == 2
    assert context.messages == before
    assert any(event.get("reason") == "circuit_breaker" for event in context.trace.events)


def _tool_protocol_is_paired(messages: list[dict]) -> bool:
    pending: set[str] = set()
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                pending.add(str(block.get("id")))
            elif block.get("type") == "tool_result":
                tool_use_id = str(block.get("tool_use_id"))
                if tool_use_id not in pending:
                    return False
                pending.remove(tool_use_id)
    return not pending
