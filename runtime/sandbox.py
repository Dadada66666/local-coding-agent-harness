from __future__ import annotations

import json
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SandboxStatus:
    enabled: bool
    available: bool
    strong_boundary: bool
    reason: str | None = None
    settings_path: Path | None = None


class SandboxRuntime:
    def __init__(self, repo_path: Path, run_dir: Path, config) -> None:
        self.repo_path = repo_path
        self.run_dir = run_dir
        self.config = config
        self.status = self._detect()

    def _detect(self) -> SandboxStatus:
        if not getattr(self.config, "sandbox_enabled", False):
            return SandboxStatus(
                enabled=False,
                available=False,
                strong_boundary=False,
                reason="sandbox disabled",
            )

        if not shutil.which("srt"):
            return SandboxStatus(
                enabled=True,
                available=False,
                strong_boundary=False,
                reason="srt not found",
            )

        system = platform.system()
        strong_boundary = system in {"Linux", "Darwin"}
        settings_path = self._settings_path()

        try:
            self._write_settings(settings_path, system)
        except OSError as exc:
            return SandboxStatus(
                enabled=True,
                available=False,
                strong_boundary=False,
                reason=f"failed to write sandbox settings: {exc}",
                settings_path=settings_path,
            )

        return SandboxStatus(
            enabled=True,
            available=True,
            strong_boundary=strong_boundary,
            reason=None if strong_boundary else "sandbox available but not a strong boundary on this platform",
            settings_path=settings_path,
        )

    def _settings_path(self) -> Path:
        custom = getattr(self.config, "sandbox_settings_path", None)
        if custom:
            return Path(custom).expanduser().resolve()
        return self.run_dir / "srt-settings.json"

    def _write_settings(self, path: Path, system: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)

        if system == "Windows":
            data = {
                "network": {"allowedDomains": [], "deniedDomains": []},
                "filesystem": {"denyRead": [], "denyWrite": []},
            }
        else:
            data = {
                "network": {"allowedDomains": [], "deniedDomains": []},
                "filesystem": {
                    "denyRead": ["~/.ssh"],
                    "allowRead": [],
                    "allowWrite": [str(self.repo_path), "/tmp"],
                    "denyWrite": [".env", ".git/hooks", ".git/config", ".claude", ".mcp.json"],
                },
            }

        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def should_wrap_command(self, command: str) -> bool:
        return self.status.enabled and self.status.available

    def can_auto_allow_unknown_bash(self) -> bool:
        return (
            self.status.enabled
            and self.status.available
            and self.status.strong_boundary
            and bool(getattr(self.config, "sandbox_auto_allow_bash", True))
        )

    def wrap_argv(self, argv: list[str]) -> list[str]:
        if not self.status.available or not self.status.settings_path:
            return argv
        return ["srt", "--settings", str(self.status.settings_path), *argv]

    def metadata(self) -> dict:
        return {
            "enabled": self.status.enabled,
            "available": self.status.available,
            "strong_boundary": self.status.strong_boundary,
            "reason": self.status.reason,
            "settings_path": str(self.status.settings_path) if self.status.settings_path else None,
        }

    def prompt_status(self) -> str:
        if not self.status.enabled:
            return "disabled"
        if not self.status.available:
            return f"enabled but unavailable ({self.status.reason})"
        if not self.status.strong_boundary:
            return f"enabled, available, weak boundary ({self.status.reason})"
        return "enabled, available, strong boundary"