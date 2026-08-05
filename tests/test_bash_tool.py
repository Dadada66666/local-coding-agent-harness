from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.base import ToolValidationError
from tools.bash import BashTool


def test_timeout_is_bounded_and_documented_in_seconds() -> None:
    tool = BashTool()

    tool.validate({"command": "echo ok", "timeout": 600}, context=None)
    with pytest.raises(ToolValidationError, match="between 1 and 600 seconds"):
        tool.validate({"command": "echo ok", "timeout": 120000}, context=None)

    assert "seconds" in tool.input_schema["properties"]["timeout"]["description"]


def test_verify_commands_use_fail_fast_posix_shell(monkeypatch) -> None:
    tool = BashTool()
    monkeypatch.setattr("tools.bash.platform.system", lambda: "Linux")

    argv = tool._build_command_argv("false\ntrue", fail_fast=True)

    assert argv == ["/bin/sh", "-lec", "false\ntrue"]
    assert tool._shell_name(fail_fast=True) == "/bin/sh -lec"
    assert 'purpose="verify"' in tool.description
    assert "fail-fast" in tool.description


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

    result = BashTool().call({"command": "echo output"}, context)

    assert result.ok is True
    assert result.content == output
    assert result.metadata["original_chars"] == len(output)
    assert result.metadata["truncated"] is False
