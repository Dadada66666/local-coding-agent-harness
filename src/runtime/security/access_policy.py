from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AccessPolicy:
    sandbox_required_read_prefixes: tuple[str, ...] = (
        ".git/config",
        ".git/hooks",
    )
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
        ".claude/commands",
        ".claude/agents",
        ".claude/skills",
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

    def sandbox_denied_read_paths(self, repo_path: Path) -> list[str]:
        prefixes = tuple(
            prefix
            for prefix in self.protected_read_prefixes
            if prefix not in self.sandbox_required_read_prefixes
        )
        return self._sandbox_paths(repo_path, prefixes)

    def sandbox_denied_write_paths(self, repo_path: Path) -> list[str]:
        return self._sandbox_paths(repo_path, self.protected_write_prefixes)

    def protected_read_references(self, command: str) -> list[str]:
        normalized = command.replace("\\", "/")
        references = self._protected_references(normalized, self.protected_read_prefixes)
        sensitive_git_read = re.search(
            r"(?:^|&&|\|\||[|;\r\n])\s*git\s+"
            r"(?:config(?:\s|$)|remote\s+(?:-v|get-url)(?:\s|$))",
            normalized,
            re.IGNORECASE,
        )
        if sensitive_git_read and ".git/config" not in references:
            references.append(".git/config")
        return references

    def protected_write_references(self, command: str) -> list[str]:
        normalized = command.replace("\\", "/")
        return self._protected_references(normalized, self.protected_write_prefixes)

    def _relative_resolved_path(self, repo_path: Path, target: Path) -> str:
        return target.resolve().relative_to(repo_path.resolve()).as_posix()

    def _sandbox_paths(self, repo_path: Path, prefixes: tuple[str, ...]) -> list[str]:
        paths = []
        for prefix in prefixes:
            target = Path(prefix).expanduser() if prefix.startswith("~/") else repo_path / prefix
            resolved = str(target.resolve())
            if resolved not in paths:
                paths.append(resolved)
        return paths

    def _protected_references(self, command: str, prefixes: tuple[str, ...]) -> list[str]:
        references = []
        for prefix in prefixes:
            pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(prefix)}(?![A-Za-z0-9_.-])"
            if re.search(pattern, command, re.IGNORECASE):
                references.append(prefix)
        return references
