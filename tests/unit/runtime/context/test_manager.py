from __future__ import annotations

from copy import deepcopy
import json
import pytest

from agent.loop import AgentLoop
from agent.messages import ModelResponse, TokenUsage, ToolCall
from runtime.bootstrap import build_runtime
from runtime.config import RunConfig
from runtime.context.budget import (
    estimate_input_tokens,
    estimate_text_tokens,
    estimate_value_tokens,
)
from runtime.context.checkpoint import (
    CONTEXT_CHECKPOINT_PREFIX,
    MANDATORY_SEMANTIC_HEADINGS,
    RuntimeCheckpointBuilder,
)
from runtime.context.manager import ContextManager
from runtime.context.projection import ToolResultAdmissionError


VALID_HANDOFF = "\n\n".join(f"{heading}\n\n- None." for heading in MANDATORY_SEMANTIC_HEADINGS)


class ScriptedSemanticClient:
    def __init__(self, outputs: list[object] | None = None) -> None:
        self.outputs = list(outputs or [VALID_HANDOFF])
        self.calls: list[dict] = []
        self.max_tokens = 16_000
        self.context_window_tokens = 272_000

    def call(self, system, messages, tools, *, max_tokens=None):
        self.calls.append(
            {
                "system": system,
                "messages": deepcopy(messages),
                "tools": deepcopy(tools),
                "max_tokens": max_tokens,
            }
        )
        output = self.outputs.pop(0)
        if isinstance(output, BaseException):
            raise output
        return ModelResponse(
            message={"role": "assistant", "content": [{"type": "text", "text": output}]},
            text=str(output),
            usage=TokenUsage(input_tokens=100, output_tokens=50),
            stop_reason="end_turn",
        )


def make_context(tmp_path, *, config: RunConfig | None = None):
    client = ScriptedSemanticClient()
    runner = AgentLoop(
        model_client=client,
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=config or RunConfig(permission_mode="accept_edits"),
    )
    return runner.create_context("inspect", include_initial_message=True), client


def add_round(context, index: int, *, chars: int = 4_000, tool: str = "read_file") -> None:
    call_id = f"call-{index}"
    context.add_assistant_message(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call_id,
                    "name": tool,
                    "input": {"path": "demo.py"},
                }
            ],
        }
    )
    context.add_tool_results([(call_id, f"result-{index} " + ("x" * chars), False)])


def test_below_pressure_epoch_is_exactly_append_only(tmp_path) -> None:
    context, client = make_context(tmp_path)
    before_messages = deepcopy(context.messages)
    before_audit = deepcopy(context.conversation_messages)

    preparation = ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        max_output_tokens=16_000,
        model_client=client,
    )

    assert preparation.changed is False
    assert context.messages == before_messages
    assert context.conversation_messages == before_audit
    assert context.context_generation == 0
    assert client.calls == []


def test_admission_shapes_oversized_batch_before_context_visibility(tmp_path) -> None:
    context, _client = make_context(
        tmp_path,
        config=RunConfig(permission_mode="accept_edits", max_tool_round_tokens=300),
    )
    calls = [ToolCall("one", "grep", {}), ToolCall("two", "grep", {})]
    results = [("one", "a" * 5_000, False), ("two", "b" * 5_000, True)]
    before = deepcopy(context.messages)

    admitted = ContextManager().admit_tool_results(context, calls, results)

    assert context.messages == before
    assert context.context_generation == 0
    assert all("artifact_id:" in content for _, content, _ in admitted)
    assert admitted[1][2] is True


def test_admission_rejects_batch_when_minimum_form_exceeds_hard_budget(tmp_path) -> None:
    context, _client = make_context(
        tmp_path,
        config=RunConfig(permission_mode="accept_edits", max_tool_round_tokens=1),
    )
    before_messages = deepcopy(context.messages)
    before_audit = deepcopy(context.conversation_messages)

    with pytest.raises(ToolResultAdmissionError, match="hard round budget"):
        ContextManager().admit_tool_results(
            context,
            [ToolCall("one", "grep", {})],
            [("one", "x", False)],
        )

    assert context.messages == before_messages
    assert context.conversation_messages == before_audit
    assert context.context_generation == 0


