from __future__ import annotations

from pathlib import Path


class ArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.artifacts_dir = run_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def persist(self, tool_call_id: str, content: str) -> str:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in tool_call_id)
        path = self.artifacts_dir / f"{safe_id}.txt"
        suffix = 1

        while path.exists():
            path = self.artifacts_dir / f"{safe_id}-{suffix}.txt"
            suffix += 1

        path.write_text(content, encoding="utf-8")
        return str(path)
