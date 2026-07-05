from __future__ import annotations

import platform
from pathlib import Path


BASE_SYSTEM_PROMPT = """You are a local coding agent working inside {workdir}.

Runtime:
- OS: {os_name}
- Command shell: {shell_name}

Behavior:
- Inspect relevant context before making changes.
- Use the available tools according to their purpose.
- Use the least context sufficient to make a correct next action
- After code edits, run the smallest relevant check when available.
- Report honestly if verification was not possible.
- For commands that require stdin, use the bash tool input field.
- When running a command to validate behavior, set bash purpose to "verify".

Safety:
- Do not attempt destructive operations.
- If permission is denied, treat the operation as cancelled.

Final answer:
- Summary
- Changed files
- Checks run
- Risks
"""


def detect_shell_name() -> str:
    if platform.system() == "Windows":
        return "PowerShell, preferring pwsh then powershell.exe, via subprocess shell=False"
    return "/bin/sh via subprocess shell=False"


def build_system_prompt(workdir: Path) -> str:
    return BASE_SYSTEM_PROMPT.format(
        workdir=workdir.resolve(),
        os_name=platform.system(),
        shell_name=detect_shell_name(),
    )


SYSTEM_PROMPT = build_system_prompt(Path.cwd())


def build_initial_messages(task: str) -> list[dict]:
    return [{"role": "user", "content": task}]

