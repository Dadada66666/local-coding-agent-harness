from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

from runtime.environment_policy import SENSITIVE_ENV_NAME_RE


REDACTED = "[REDACTED]"
MIN_SECRET_VALUE_CHARS = 8
DOTENV_SECRET_RE = re.compile(
    r"(?im)^(\s*(?:export\s+)?[A-Z_][A-Z0-9_]*"
    r"(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE_KEY|AUTH)"
    r"[A-Z0-9_]*\s*=\s*)([^\r\n]*)$"
)


class SecretRedactor:
    def __init__(self, secret_values: tuple[str, ...] = ()) -> None:
        self.secret_values = tuple(
            sorted(
                {
                    value
                    for value in secret_values
                    if len(value) >= MIN_SECRET_VALUE_CHARS
                },
                key=len,
                reverse=True,
            )
        )

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> SecretRedactor:
        values = environment if environment is not None else os.environ
        secrets = tuple(
            value
            for name, value in values.items()
            if SENSITIVE_ENV_NAME_RE.search(name)
        )
        return cls(secrets)

    def redact(self, text: str) -> str:
        redacted, _ = self.redact_with_count(text)
        return redacted

    def redact_with_count(self, text: str) -> tuple[str, int]:
        redacted = str(text)
        replacements = 0

        for secret in self.secret_values:
            count = redacted.count(secret)
            if count:
                redacted = redacted.replace(secret, REDACTED)
                replacements += count

        def replace_assignment(match: re.Match) -> str:
            nonlocal replacements
            value = match.group(2).strip()
            if not value or value == REDACTED:
                return match.group(0)
            replacements += 1
            return f"{match.group(1)}{REDACTED}"

        redacted = DOTENV_SECRET_RE.sub(replace_assignment, redacted)
        return redacted, replacements

    def redact_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_value(item) for item in value)
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if SENSITIVE_ENV_NAME_RE.search(str(key)) and isinstance(item, str):
                    redacted[key] = REDACTED
                else:
                    redacted[key] = self.redact_value(item)
            return redacted
        return value
