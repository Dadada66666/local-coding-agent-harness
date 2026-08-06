from __future__ import annotations

import json
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from runtime.security.access_policy import AccessPolicy


PROBE_TIMEOUT_SECONDS = 10
MAX_REASON_CHARS = 600
WINDOWS_NO_SETTINGS_REASON = "Windows SRT runs without --settings to avoid srt-win ACL stamp instability"
READ_PROBE_MARKER = "local-coding-agent-harness-protected-read-probe"


@dataclass(frozen=True)
class SandboxStatus:
    enabled: bool
    available: bool
    strong_boundary: bool
    reason: str | None = None
    settings_path: Path | None = None
    executable_path: str | None = None
    settings_applied: bool = False
    protected_reads_enforced: bool = False


class SandboxRuntime:
    def __init__(
        self,
        repo_path: Path,
        run_dir: Path,
        config,
        access_policy: AccessPolicy | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.run_dir = run_dir
        self.config = config
        self.access_policy = access_policy or AccessPolicy()
        self.status = self._detect()

    def _detect(self) -> SandboxStatus:
        if not getattr(self.config, "sandbox_enabled", False):
            return SandboxStatus(
                enabled=False,
                available=False,
                strong_boundary=False,
                reason="sandbox disabled",
            )

        executable = self._find_executable()
        if executable is None:
            return SandboxStatus(
                enabled=True,
                available=False,
                strong_boundary=False,
                reason="srt not found",
            )

        system = platform.system()
        settings_applied = self._should_apply_settings(system)
        settings_path = self._settings_path() if settings_applied else None

        if settings_applied:
            try:
                self._write_settings(settings_path)
            except OSError as exc:
                return SandboxStatus(
                    enabled=True,
                    available=False,
                    strong_boundary=False,
                    reason=f"failed to write sandbox settings: {exc}",
                    settings_path=settings_path,
                    executable_path=executable,
                )

        probe_error = self._probe(executable, settings_path, settings_applied)
        if probe_error is not None:
            return SandboxStatus(
                enabled=True,
                available=False,
                strong_boundary=False,
                reason=probe_error,
                settings_path=settings_path,
                executable_path=executable,
            )

        read_probe_error = None
        if settings_applied:
            read_probe_error = self._probe_protected_read(
                executable,
                settings_path,
                settings_applied,
            )
        protected_reads_enforced = settings_applied and read_probe_error is None
        strong_boundary = settings_applied and protected_reads_enforced

        reason = None
        if system == "Windows":
            reason = WINDOWS_NO_SETTINGS_REASON
        elif read_probe_error is not None:
            reason = read_probe_error
        elif not strong_boundary:
            reason = "sandbox available but not a strong boundary on this platform"

        return SandboxStatus(
            enabled=True,
            available=True,
            strong_boundary=strong_boundary,
            reason=reason,
            settings_path=settings_path,
            executable_path=executable,
            settings_applied=settings_applied,
            protected_reads_enforced=protected_reads_enforced,
        )

    def _find_executable(self) -> str | None:
        if platform.system() == "Windows":
            return shutil.which("srt.cmd") or shutil.which("srt.exe") or shutil.which("srt")
        return shutil.which("srt")

    def _should_apply_settings(self, system: str) -> bool:
        return system in {"Linux", "Darwin"}

    def _settings_path(self) -> Path:
        custom = getattr(self.config, "sandbox_settings_path", None)
        if custom:
            return Path(custom).expanduser().resolve()
        return (
            Path(tempfile.gettempdir())
            / "local-coding-agent-harness"
            / "srt-settings"
            / f"{self.run_dir.name}.json"
        )

    def _write_settings(self, path: Path | None) -> None:
        if path is None:
            return

        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass

        network = {
            "allowedDomains": [],
            "deniedDomains": [],
            "allowLocalBinding": False,
        }

        data = {
            "network": network,
            "filesystem": {
                "denyRead": self.access_policy.sandbox_denied_read_paths(self.repo_path),
                "allowRead": [],
                "allowWrite": [str(self.repo_path), "/tmp"],
                "denyWrite": self.access_policy.sandbox_denied_write_paths(self.repo_path),
            },
        }

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _probe(self, executable: str, settings_path: Path | None, settings_applied: bool) -> str | None:
        argv = self._srt_argv(executable, settings_path, settings_applied, ["echo", "sandbox-probe"])

        try:
            completed = subprocess.run(
                argv,
                cwd=self.repo_path,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            return "srt not found"
        except subprocess.TimeoutExpired:
            return f"srt probe timed out after {PROBE_TIMEOUT_SECONDS}s"
        except OSError as exc:
            return f"srt probe failed to start: {exc}"

        if completed.returncode == 0:
            return None

        output = f"{completed.stdout or ''}{completed.stderr or ''}".strip()
        return f"srt probe failed: {self._preview(output) or f'exit code {completed.returncode}'}"

    def _probe_protected_read(
        self,
        executable: str,
        settings_path: Path | None,
        settings_applied: bool,
    ) -> str | None:
        probe_path = self.run_dir / ".sandbox-read-probe"
        try:
            probe_path.write_text(READ_PROBE_MARKER, encoding="utf-8")
            argv = self._srt_argv(
                executable,
                settings_path,
                settings_applied,
                ["/bin/cat", str(probe_path)],
            )
            completed = subprocess.run(
                argv,
                cwd=self.repo_path,
                shell=False,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                check=False,
                stdin=subprocess.DEVNULL,
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return f"protected read probe failed: {self._preview(str(exc))}"
        finally:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass

        output = f"{completed.stdout or ''}{completed.stderr or ''}"
        if completed.returncode != 0 and READ_PROBE_MARKER not in output:
            return None
        return "sandbox protected read policy was not enforced"

    def _srt_argv(
        self,
        executable: str,
        settings_path: Path | None,
        settings_applied: bool,
        command_argv: list[str],
    ) -> list[str]:
        if settings_applied:
            if settings_path is None:
                raise ValueError("settings_path is required when settings_applied=true")
            return [executable, "--settings", str(settings_path), *command_argv]
        return [executable, *command_argv]

    def _preview(self, text: str) -> str:
        if len(text) <= MAX_REASON_CHARS:
            return text
        omitted = len(text) - MAX_REASON_CHARS
        return f"{text[:MAX_REASON_CHARS]}... {omitted} chars omitted"

    def should_wrap_command(self, command: str) -> bool:
        return self.status.enabled and self.status.available

    def can_auto_allow_unknown_bash(self) -> bool:
        return (
            self.status.enabled
            and self.status.available
            and self.status.strong_boundary
            and self.status.protected_reads_enforced
            and bool(getattr(self.config, "sandbox_auto_allow_bash", True))
        )

    def wrap_argv(self, argv: list[str]) -> list[str]:
        if not self.status.available or not self.status.executable_path:
            return argv
        return self._srt_argv(
            self.status.executable_path,
            self.status.settings_path,
            self.status.settings_applied,
            argv,
        )

    def metadata(self) -> dict:
        return {
            "enabled": self.status.enabled,
            "available": self.status.available,
            "strong_boundary": self.status.strong_boundary,
            "reason": self.status.reason,
            "settings_path": str(self.status.settings_path) if self.status.settings_path else None,
            "executable_path": self.status.executable_path,
            "settings_applied": self.status.settings_applied,
            "protected_reads_enforced": self.status.protected_reads_enforced,
        }

    def prompt_status(self) -> str:
        if not self.status.enabled:
            return "disabled"
        if not self.status.available:
            return f"enabled but unavailable ({self.status.reason})"
        if not self.status.strong_boundary:
            return f"enabled, available, weak boundary ({self.status.reason})"
        return "enabled, available, strong boundary"
