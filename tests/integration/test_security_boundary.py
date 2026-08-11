from __future__ import annotations

import json
from types import SimpleNamespace

from runtime.config import RunConfig
from agent.loop import AgentLoop
from agent.messages import ToolCall
from runtime.bootstrap import build_runtime
from runtime.security.environment_policy import EnvironmentPolicy
from runtime.security import BashRisk, PermissionMode, RiskClassifier
from runtime.observability.readable_trace_writer import ReadableTraceWriter
from runtime.security.redaction import REDACTED, SecretRedactor
from runtime.security.sandbox import SandboxRuntime
from tools.base import ToolResult


def make_runner(tmp_path) -> AgentLoop:
    return AgentLoop(
        model_client=object(),
        runtime=build_runtime(),
        repo_path=tmp_path,
        permission_mode=PermissionMode.ACCEPT_EDITS,
        config=RunConfig(permission_mode=PermissionMode.ACCEPT_EDITS),
    )


def test_environment_policy_does_not_inherit_host_secrets() -> None:
    source = {
        "PATH": "/usr/bin",
        "LANG": "C.UTF-8",
        "APP_MODE": "test",
        "ANTHROPIC_API_KEY": "provider-secret",
        "TEST_TOKEN": "test-secret",
    }

    env = EnvironmentPolicy(("APP_MODE", "TEST_TOKEN")).build(source)

    assert env["PATH"] == "/usr/bin"
    assert env["APP_MODE"] == "test"
    assert "ANTHROPIC_API_KEY" not in env
    assert "TEST_TOKEN" not in env


def test_secret_redactor_handles_environment_values_and_dotenv_output() -> None:
    redactor = SecretRedactor(("provider-secret-value",))

    content = redactor.redact(
        "token=provider-secret-value\n"
        "LCAH_SECRET_SHOULD_NOT_LEAK=probe-secret\n"
    )

    assert "provider-secret-value" not in content
    assert "probe-secret" not in content
    assert content.count(REDACTED) == 2


def test_compound_command_requires_every_segment_to_be_known() -> None:
    classifier = RiskClassifier()

    safe_read = classifier.classify_bash("pwd && rg -n needle .")
    mixed_read = classifier.classify_bash(
        "ls -la .env && printf '%s\\n' marker && sed -n '1,20p' .env"
    )
    dynamic_read = classifier.classify_bash("echo $(cat .env)")
    existence_check = classifier.classify_bash(
        "test ! -e probe.txt && printf '%s\\n' absent"
    )

    assert safe_read.risk == BashRisk.READ_ONLY_COMMAND
    assert mixed_read.risk == BashRisk.UNKNOWN
    assert dynamic_read.risk == BashRisk.UNKNOWN
    assert existence_check.risk == BashRisk.SAFE_CHECK


def test_bash_protected_read_is_denied_before_execution(tmp_path) -> None:
    runner = make_runner(tmp_path)
    context = runner.create_context("inspect configuration", include_initial_message=True)
    bash = runner.runtime.tool_registry.get("bash")

    direct = context.permission_gate.check(
        bash,
        {"command": "sed -n '1,20p' .env"},
        context,
    )
    compound = context.permission_gate.check(
        bash,
        {"command": "ls -la .env && sed -n '1,20p' .env"},
        context,
    )
    example = context.permission_gate.check(
        bash,
        {"command": "cat .env.example"},
        context,
    )
    git_status = context.permission_gate.check(
        bash,
        {"command": "git status --short"},
        context,
    )
    git_config = context.permission_gate.check(
        bash,
        {"command": "git config --list"},
        context,
    )

    assert direct.behavior == "deny"
    assert direct.risk == "protected_read"
    assert direct.terminal_on_deny is False
    assert compound.behavior == "deny"
    assert example.behavior == "allow"
    assert git_status.behavior == "allow"
    assert git_config.behavior == "deny"


def test_bash_protected_mutation_is_denied_even_without_extracted_paths(tmp_path) -> None:
    runner = make_runner(tmp_path)
    context = runner.create_context("update configuration", include_initial_message=True)
    bash = runner.runtime.tool_registry.get("bash")

    decision = context.permission_gate.check(
        bash,
        {"command": "Set-Content .env 'SECRET=changed'"},
        context,
    )

    assert decision.behavior == "deny"
    assert decision.risk == "protected_write"
    assert decision.terminal_on_deny is True