def test_admission_prefers_non_source_and_uses_source_only_as_last_resort(tmp_path) -> None:
    source_path = tmp_path / "demo.py"
    source_path.write_text("\n".join(f"value_{line} = {line}" for line in range(500)))
    runtime = build_runtime()
    runner = AgentLoop(
        model_client=object(),
        runtime=runtime,
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", max_tool_round_tokens=5_000),
    )
    context = runner.create_context("inspect", include_initial_message=True)
    source_result = runtime.executor.execute(
        ToolCall("source", "read_file", {"path": "demo.py"}),
        context,
    )
    calls = [ToolCall("source", "read_file", {}), ToolCall("grep", "grep", {})]

    admitted = runtime.context_manager.admit_tool_results(
        context,
        calls,
        [("source", source_result.content, False), ("grep", "match\n" * 2_000, False)],
    )
    rendered = {identifier: content for identifier, content, _ in admitted}

    assert "Source observation compacted" not in rendered["source"]
    assert "artifact_id:" in rendered["grep"]
    assert _admitted_tokens(admitted) <= context.config.max_tool_round_tokens

    context.config.max_tool_round_tokens = 100
    context.record_tool_result_metadata("source-2", source_result.metadata)
    admitted = runtime.context_manager.admit_tool_results(
        context,
        [ToolCall("source-2", "read_file", {})],
        [("source-2", source_result.content, False)],
    )

    assert "Source observation compacted" in admitted[0][1]
    assert _admitted_tokens(admitted) <= context.config.max_tool_round_tokens
    assert context.source_read_metrics.source_observations_projected == 1
    context.mark_source_observation_projected("source-2", source_result.metadata)
    assert context.source_read_metrics.source_observations_projected == 1


def test_semantic_rebase_selects_final_rounds_before_summary(tmp_path) -> None:
    config = RunConfig(
        permission_mode="accept_edits",
        context_recent_raw_tokens=1_500,
        context_post_rebase_ceiling_tokens=20_000,
    )
    context, client = make_context(tmp_path, config=config)
    for index in range(5):
        add_round(context, index, chars=2_000)
    audit_before = deepcopy(context.conversation_messages)

    preparation = ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        max_output_tokens=16_000,
        force=True,
        reason="explicit",
        model_client=client,
    )

    assert preparation.compacted is True
    assert len(client.calls) == 1
    assert client.calls[0]["tools"] == []
    assert context.messages[0]["content"].startswith(CONTEXT_CHECKPOINT_PREFIX)
    assert context.conversation_messages == audit_before
    assert context.context_generation == 1
    checkpoint = context.messages[0]["content"]
    deterministic = json.loads(
        checkpoint.split("AUTHORITATIVE_RUNTIME_STATE:\n", 1)[1].split("\n\nSEMANTIC_HANDOFF:", 1)[
            0
        ]
    )
    assert deterministic["context_generation"] == context.context_generation
    assert deterministic["history_recovery"]["current_window_id"] == (
        context.history_window_id(context.context_generation)
    )
    assert deterministic["history_windows"] == context.history_windows()
    assert context.context_compactions == 1
    assert _tool_protocol_is_paired(context.messages)
    event = next(
        event for event in context.cost_tracker.context_events if event["type"] == "context_rebase"
    )
    assert event["local_input_tokens_after"] < event["local_input_tokens_before"]
    assert event["local_input_tokens_after"] <= config.context_post_rebase_ceiling_tokens


def test_semantic_input_preserves_trust_boundaries_and_all_removed_items(tmp_path) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_recent_raw_tokens=1,
            context_post_rebase_ceiling_tokens=20_000,
        ),
    )
    context.add_user_message({"role": "user", "content": "Use B, not A."})
    add_round(context, 1, chars=1_000, tool="grep")

    ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=client,
    )

    semantic_text = client.calls[0]["messages"][0]["content"]
    assert "AUTHORITATIVE_USER_INTENT" in semantic_text
    assert "Use B, not A." in semantic_text
    assert "DERIVED_AGENT_REASONING" in semantic_text
    assert "UNTRUSTED_EXTERNAL_EVIDENCE" in semantic_text
    for ordinal in range(len(context.conversation_messages)):
        assert context.audit_item_id(ordinal) in semantic_text


