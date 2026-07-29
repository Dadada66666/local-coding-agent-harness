from __future__ import annotations

import os
import re
from collections.abc import Mapping


SENSITIVE_ENV_NAME_RE = re.compile(
    r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|PRIVATE[_-]?KEY|AUTH)",
    re.IGNORECASE,
)
NEVER_INHERIT_ENV_NAMES = {
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
}
DEFAULT_ENV_NAMES = {
    "APPDATA",
    "COLORTERM",
    "COMSPEC",
    "CONDA_PREFIX",
    "FORCE_COLOR",
    "HOME",
    "LANG",
    "LOCALAPPDATA",
    "LOGNAME",
    "NO_COLOR",
    "PATH",
    "PATHEXT",
    "SHELL",
    "SYSTEMROOT",
    "TEMP",
    "TERM",
    "TMP",
    "TMPDIR",
    "USER",
    "USERNAME",
    "USERPROFILE",
    "VIRTUAL_ENV",
    "WINDIR",
    "XDG_RUNTIME_DIR",
}


class EnvironmentPolicy:
    def __init__(self, extra_names: tuple[str, ...] = ()) -> None:
        self.extra_names = tuple(name.strip() for name in extra_names if name.strip())

    def build(self, source: Mapping[str, str] | None = None) -> dict[str, str]:
        values = source if source is not None else os.environ
        allowed_names = DEFAULT_ENV_NAMES | set(self.extra_names)
        env = {
            name: value
            for name, value in values.items()
            if self._is_allowed(name, allowed_names)
        }
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        return env

    def metadata(self, source: Mapping[str, str] | None = None) -> dict:
        values = source if source is not None else os.environ
        inherited = self.build(values)
        inherited_source_keys = sum(1 for name in values if name in inherited)
        return {
            "sanitized": True,
            "inherited_keys": sorted(inherited),
            "filtered_count": max(len(values) - inherited_source_keys, 0),
        }

    def _is_allowed(self, name: str, allowed_names: set[str]) -> bool:
        normalized = name.upper()
        if normalized in NEVER_INHERIT_ENV_NAMES:
            return False
        if SENSITIVE_ENV_NAME_RE.search(normalized):
            return False
        return normalized in allowed_names or normalized.startswith("LC_")