def test_tool_result_is_redacted_before_messages_and_artifacts(
    monkeypatch,
    tmp_path,
) -> None:
    runner = make_runner(tmp_path)
    context = runner.create_context("inspect configuration", include_initial_message=True)
    bash = runner.runtime.tool_registry.get("bash")
    monkeypatch.setattr(
        bash,
        "call",
        lambda args, current_context: ToolResult(
            ok=True,
            content="LCAH_SECRET_SHOULD_NOT_LEAK=probe-secret",
        ),
    )

    result = runner.runtime.executor.execute(
        ToolCall("read-secret", "bash", {"command": "printf safe"}),
        context,
    )
    context.add_tool_result("read-secret", result.content)
    context.final_text = "LCAH_SECRET_SHOULD_NOT_LEAK=probe-secret"
    context.conversation_messages.append(
        {
            "role": "assistant",
            "content": [
                {
                    "type": "text",
                    "text": "LCAH_SECRET_SHOULD_NOT_LEAK=probe-secret",
                }
            ],
        }
    )

    report_path = context.report_writer.write(context)
    readable_path = context.run_dir / "readable_trace.md"

    ReadableTraceWriter().write(context)
    trace = context.trace.path.read_text(encoding="utf-8")
    report = report_path.read_text(encoding="utf-8")
    readable = readable_path.read_text(encoding="utf-8")

    assert result.content == f"LCAH_SECRET_SHOULD_NOT_LEAK={REDACTED}"
    assert result.metadata["secret_redacted"] is True
    assert "probe-secret" not in trace
    assert "probe-secret" not in report
    assert "probe-secret" not in readable

    events = [json.loads(line) for line in trace.splitlines()]
    tool_result = next(event for event in events if event.get("type") == "tool_result")
    assert tool_result["output_preview"].endswith(REDACTED)


def test_large_tool_output_is_persisted_once_and_recoverable(monkeypatch, tmp_path) -> None:
    runner = make_runner(tmp_path)
    context = runner.create_context("inspect generated output", include_initial_message=True)
    bash = runner.runtime.tool_registry.get("bash")
    minimum_chars = context.config.max_tool_result_chars + 1
    full_output = ("0123456789" * ((minimum_chars // 10) + 1))[:minimum_chars]
    monkeypatch.setattr(
        bash,
        "call",
        lambda args, current_context: ToolResult(ok=True, content=full_output),
    )

    result = runner.runtime.executor.execute(
        ToolCall("large-output", "bash", {"command": "printf safe"}),
        context,
    )

    assert result.artifact_id is not None
    assert context.tool_result_artifacts["large-output"] == result.artifact_id
    assert str(context.run_dir) not in result.content
    reference = context.artifacts.get(result.artifact_id)
    assert reference is not None
    assert reference.path.read_text(encoding="utf-8") == full_output

    artifact_result = runner.runtime.executor.execute(
        ToolCall(
            "read-slice",
            "read_artifact",
            {"artifact_id": result.artifact_id, "offset": 10, "limit": 20},
        ),
        context,
    )
    assert artifact_result.ok is True
    assert artifact_result.content.startswith("01234567890123456789")
    assert context.tool_result_artifacts["read-slice"] == result.artifact_id


def test_large_output_persistence_failure_returns_bounded_preview(monkeypatch, tmp_path) -> None:
    runner = make_runner(tmp_path)
    context = runner.create_context("inspect generated output", include_initial_message=True)
    context.config.max_tool_result_chars = 512
    bash = runner.runtime.tool_registry.get("bash")
    monkeypatch.setattr(
        bash,
        "call",
        lambda args, current_context: ToolResult(ok=True, content="x" * 12000),
    )
    monkeypatch.setattr(
        context.artifacts,
        "persist",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk unavailable")),
    )

    result = runner.runtime.executor.execute(
        ToolCall("large-output", "bash", {"command": "printf safe"}),
        context,
    )

    assert result.ok is True
    assert result.artifact_id is None
    assert result.metadata["artifact_persist_failed"] is True
    assert len(result.content) <= context.config.max_tool_result_chars
    assert "artifact_persist_error" in context.trace.path.read_text(encoding="utf-8")


def test_unknown_auto_allow_requires_attested_protected_reads() -> None:
    sandbox = SimpleNamespace(
        status=SimpleNamespace(
            enabled=True,
            available=True,
            strong_boundary=True,
            protected_reads_enforced=False,
        ),
        config=SimpleNamespace(sandbox_auto_allow_bash=True),
    )

    assert SandboxRuntime.can_auto_allow_unknown_bash(sandbox) is False
