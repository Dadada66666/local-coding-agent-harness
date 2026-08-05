from __future__ import annotations

import pytest

from agent.context import RunConfig
from agent.loop import AgentLoop
from runtime.bootstrap import build_runtime
from tools.base import ToolValidationError
from tools.read_artifact import ReadArtifactTool


def make_context(tmp_path):
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(permission_mode="accept_edits", artifact_read_max_chars=20),
    )
    return runner.create_context("inspect output", include_initial_message=True)


def test_read_artifact_uses_opaque_current_run_id(tmp_path) -> None:
    context = make_context(tmp_path)
    reference = context.artifacts.persist("tool-1", "abcdefghijklmnopqrstuvwxyz")
    tool = ReadArtifactTool()

    result = tool.call(
        {"artifact_id": reference.artifact_id, "offset": 5, "limit": 10},
        context,
    )

    assert result.ok is True
    assert result.content.startswith("fghijklmno")
    assert "offset=15" in result.content
    assert result.artifact_id == reference.artifact_id
    assert str(context.run_dir) not in result.content


def test_artifact_id_does_not_embed_provider_tool_call_id(tmp_path) -> None:
    context = make_context(tmp_path)

    reference = context.artifacts.persist("sensitive/" + ("very-long-" * 100), "output")

    assert reference.artifact_id.startswith("artifact_")
    assert "sensitive" not in reference.artifact_id
    assert len(reference.artifact_id) == len("artifact_") + 16
    assert reference.tool_call_id.startswith("sensitive/")


def test_artifact_read_clamps_offsets_past_end(tmp_path) -> None:
    context = make_context(tmp_path)
    reference = context.artifacts.persist("tool-1", "short")

    result = context.artifacts.read(reference.artifact_id, offset=100, limit=10)

    assert result.content == ""
    assert result.offset == 5
    assert result.next_offset == 5
    assert result.total_chars == 5


def test_read_artifact_rejects_unknown_ids_and_oversized_reads(tmp_path) -> None:
    context = make_context(tmp_path)
    tool = ReadArtifactTool()

    tool.validate({"artifact_id": "artifact_missing", "limit": 20}, context)
    result = tool.call({"artifact_id": "artifact_missing"}, context)

    assert result.ok is False
    assert result.error == "unknown artifact"
    with pytest.raises(ToolValidationError, match="between 1 and 20"):
        tool.validate({"artifact_id": "artifact_missing", "limit": 21}, context)

    with pytest.raises(ToolValidationError, match="offset must be an integer"):
        tool.validate({"artifact_id": "artifact_missing", "offset": "bad"}, context)


def test_read_artifact_default_respects_tool_result_budget(tmp_path) -> None:
    runner = AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode="accept_edits",
        config=RunConfig(
            permission_mode="accept_edits",
            max_tool_result_chars=512,
            artifact_read_max_chars=1000,
        ),
    )
    context = runner.create_context("inspect output", include_initial_message=True)
    reference = context.artifacts.persist("tool-1", "x" * 1000)
    tool = ReadArtifactTool()

    tool.validate({"artifact_id": reference.artifact_id}, context)
    result = tool.call({"artifact_id": reference.artifact_id}, context)

    assert result.metadata["next_offset"] == 256
