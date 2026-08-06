from __future__ import annotations

from io import StringIO

from rich.console import Console

import runtime.observability.console as runtime_console


def test_tool_label_is_rendered_in_yellow(monkeypatch) -> None:
    output = StringIO()
    monkeypatch.setattr(
        runtime_console,
        "CONSOLE",
        Console(file=output, force_terminal=True, color_system="standard", highlight=False),
    )

    runtime_console.print_tool_call("read_file", {"path": "demo.py"})

    rendered = output.getvalue()
    assert "\x1b[1;33m[tool]\x1b[0m" in rendered
    assert "read_file {'path': 'demo.py'}" in rendered


def test_validation_failure_and_model_wait_are_visible(monkeypatch) -> None:
    output = StringIO()
    monkeypatch.setattr(
        runtime_console,
        "CONSOLE",
        Console(file=output, force_terminal=True, color_system="standard", highlight=False),
    )

    runtime_console.print_model_call_start(2, 40)
    runtime_console.print_tool_validation_failure("write_file", "path is required")

    rendered = output.getvalue()
    assert "[model]" in rendered
    assert "call 2/40 waiting for response" in rendered
    assert "\x1b[1;33m[tool]\x1b[0m" in rendered
    assert "write_file rejected" in rendered
