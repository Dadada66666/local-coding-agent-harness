from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


PermissionBehavior = Literal["allow", "deny", "ask"]
PermissionRuleSource = Literal["session", "project", "user", "policy"]


@dataclass(frozen=True)
class PermissionRuleValue:
    tool_name: str | None = None
    rule_content: str | None = None
    operation_scope: str | None = None


@dataclass(frozen=True)
class PermissionRule:
    source: PermissionRuleSource
    behavior: PermissionBehavior
    value: PermissionRuleValue


class PermissionRuleStore:
    def __init__(self) -> None:
        self.rules: list[PermissionRule] = []

    def add(self, rule: PermissionRule) -> None:
        if rule not in self.rules:
            self.rules.append(rule)

    def match(
        self,
        behavior: PermissionBehavior,
        tool_name: str,
        operation_scope: str | None,
    ) -> PermissionRule | None:
        for rule in self.rules:
            if rule.behavior != behavior:
                continue
            if (
                rule.value.operation_scope
                and operation_scope
                and rule.value.operation_scope == operation_scope
            ):
                return rule
            if rule.value.tool_name == tool_name and rule.value.rule_content is None:
                return rule
            if rule.value.tool_name == tool_name and rule.value.rule_content and operation_scope:
                if rule.value.rule_content == operation_scope:
                    return rule
        return None
