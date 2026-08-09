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
    completed_turn: int | None = None
    fully_scanned_consumed: bool = False
    unique_observation_chars: int = 0
    observation_ids: list[str] = field(default_factory=list)
    projected_observation_ids: set[str] = field(default_factory=set)
    forced_rescan_ranges: list[tuple[int, int]] = field(default_factory=list)

    def overlap(self, start: int, end: int) -> int:
        return overlap_length(self.covered_ranges, start, end)

    def record_range(
        self,
        start: int,
        end: int,
        *,
        observation_chars: int,
        turn_id: int | None,
    ) -> tuple[int, int, bool]:
        requested = max(end - start, 0)
        already_seen = self.overlap(start, end)
        new_lines = max(requested - already_seen, 0)
        was_complete = self.fully_scanned
        self.covered_ranges = merge_ranges(self.covered_ranges, (start, end))
        self.fully_scanned = self._covers_all_lines()
        self.last_read_turn = turn_id
        if new_lines and requested:
            self.unique_observation_chars += int(observation_chars * new_lines / requested)
        if self.fully_scanned and not was_complete:
            self.completed_turn = turn_id
            self.fully_scanned_consumed = False
        return already_seen, new_lines, self.fully_scanned and not was_complete

    def record_forced_rescan(self, start: int, end: int) -> bool:
        if start == 0:
            self.forced_rescan_ranges = []
        if not self.forced_rescan_ranges and start != 0:
            return False
        self.forced_rescan_ranges = merge_ranges(
            self.forced_rescan_ranges,
            (start, end),
        )
        if self._ranges_cover_all(self.forced_rescan_ranges):
            self.forced_rescan_ranges = []
            return True
        return False

    def mark_consumed(self, turn_id: int | None) -> None:
        if (
            self.fully_scanned
            and not self.fully_scanned_consumed
            and self.completed_turn is not None
            and turn_id is not None
            and turn_id > self.completed_turn
        ):
            self.fully_scanned_consumed = True

    @property
    def active(self) -> bool:
        return not self.fully_scanned or not self.fully_scanned_consumed

    @property
    def estimated_tokens(self) -> int:
        return (self.unique_observation_chars + 2) // 3

    @property
    def unprojected_observation_count(self) -> int:
        return sum(
            tool_call_id not in self.projected_observation_ids
            for tool_call_id in self.observation_ids
        )

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
    high_overlap_rereads: int = 0
    redundant_reads_avoided: int = 0
    full_rescans: int = 0
    source_observations_projected: int = 0
    source_projection_protections: int = 0
    source_artifacts_created: int = 0
    source_snapshots_persisted: int = 0
    files_read: set[str] = field(default_factory=set)
    fully_scanned_files: set[str] = field(default_factory=set)
