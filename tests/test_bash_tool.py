from __future__ import annotations

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


def test_normal_commands_keep_existing_posix_shell_behavior(monkeypatch) -> None:
    tool = BashTool()
    monkeypatch.setattr("tools.bash.platform.system", lambda: "Linux")

    argv = tool._build_command_argv("echo ok")

    assert argv == ["/bin/sh", "-lc", "echo ok"]
