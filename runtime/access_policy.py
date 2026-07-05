from __future__ import annotations

from dataclasses import dataclass


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
        normalized = path.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized

    def is_protected_read(self, path: str) -> bool:
        normalized = self.normalize(path)
        return any(
            normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/")
            for prefix in self.protected_read_prefixes
        )

    def is_protected_write(self, path: str) -> bool:
        normalized = self.normalize(path)
        return any(
            normalized == prefix or normalized.startswith(prefix.rstrip("/") + "/")
            for prefix in self.protected_write_prefixes
        )
