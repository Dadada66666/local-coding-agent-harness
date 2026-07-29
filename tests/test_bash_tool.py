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
