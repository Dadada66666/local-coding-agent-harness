from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AccessPolicy:
    protected_read_prefixes: tuple[str, ...] = (
        ".agent",
        ".env",
        ".mcp.json",
        ".git/config",
        ".git/hooks",
        "~/.ssh",
    )
    protected_write_prefixes: tuple[str, ...] = (
        ".agent",
        ".env",
        ".mcp.json",
        ".git/config",
        ".git/hooks",
        "~/.ssh",
    )

    def normalize(self, path: str) -> str:
        normalized = os.path.normcase(path).replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    def is_protected_read(self, path: str) -> bool:
        normalized = self.normalize(path)
        return any(
            normalized == self.normalize(prefix)
            or normalized.startswith(self.normalize(prefix).rstrip("/") + "/")
            for prefix in self.protected_read_prefixes
        )

    def is_protected_write(self, path: str) -> bool:
        normalized = self.normalize(path)
        return any(
            normalized == self.normalize(prefix)
            or normalized.startswith(self.normalize(prefix).rstrip("/") + "/")
            for prefix in self.protected_write_prefixes
        )

    def is_protected_resolved_read(self, repo_path: Path, target: Path) -> bool:
        try:
            relative = self._relative_resolved_path(repo_path, target)
        except (OSError, ValueError):
            return True
        return self.is_protected_read(relative)

    def is_protected_resolved_write(self, repo_path: Path, target: Path) -> bool:
        try:
            relative = self._relative_resolved_path(repo_path, target)
        except (OSError, ValueError):
            return True
        return self.is_protected_write(relative)

    def _relative_resolved_path(self, repo_path: Path, target: Path) -> str:
        return target.resolve().relative_to(repo_path.resolve()).as_posix()
