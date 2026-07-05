from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Operation:
    kind: str
    action: str
    subject: str
    paths: list[str] = field(default_factory=list)
    command: str | None = None
    scope_key: str | None = None
    terminal_on_deny: bool = False
    is_read_only: bool = False
    is_destructive: bool = False
    is_sensitive: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "action": self.action,
            "subject": self.subject,
            "paths": self.paths,
            "command": self.command,
            "scope_key": self.scope_key,
            "terminal_on_deny": self.terminal_on_deny,
            "is_read_only": self.is_read_only,
            "is_destructive": self.is_destructive,
            "is_sensitive": self.is_sensitive,
            "metadata": self.metadata,
        }
