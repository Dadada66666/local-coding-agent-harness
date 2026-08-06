from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


@dataclass(frozen=True)
class ArtifactReference:
    artifact_id: str
    path: Path
    chars: int
    tool_call_id: str


@dataclass(frozen=True)
class ArtifactSlice:
    artifact_id: str
    content: str
    offset: int
    next_offset: int
    total_chars: int

    @property
    def has_more(self) -> bool:
        return self.next_offset < self.total_chars


class ArtifactStore:
    def __init__(self, run_dir: Path) -> None:
        self.artifacts_dir = run_dir / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, ArtifactReference] = {}

    def persist(self, tool_call_id: str, content: str) -> ArtifactReference:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        artifact_id = f"artifact_{uuid4().hex[:16]}"
        path = self.artifacts_dir / f"{artifact_id}.txt"

        while path.exists() or artifact_id in self._records:
            artifact_id = f"artifact_{uuid4().hex[:16]}"
            path = self.artifacts_dir / f"{artifact_id}.txt"

        path.write_text(content, encoding="utf-8")
        reference = ArtifactReference(
            artifact_id=artifact_id,
            path=path,
            chars=len(content),
            tool_call_id=str(tool_call_id),
        )
        self._records[artifact_id] = reference
        return reference

    def read(self, artifact_id: str, *, offset: int, limit: int) -> ArtifactSlice:
        reference = self._records.get(artifact_id)
        if reference is None:
            raise KeyError(artifact_id)
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if limit <= 0:
            raise ValueError("limit must be > 0")

        effective_offset = min(offset, reference.chars)
        selected = self._read_chars(
            reference.path,
            offset=effective_offset,
            limit=limit,
        )
        next_offset = effective_offset + len(selected)
        return ArtifactSlice(
            artifact_id=artifact_id,
            content=selected,
            offset=effective_offset,
            next_offset=next_offset,
            total_chars=reference.chars,
        )

    def get(self, artifact_id: str) -> ArtifactReference | None:
        return self._records.get(artifact_id)

    def _read_chars(self, path: Path, *, offset: int, limit: int) -> str:
        with path.open("r", encoding="utf-8") as file:
            remaining = offset
            while remaining > 0:
                skipped = file.read(min(remaining, 8192))
                if not skipped:
                    return ""
                remaining -= len(skipped)
            return file.read(limit)