def test_shared_checkpoint_budget_controls_semantic_output_limit(tmp_path) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_recent_raw_tokens=1,
            context_post_rebase_ceiling_tokens=20_000,
        ),
    )
    add_round(context, 1)

    ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=client,
    )

    deterministic = RuntimeCheckpointBuilder().build_authoritative_state(context)
    wrapper_tokens = RuntimeCheckpointBuilder().checkpoint_wrapper_tokens()
    expected = min(
        context.config.semantic_checkpoint_max_tokens,
        context.config.semantic_checkpoint_max_tokens
        + context.config.deterministic_checkpoint_max_tokens
        - deterministic.actual_tokens
        - wrapper_tokens,
    )
    assert client.calls[0]["max_tokens"] == expected
    assert estimate_text_tokens(context.messages[0]["content"]) <= 12_288


@pytest.mark.parametrize(
    "invalid",
    [
        "",
        "## CONFIRMED\n\n- None.",
        VALID_HANDOFF.replace("## UNRESOLVED", "## CONFIRMED"),
        "\n\n".join(reversed(VALID_HANDOFF.split("\n\n"))),
    ],
)
def test_invalid_semantic_output_never_commits_under_normal_pressure(tmp_path, invalid) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_recent_raw_tokens=1,
            context_post_rebase_ceiling_tokens=20_000,
        ),
    )
    client.outputs = [invalid]
    add_round(context, 1)
    before = deepcopy(context.messages)

    preparation = ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=client,
    )

    assert preparation.changed is False
    assert context.messages == before
    assert context.context_generation == 0


def test_one_normal_semantic_failure_per_generation(tmp_path) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_window_tokens=272_000,
            context_recent_raw_tokens=1,
            context_post_rebase_ceiling_tokens=136_000,
        ),
    )
    client.outputs = [RuntimeError("semantic unavailable")]
    add_round(context, 1, chars=980_000)
    manager = ContextManager()

    manager.prepare_context(context, system="system", tools=[], model_client=client)
    manager.prepare_context(context, system="system", tools=[], model_client=client)

    assert len(client.calls) == 1
    assert context.last_auto_compaction_failed_generation == 0
    assert context.context_generation == 0


def test_hard_pressure_bypasses_guard_and_uses_deterministic_emergency(tmp_path) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_window_tokens=50_000,
            context_recent_raw_tokens=1_000,
            context_post_rebase_ceiling_tokens=20_000,
            deterministic_checkpoint_max_tokens=800,
            semantic_checkpoint_max_tokens=1_600,
        ),
    )
    context.last_auto_compaction_failed_generation = 0
    client.outputs = [RuntimeError("semantic unavailable")]
    for index in range(3):
        add_round(context, index, chars=50_000)

    preparation = ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        model_client=client,
    )

    assert preparation.compacted is True
    assert context.context_generation == 1
    assert "Unavailable after hard-pressure compaction failure" in context.messages[0]["content"]
    assert _tool_protocol_is_paired(context.messages)
    event = next(
        event for event in context.cost_tracker.context_events if event["type"] == "context_rebase"
    )
    failure = next(
        event
        for event in context.cost_tracker.context_events
        if event["type"] == "context_rebase_failure"
    )
    assert event["emergency_fallback"] is True
    assert failure["failure_reason"] == "provider_call"
    assert failure["normal_guard_bypassed"] is True
    assert event["local_input_tokens_after"] <= 20_000


def test_rebase_candidate_rejection_is_atomic(tmp_path) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_recent_raw_tokens=64_000,
            context_post_rebase_ceiling_tokens=136_000,
        ),
    )
    add_round(context, 1, chars=100)
    before_messages = deepcopy(context.messages)
    before_audit = deepcopy(context.conversation_messages)

    preparation = ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=client,
    )

    assert preparation.changed is False
    assert context.messages == before_messages
    assert context.conversation_messages == before_audit
    assert context.context_generation == 0


def test_semantic_preflight_is_input_only(tmp_path) -> None:
    context, client = make_context(tmp_path)
    builder = RuntimeCheckpointBuilder()
    semantic_input = builder.build_semantic_input(
        context,
        removed_items=[],
        previous_semantic_handoff=None,
        authoritative_state=builder.build_authoritative_state(context),
    )
    system = builder.semantic_system_prompt()

    measured = estimate_input_tokens(system, semantic_input, [])
    limit = context.config.context_window_tokens - 8_192 - 4_096

    assert measured == estimate_input_tokens(system, semantic_input, [])
    assert measured < limit
    assert client.calls == []


