from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from runtime.sandbox import SandboxRuntime


def test_default_sandbox_settings_path_is_outside_run_dir() -> None:
    sandbox = SandboxRuntime.__new__(SandboxRuntime)
    sandbox.run_dir = Path("/repo/.agent/runs/20260705-150446-911bc411")
    sandbox.config = SimpleNamespace(sandbox_settings_path=None)

    settings_path = sandbox._settings_path()

    assert settings_path.name == "20260705-150446-911bc411.json"
    assert settings_path.parent.name == "srt-settings"
    assert not settings_path.is_relative_to(sandbox.run_dir)


def test_custom_sandbox_settings_path_is_respected() -> None:
    sandbox = SandboxRuntime.__new__(SandboxRuntime)
    sandbox.run_dir = Path("/repo/.agent/runs/run-1")
    custom_path = Path.cwd() / "custom-srt-settings.json"
    sandbox.config = SimpleNamespace(sandbox_settings_path=str(custom_path))

    settings_path = sandbox._settings_path()

    assert settings_path == custom_path.resolve()
