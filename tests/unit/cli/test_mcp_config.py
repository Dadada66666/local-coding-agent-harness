from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from typer.testing import CliRunner

import cli.app as cli_app


def test_build_run_config_resolves_explicit_mcp_path(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"

    config = cli_app.build_run_config(
        "accept_edits",
        False,
        True,
        False,
        None,
        mcp_config=path,
    )

    assert config.mcp_config_path == str(path.resolve())


def test_root_mcp_option_is_passed_to_interactive_start(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    path = tmp_path / "mcp.json"
    monkeypatch.setattr(cli_app, "configure_stdio", lambda: None)
    monkeypatch.setattr(
        cli_app,
        "start_interactive",
        lambda **kwargs: captured.update(kwargs),
    )

    result = CliRunner().invoke(
        cli_app.app,
        ["--permission", "accept_edits", "--mcp-config", str(path)],
    )

    assert result.exit_code == 0
    assert captured["mcp_config"] == path


def test_run_mcp_option_is_passed_to_runner_config(tmp_path: Path, monkeypatch) -> None:
    captured = {}
    path = tmp_path / "mcp.json"
    report_path = tmp_path / "report.md"

    class FakeRunner:
        def run(self, task: str):
            captured["task"] = task
            return SimpleNamespace(run_dir=tmp_path)

    def fake_build_agent_runner(**kwargs):
        captured.update(kwargs)
        return FakeRunner()

    monkeypatch.setattr(cli_app, "configure_stdio", lambda: None)
    monkeypatch.setattr(cli_app, "build_agent_runner", fake_build_agent_runner)

    result = CliRunner().invoke(
        cli_app.app,
        [
            "run",
            "inspect",
            "--permission",
            "accept_edits",
            "--mcp-config",
            str(path),
        ],
    )

    assert result.exit_code == 0
    assert captured["config"].mcp_config_path == str(path.resolve())
    assert captured["task"] == "inspect"
    assert str(report_path) in result.output
