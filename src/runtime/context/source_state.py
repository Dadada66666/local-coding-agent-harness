from __future__ import annotations

from dataclasses import dataclass, field


def merge_ranges(
    ranges: list[tuple[int, int]],
    incoming: tuple[int, int] | None = None,
) -> list[tuple[int, int]]:
    values = [*ranges, *([incoming] if incoming is not None else [])]
    normalized = sorted((start, end) for start, end in values if end > start)
    merged: list[tuple[int, int]] = []
    for start, end in normalized:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return merged


def overlap_length(
    ranges: list[tuple[int, int]],
    start: int,
    end: int,
) -> int:
    if end <= start:
        return 0
    return sum(
        max(0, min(end, covered_end) - max(start, covered_start))
        for covered_start, covered_end in ranges
    )


@dataclass
class SourceReadState:
    source_path: str
    sha256: str
    total_lines: int
    covered_ranges: list[tuple[int, int]] = field(default_factory=list)
    fully_scanned: bool = False
    last_read_turn: int | None = None
    observation_ids: list[str] = field(default_factory=list)
    projected_observation_ids: set[str] = field(default_factory=set)

    def overlap(self, start: int, end: int) -> int:
        return overlap_length(self.covered_ranges, start, end)

    def record_range(
        self,
        start: int,
        end: int,
        *,
        turn_id: int | None,
    ) -> tuple[int, int, bool]:
        requested = max(end - start, 0)
        already_seen = self.overlap(start, end)
        new_lines = max(requested - already_seen, 0)
        was_complete = self.fully_scanned
        self.covered_ranges = merge_ranges(self.covered_ranges, (start, end))
        self.fully_scanned = self._covers_all_lines()
        self.last_read_turn = turn_id
        return already_seen, new_lines, self.fully_scanned and not was_complete

    def _covers_all_lines(self) -> bool:
        return self._ranges_cover_all(self.covered_ranges)

    def _ranges_cover_all(self, ranges: list[tuple[int, int]]) -> bool:
        if self.total_lines == 0:
            return True
        return bool(ranges and ranges[0][0] == 0 and ranges[0][1] >= self.total_lines)


@dataclass
class SourceReadMetrics:
    read_file_calls: int = 0
    unique_source_lines_returned: int = 0
    duplicate_source_lines_returned: int = 0
    rehydration_reads: int = 0
    rehydrated_source_lines: int = 0
    high_overlap_rereads: int = 0
    redundant_reads_avoided: int = 0
    source_observations_projected: int = 0
    files_read: set[str] = field(default_factory=set)
    fully_scanned_files: set[str] = field(default_factory=set)
