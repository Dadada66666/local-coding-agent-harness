from __future__ import annotations

from pathlib import Path


BASE_SYSTEM_PROMPT = """You are a local coding agent working inside  {workdir}.

Behavior:
- Inspect relevant files before editing.
- Prefer dedicated tools: read_file for reading, edit_file for edits, bash only for commands.
- Make minimal correct changes.
- After code edits, run the smallest relevant check when available.
- Report honestly if verification was not possible.

Safety:
- Do not attempt destructive operations.
- If permission is denied, treat the operation as cancelled.

Final answer:
- Summary
- Changed files
- Checks run
- Risks
"""

def build_system_prompt(workdir: Path) -> str:
    return BASE_SYSTEM_PROMPT.format(workdir=workdir.resolve())


SYSTEM_PROMPT = build_system_prompt(Path.cwd())


def build_initial_messages(task: str) -> list[dict]:
    return [{"role": "user", "content": task}]