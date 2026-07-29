from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.access_policy import AccessPolicy
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


def test_generated_settings_share_access_policy_paths(tmp_path: Path) -> None:
    sandbox = SandboxRuntime.__new__(SandboxRuntime)
    sandbox.repo_path = tmp_path
    sandbox.access_policy = AccessPolicy()
    settings_path = tmp_path / "settings.json"

    sandbox._write_settings(settings_path)

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    filesystem = settings["filesystem"]
    assert str((tmp_path / ".env").resolve()) in filesystem["denyRead"]
    assert str((tmp_path / ".agent").resolve()) in filesystem["denyRead"]
    assert str((tmp_path / ".git" / "config").resolve()) not in filesystem["denyRead"]
    assert str((tmp_path / ".env").resolve()) in filesystem["denyWrite"]
    assert str((tmp_path / ".agent").resolve()) in filesystem["denyWrite"]


def test_protected_read_probe_requires_an_actual_denial(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sandbox = SandboxRuntime.__new__(SandboxRuntime)
    sandbox.repo_path = tmp_path
    sandbox.run_dir = tmp_path / ".agent" / "runs" / "run-1"
    sandbox.run_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "runtime.sandbox.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="local-coding-agent-harness-protected-read-probe",
            stderr="",
        ),
    )

    error = sandbox._probe_protected_read(
        "srt",
        tmp_path / "settings.json",
        True,
    )

    assert error == "sandbox protected read policy was not enforced"
    assert not (sandbox.run_dir / ".sandbox-read-probe").exists()


@pytest.mark.skipif(
    platform.system() != "Linux" or shutil.which("srt") is None,
    reason="requires Linux and srt",
)
def test_linux_srt_blocks_protected_read_canaries(tmp_path: Path) -> None:
    secret = "LCAH_SECRET_SHOULD_NOT_LEAK=linux-srt-canary"
    (tmp_path / ".env").write_text(secret, encoding="utf-8")
    run_dir = tmp_path / ".agent" / "runs" / "integration"
    run_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    config = SimpleNamespace(
        sandbox_enabled=True,
        sandbox_settings_path=str(tmp_path / "srt-settings.json"),
        sandbox_auto_allow_bash=True,
    )

    sandbox = SandboxRuntime(tmp_path, run_dir, config, AccessPolicy())
    completed = subprocess.run(
        sandbox.wrap_argv(["/bin/cat", str(tmp_path / ".env")]),
        cwd=tmp_path,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    git_status = subprocess.run(
        sandbox.wrap_argv(["git", "status", "--short"]),
        cwd=tmp_path,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )

    output = f"{completed.stdout or ''}{completed.stderr or ''}"
    assert sandbox.status.strong_boundary is True
    assert sandbox.status.protected_reads_enforced is True
    assert completed.returncode != 0
    assert "linux-srt-canary" not in output
    assert git_status.returncode == 0
