from importlib import import_module
from importlib.metadata import distribution

import pytest
from typer.testing import CliRunner

from cli.app import app


@pytest.mark.parametrize("package_name", ["agent", "runtime", "tools", "cli"])
def test_top_level_package_is_importable(package_name: str) -> None:
    assert import_module(package_name) is not None


def test_console_scripts_target_cli_app() -> None:
    entry_points = {
        entry_point.name: entry_point.value
        for entry_point in distribution("local-coding-agent-harness").entry_points
        if entry_point.group == "console_scripts"
    }

    assert entry_points["agent"] == "cli.app:app"
    assert entry_points["lcah"] == "cli.app:app"


def test_cli_app_exposes_existing_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "run" in result.stdout
    assert "report" in result.stdout
    assert "replay" in result.stdout