def test_parallel_tool_round_is_retained_complete(tmp_path) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_recent_raw_tokens=2_000,
            context_post_rebase_ceiling_tokens=20_000,
        ),
    )
    context.add_user_message({"role": "user", "content": "old " * 5_000})
    context.add_assistant_message(
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "a", "name": "grep", "input": {}},
                {"type": "tool_use", "id": "b", "name": "grep", "input": {}},
            ],
        }
    )
    context.add_tool_results([("a", "ok", False), ("b", "failed", True)])

    preparation = ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=client,
    )

    assert preparation.compacted is True
    assert _tool_protocol_is_paired(context.messages)
    rendered = json.dumps(context.messages, sort_keys=True)
    assert '"id": "a"' in rendered and '"tool_use_id": "a"' in rendered
    assert '"id": "b"' in rendered and '"tool_use_id": "b"' in rendered


def test_protocol_validator_accepts_parallel_tool_results() -> None:
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "a", "name": "grep", "input": {}},
                {"type": "tool_use", "id": "b", "name": "grep", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "one"},
                {"type": "tool_result", "tool_use_id": "b", "content": "two"},
            ],
        },
    ]

    assert ContextManager()._protocol_is_complete(messages) is True


@pytest.mark.parametrize(
    "messages",
    [
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "id": "a", "name": "grep", "input": {}},
                    {"type": "tool_use", "id": "b", "name": "grep", "input": {}},
                ],
            },
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "a", "content": "one"}],
            },
        ],
        [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "a", "name": "grep", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "a", "content": "one"},
                    {"type": "tool_result", "tool_use_id": "c", "content": "orphan"},
                ],
            },
        ],
        [
            {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "a", "name": "grep", "input": {}}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "a", "content": "one"},
                    {"type": "tool_result", "tool_use_id": "a", "content": "duplicate"},
                ],
            },
        ],
        [
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "a", "content": "orphan"}],
            }
        ],
    ],
    ids=("missing", "orphan", "duplicate", "standalone"),
)
def test_protocol_validator_rejects_invalid_tool_results(messages) -> None:
    assert ContextManager()._protocol_is_complete(messages) is False


def test_repeated_rebases_consolidate_one_previous_checkpoint(tmp_path) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_recent_raw_tokens=1,
            context_post_rebase_ceiling_tokens=20_000,
        ),
    )
    client.outputs = [VALID_HANDOFF, VALID_HANDOFF]
    for index in range(3):
        add_round(context, index, chars=4_000)
    manager = ContextManager()

    first = manager.prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=client,
    )
    for index in range(3, 6):
        add_round(context, index, chars=4_000)
    second = manager.prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=client,
    )

    assert first.compacted is True and second.compacted is True
    assert context.context_generation == 2
    assert len(context.history_windows()) == 3
    assert (
        sum(
            isinstance(message.get("content"), str)
            and message["content"].startswith(CONTEXT_CHECKPOINT_PREFIX)
            for message in context.messages
        )
        == 1
    )
    assert VALID_HANDOFF in client.calls[1]["messages"][0]["content"]


def test_semantic_usage_is_separate_from_main_provider_anchor(tmp_path) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_recent_raw_tokens=1,
        ),
    )
    add_round(context, 1, chars=20)
    anchor = TokenUsage(input_tokens=321, output_tokens=12)
    context.last_model_usage = anchor
    context.last_model_usage_message_index = 0
    context.last_model_usage_generation = 0
    context.last_model_usage_local_input_tokens = 100

    preparation = ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=client,
    )

    assert preparation.changed is False
    assert context.last_model_usage is anchor
    assert context.last_model_usage_message_index == 0
    assert context.cost_tracker.compaction_calls == 1


