from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from runtime.operation import Operation


class PermissionMode:
    READ_ONLY = "read_only"
    ACCEPT_EDITS = "accept_edits"
    MANUAL_APPROVAL = "manual_approval"


class PermissionBehavior:
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class BashRisk:
    SAFE_CHECK = "safe_check"
    READ_ONLY_COMMAND = "read_only_command"
    FILE_WRITE_VIA_BASH = "file_write_via_bash"
    FILE_DELETE_VIA_BASH = "file_delete_via_bash"
    DESTRUCTIVE = "destructive"
    NETWORK = "network"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BashRiskDecision:
    risk: str
    reason: str
    target_paths: list[str]
    command_prefix: str | None
    suggested_tool: str | None
    confidence: str
    execution_route: str | None = None
    effects: tuple[str, ...] = ()
    network_hosts: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        metadata = {
            "risk": self.risk,
            "reason": self.reason,
            "target_paths": self.target_paths,
            "command_prefix": self.command_prefix,
            "suggested_tool": self.suggested_tool,
            "confidence": self.confidence,
        }
        if self.execution_route is not None:
            metadata["execution_route"] = self.execution_route
        if self.effects:
            metadata["effects"] = list(self.effects)
        if self.network_hosts:
            metadata["network_hosts"] = list(self.network_hosts)
        return metadata


@dataclass
class PermissionDecision:
    behavior: str
    risk: str
    message: str
    proposed_scope: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    operation: Operation | None = None
    terminal_on_deny: bool = False
    decision_reason: str | None = None

