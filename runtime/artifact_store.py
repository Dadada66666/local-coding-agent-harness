from __future__ import annotations

from pathlib import Path
from uuid import uuid4


class ArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.artifacts_dir = run_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def save_text(self, name_hint: str, content: str) -> Path:
        path = self.artifacts_dir / f"{uuid4().hex[:8]}-{name_hint}"
        path.write_text(content, encoding="utf-8")
        return path