def test_normal_semantic_preflight_failure_is_suppressed_for_generation(tmp_path) -> None:
    context, client = make_context(tmp_path)
    for index in range(6_200):
        context.add_user_message({"role": "user", "content": f"item-{index}:" + ("x" * 120)})
    manager = ContextManager()

    first = manager.prepare_context(
        context,
        system="system",
        tools=[],
        model_client=client,
    )
    second = manager.prepare_context(
        context,
        system="system",
        tools=[],
        model_client=client,
    )

    assert first.failure_reason == "semantic_preflight"
    assert second.changed is False
    assert context.last_auto_compaction_failed_generation == 0
    assert client.calls == []
    failures = [
        event
        for event in context.cost_tracker.context_events
        if event["type"] == "context_rebase_failure"
    ]
    assert len(failures) == 1


def test_explicit_failure_bypasses_guard_without_changing_it(tmp_path) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_recent_raw_tokens=1,
        ),
    )
    context.last_auto_compaction_failed_generation = 0
    client.outputs = [RuntimeError("unavailable")]
    add_round(context, 1, chars=2_000)

    preparation = ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=client,
    )

    assert preparation.changed is False
    assert len(client.calls) == 1
    assert context.context_generation == 0
    assert context.last_auto_compaction_failed_generation == 0


def test_emergency_budget_and_history_ranges_are_frozen_before_tail_selection(
    tmp_path,
) -> None:
    context, client = make_context(
        tmp_path,
        config=RunConfig(
            permission_mode="accept_edits",
            context_window_tokens=50_000,
            context_recent_raw_tokens=1_000,
            context_post_rebase_ceiling_tokens=20_000,
        ),
    )
    for index in range(3):
        add_round(context, index, chars=80_000)

    preparation = ContextManager().prepare_context(
        context,
        system="system",
        tools=[],
        model_client=client,
    )

    assert preparation.compacted is True
    assert client.calls == []
    event = next(
        event for event in context.cost_tracker.context_events if event["type"] == "context_rebase"
    )
    failure = next(
        event
        for event in context.cost_tracker.context_events
        if event["type"] == "context_rebase_failure"
    )
    assert event["final_raw_input_tokens"] == 0
    assert failure["failure_reason"] == "semantic_preflight"
    checkpoint = context.messages[0]["content"]
    deterministic_text = checkpoint.split("AUTHORITATIVE_RUNTIME_STATE:\n", 1)[1].split(
        "\n\nSEMANTIC_HANDOFF:", 1
    )[0]
    deterministic = json.loads(deterministic_text)
    assert deterministic["history_recovery"]["removed_ranges"]
    assert deterministic["context_generation"] == context.context_generation
    assert deterministic["history_recovery"]["current_window_id"] == (
        context.history_window_id(context.context_generation)
    )


def test_full_rebase_source_recovery_uses_read_file_and_history(tmp_path) -> None:
    path = tmp_path / "demo.py"
    path.write_text("\n".join(f"value_{index} = {index}" for index in range(100)))
    runtime = build_runtime()
    runner = AgentLoop(
        model_client=ScriptedSemanticClient(),
        runtime=runtime,
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(
            permission_mode="accept_edits",
            context_recent_raw_tokens=1,
            context_post_rebase_ceiling_tokens=20_000,
        ),
    )
    context = runner.create_context("inspect", include_initial_message=True)
    call = ToolCall("source", "read_file", {"path": "demo.py"})
    context.add_assistant_message(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": call.id,
                    "name": call.name,
                    "input": call.arguments,
                }
            ],
        }
    )
    source = runtime.executor.execute(call, context)
    context.add_tool_result(call.id, source.content)
    context.add_user_message({"role": "user", "content": "old evidence " * 4_000})

    preparation = runtime.context_manager.prepare_context(
        context,
        system="system",
        tools=[],
        force=True,
        reason="explicit",
        model_client=runner.model_client,
    )
    rehydrated = runtime.executor.execute(
        ToolCall("source-again", "read_file", {"path": "demo.py"}),
        context,
    )
    history = runtime.tool_registry.get("history_search_contents").call(
        {"query": "value_50 = 50"},
        context,
    )

    assert preparation.compacted is True
    assert rehydrated.ok is True
    assert rehydrated.metadata["rehydration"] is True
    assert json.loads(history.content)["matches"]


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


def _admitted_tokens(results: list[tuple[str, str, bool]]) -> int:
    return sum(
        estimate_value_tokens(
            {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
                **({"is_error": True} if is_error else {}),
            }
        )
        for tool_use_id, content, is_error in results
    )
