from __future__ import annotations


SYSTEM_PROMPT = """You are a local coding agent.
Inspect the repository, make minimal correct edits, run tests, and report the result.
All file writes and shell commands must go through tools.
"""


def build_initial_messages(task: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]

