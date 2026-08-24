from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from tools.base import ToolValidationError
from tools.bash import BashTool


def test_timeout_is_bounded_and_documented_in_seconds() -> None:
    tool = BashTool()

    tool.validate(
        {"command": "echo ok", "timeout": 600, "result_scope": "command"},
        context=None,
    )
    with pytest.raises(ToolValidationError, match="between 1 and 600 seconds"):
        tool.validate(
            {"command": "echo ok", "timeout": 120000, "result_scope": "command"},
            context=None,
        )
    with pytest.raises(ToolValidationError, match="exit_expectation"):
        tool.validate(
            {
                "command": "echo ok",
                "exit_expectation": "sometimes",
                "result_scope": "command",
            },
            context=None,
        )

    assert "seconds" in tool.input_schema["properties"]["timeout"]["description"]
    assert tool.input_schema["properties"]["exit_expectation"]["enum"] == [
        "zero",
        "nonzero",
    ]


def test_result_scope_is_required_and_validated_exactly() -> None:
    tool = BashTool()

    assert tool.input_schema["required"] == ["command", "result_scope"]
    assert tool.input_schema["properties"]["result_scope"]["enum"] == [
        "command",
        "launcher",
    ]
    assert (
        "Launcher results never become authoritative verification"
        in tool.input_schema["properties"]["result_scope"]["description"]
    )
    assert "launcher success is not verification success" in tool.description

    for args in (
        {"command": "echo ok"},
        {"command": "echo ok", "result_scope": 1},
        {"command": "echo ok", "result_scope": "COMMAND"},
        {"command": "echo ok", "result_scope": "unknown"},
    ):
        with pytest.raises(ToolValidationError, match="result_scope"):
            tool.validate(args, context=None)


def test_purpose_contract_accepts_run_probe_verify_and_rejects_other_values() -> None:
    tool = BashTool()

    assert tool.input_schema["properties"]["purpose"]["enum"] == [
        "run",
        "probe",
        "verify",
    ]
    for purpose in ("run", "probe", "verify"):
        tool.validate(
            {"command": "echo ok", "purpose": purpose, "result_scope": "command"},
            context=None,
        )
    for purpose in ("check", "VERIFY", 1):
        with pytest.raises(ToolValidationError, match="purpose"):
            tool.validate(
                {
                    "command": "echo ok",
                    "purpose": purpose,
                    "result_scope": "command",
                },
                context=None,
            )


def test_verify_commands_use_fail_fast_posix_shell(monkeypatch) -> None:
    tool = BashTool()
    monkeypatch.setattr("tools.bash.platform.system", lambda: "Linux")

    argv = tool._build_command_argv("false\ntrue", fail_fast=True)

    assert argv == ["/bin/sh", "-lec", "false\ntrue"]
    assert tool._shell_name(fail_fast=True) == "/bin/sh -lec"
    assert "purpose=verify" in tool.description
    assert "fail-fast" in tool.description
    assert "edit_file/write_file/delete_file, not Bash" in tool.description
    assert "apply_patch" in tool.description
    assert "must not mutate files" in tool.description
    assert (
        "authoritative final task verification"
        in tool.input_schema["properties"]["purpose"]["description"]
    )


def test_probe_does_not_enable_verify_fail_fast(monkeypatch, tmp_path) -> None:
    captured: dict = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return SimpleNamespace(stdout="ready", stderr=None, returncode=0)

    monkeypatch.setattr("tools.bash.platform.system", lambda: "Linux")
    monkeypatch.setattr("tools.bash.subprocess.run", fake_run)
    context = SimpleNamespace(repo_path=tmp_path, sandbox=None)

    result = BashTool().call(
        {
            "command": "curl -fsSI http://127.0.0.1:4173/",
            "purpose": "probe",
            "result_scope": "command",
        },
        context,
    )

    assert captured["argv"] == [
        "/bin/sh",
        "-lc",
        "curl -fsSI http://127.0.0.1:4173/",
    ]
    assert result.metadata["purpose"] == "probe"
    assert result.metadata["fail_fast"] is False


def test_normal_commands_keep_existing_posix_shell_behavior(monkeypatch) -> None:
    tool = BashTool()
    monkeypatch.setattr("tools.bash.platform.system", lambda: "Linux")

    argv = tool._build_command_argv("echo ok")

    assert argv == ["/bin/sh", "-lc", "echo ok"]


def test_bash_keeps_full_output_for_post_processing(monkeypatch, tmp_path) -> None:
    output = "x" * 20000
    monkeypatch.setattr(
        "tools.bash.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout=output,
            stderr=None,
            returncode=0,
        ),
    )
    context = SimpleNamespace(repo_path=tmp_path, sandbox=None)

    result = BashTool().call({"command": "echo output", "result_scope": "command"}, context)

    assert result.ok is True
    assert result.content == output
    assert result.metadata["original_chars"] == len(output)
    assert result.metadata["truncated"] is False
    assert result.metadata["result_scope"] == "command"


@pytest.mark.parametrize(
    ("returncode", "expectation", "expected_ok", "expected_error"),
    [
        (7, "nonzero", True, None),
        (0, "nonzero", False, "command exited 0; expected nonzero"),
        (7, "zero", False, "command exited 7"),
    ],
)
def test_bash_matches_the_declared_exit_expectation(
    monkeypatch,
    tmp_path,
    returncode: int,
    expectation: str,
    expected_ok: bool,
    expected_error: str | None,
) -> None:
    monkeypatch.setattr(
        "tools.bash.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            stdout="command output",
            stderr=None,
            returncode=returncode,
        ),
    )
    context = SimpleNamespace(repo_path=tmp_path, sandbox=None)

    result = BashTool().call(
        {
            "command": "check",
            "exit_expectation": expectation,
            "result_scope": "command",
        },
        context,
    )

    assert result.ok is expected_ok
    assert result.error == expected_error
    assert result.metadata["returncode"] == returncode
    assert result.metadata["exit_expectation"] == expectation
    assert result.metadata["result_scope"] == "command"


def test_launcher_scope_is_preserved_on_timeout(monkeypatch, tmp_path) -> None:
    def timeout_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            cmd=["/bin/sh"],
            timeout=10,
            output="partial output",
        )

    monkeypatch.setattr("tools.bash.subprocess.run", timeout_run)
    context = SimpleNamespace(repo_path=tmp_path, sandbox=None)

    result = BashTool().call(
        {"command": "start service", "result_scope": "launcher", "timeout": 10},
        context,
    )

    assert result.ok is False
    assert result.error == "timeout after 10s"
    assert result.metadata["timed_out"] is True
    assert result.metadata["result_scope"] == "launcher"
