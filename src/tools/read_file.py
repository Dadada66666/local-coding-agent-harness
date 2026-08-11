from __future__ import annotations

import math

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


DEFAULT_LIMIT = 350
BROAD_READ_MAX_LINES = 200
PAGINATION_PATH_MAX_CHARS = 80
REPEATED_SEGMENT_HINT = "[read_file: segment already returned unchanged]"


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a UTF-8 text file with line numbers."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    read_only = True
    dangerous = False
    concurrency_safe = True

    def classify_operation(self, args: dict, context) -> Operation:
        requested_path = args.get("path", "")
        return Operation(
            kind="fs.read",
            action="read",
            subject=str(requested_path),
            paths=[str(requested_path)] if requested_path else [],
            scope_key=f"read:file:{requested_path}",
            is_read_only=True,
        )

    def validate(self, args: dict, context) -> None:
        unknown = set(args) - {"path", "offset", "limit"}
        if unknown:
            raise ToolValidationError(
                f"unknown read_file fields: {', '.join(sorted(unknown))}"
            )
        if not args.get("path"):
            raise ToolValidationError("read_file requires path")
        if int(args.get("offset", 0)) < 0:
            raise ToolValidationError("offset must be >= 0")
        if int(args.get("limit", DEFAULT_LIMIT)) <= 0:
            raise ToolValidationError("limit must be > 0")

    def call(self, args: dict, context) -> ToolResult:
        requested_path = args["path"]
        target = context.safe_path(requested_path)
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", DEFAULT_LIMIT))

        if context.access_policy.is_protected_resolved_read(context.repo_path, target):
            return ToolResult(
                ok=False,
                content="Permission denied: protected read path.",
                error="protected read",
                metadata={"protected_read": True},
            )
        if not target.exists():
            return ToolResult(ok=False, content=f"File not found: {requested_path}", error="file not found")
        if not target.is_file():
            return ToolResult(ok=False, content=f"Not a file: {requested_path}", error="not a file")

        raw = target.read_bytes()
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as exc:
            return ToolResult(
                ok=False,
                content=f"File is not valid UTF-8: {requested_path}",
                error="decode error",
                metadata={"encoding": "utf-8", "reason": str(exc)},
            )

        snapshot = context.record_file_snapshot(
            target,
            raw,
            partial=True,
            reuse_hash=True,
        )
        state = context.source_read_state(
            target,
            requested_path=requested_path,
            sha256=snapshot.sha256,
            total_lines=len(lines),
        )
        requested_end = min(offset + limit, len(lines))
        requested_lines = max(requested_end - offset, 0)
        already_seen = state.overlap(offset, requested_end)
        overlap_ratio = already_seen / requested_lines if requested_lines else 0.0
        broad_read = requested_lines >= self._broad_read_threshold(len(lines))
        high_overlap = requested_lines > 0 and overlap_ratio >= 0.8
        metrics = context.source_read_metrics
        metrics.read_file_calls += 1
        metrics.files_read.add(state.source_path)
        if high_overlap:
            metrics.high_overlap_rereads += 1

        source_body_available = state.unprojected_observation_count > 0
        if state.fully_scanned and broad_read and high_overlap and source_body_available:
            metrics.redundant_reads_avoided += 1
            context.record_file_snapshot(
                target,
                raw,
                partial=False,
                reuse_hash=True,
            )
            return ToolResult(
                ok=True,
                content=(
                    "This unchanged source file was already fully scanned in the current task.\n"
                    f"Requested range lines {offset + 1}-{requested_end} is "
                    f"{overlap_ratio:.0%} previously covered.\n"
                    "Use grep for symbol discovery or a narrow read_file range for exact "
                    "edit context. File changes automatically invalidate this coverage."
                ),
                metadata={
                    **self._source_metadata(
                        context,
                        target,
                        requested_path,
                        snapshot.sha256,
                        total_lines=len(lines),
                    ),
                    "offset": offset,
                    "limit": limit,
                    "returned_lines": 0,
                    "returned_line_start": None,
                    "returned_line_end": None,
                    "remaining_lines": max(len(lines) - offset, 0),
                    "next_offset": None,
                    "has_more": False,
                    "pagination": "lines",
                    "page_limited_by_chars": False,
                    "repeated_segment": True,
                    "redundant_source": True,
                    "overlap_ratio": round(overlap_ratio, 4),
                    "already_seen_lines": already_seen,
                    "new_lines": 0,
                    "fully_scanned": True,
                    "partial": False,
                    "projection_kind": "source_notice",
                },
            )

        selected = lines[offset : offset + limit]
        display_path = self._display_path(requested_path)
        suffix_reserve = self._pagination_suffix_reserve(
            path=display_path,
            total_lines=len(lines),
        )
        rendered, page_limited, source_line_truncated = self._render_bounded_page(
            selected,
            offset=offset,
            max_chars=max(int(context.config.max_tool_result_chars) - suffix_reserve, 1),
        )
        returned_count = len(rendered)
        returned_end_offset = offset + returned_count
        remaining = max(len(lines) - returned_end_offset, 0)
        has_more = remaining > 0
        next_offset = returned_end_offset if has_more else None

        coverage_end_offset = offset if source_line_truncated else returned_end_offset
        already_seen, new_lines, became_fully_scanned = state.record_range(
            offset,
            coverage_end_offset,
            observation_chars=sum(len(value) + 1 for value in rendered),
            turn_id=getattr(context, "current_turn_id", None),
        )
        returned_overlap_ratio = (
            already_seen / returned_count if returned_count else 0.0
        )
        repeated_segment = returned_count > 0 and already_seen == returned_count
        metrics.unique_source_lines_returned += new_lines
        metrics.duplicate_source_lines_returned += already_seen
        if became_fully_scanned:
            metrics.fully_scanned_files.add(state.source_path)
        partial = not state.fully_scanned
        snapshot = context.record_file_snapshot(
            target,
            raw,
            partial=partial,
            reuse_hash=True,
        )

        if repeated_segment:
            rendered.append(REPEATED_SEGMENT_HINT)
        rendered.append(
            self._pagination_footer(
                path=display_path,
                start=offset + 1 if returned_count else None,
                end=returned_end_offset if returned_count else None,
                total_lines=len(lines),
                next_offset=next_offset,
                complete=state.fully_scanned and not has_more,
                source_line_truncated=source_line_truncated,
            )
        )

        return ToolResult(
            ok=True,
            content="\n".join(rendered),
            metadata={
                "path": str(target),
                "requested_path": requested_path,
                "resolved_path": str(target),
                "offset": offset,
                "limit": limit,
                "total_lines": len(lines),
                "returned_lines": returned_count,
                "returned_line_start": offset + 1 if returned_count else None,
                "returned_line_end": returned_end_offset if returned_count else None,
                "remaining_lines": remaining,
                "next_offset": next_offset,
                "has_more": has_more,
                "pagination": "lines",
                "page_limited_by_chars": page_limited,
                "source_line_truncated": source_line_truncated,
                "repeated_segment": repeated_segment,
                "redundant_source": False,
                "overlap_ratio": round(returned_overlap_ratio, 4),
                "already_seen_lines": already_seen,
                "new_lines": new_lines,
                "snapshot_sha256": snapshot.sha256,
                "source_sha256": snapshot.sha256,
                "source_path": state.source_path,
                "projection_kind": "source_slice",
                "reconstructible": not source_line_truncated,
                "fully_scanned": state.fully_scanned,
                "partial": partial,
            },
        )

    def _render_bounded_page(
        self,
        lines: list[str],
        *,
        offset: int,
        max_chars: int,
    ) -> tuple[list[str], bool, bool]:
        budget = max(max_chars, 1)
        rendered: list[str] = []
        used = 0
        for index, line in enumerate(lines):
            value = f"{offset + index + 1:>4} | {line}"
            added = len(value) + (1 if rendered else 0)
            if rendered and used + added > budget:
                break
            if not rendered and added > budget:
                marker = " ... [line truncated]"
                if budget <= len(marker):
                    rendered.append(marker[:budget])
                else:
                    rendered.append(f"{value[: budget - len(marker)]}{marker}")
                return rendered, True, True
            rendered.append(value)
            used += added
        return rendered, len(rendered) < len(lines), False

    def _broad_read_threshold(self, total_lines: int) -> int:
        if total_lines <= 0:
            return BROAD_READ_MAX_LINES
        return max(50, min(BROAD_READ_MAX_LINES, math.ceil(total_lines * 0.15)))

    def _display_path(self, requested_path: str) -> str:
        if len(requested_path) <= PAGINATION_PATH_MAX_CHARS:
            return requested_path
        tail_chars = PAGINATION_PATH_MAX_CHARS // 2
        head_chars = PAGINATION_PATH_MAX_CHARS - tail_chars - 3
        return f"{requested_path[:head_chars]}...{requested_path[-tail_chars:]}"

    def _pagination_suffix_reserve(self, *, path: str, total_lines: int) -> int:
        maximum_line = max(total_lines, 1)
        variants = (
            self._pagination_footer(
                path=path,
                start=maximum_line,
                end=maximum_line,
                total_lines=total_lines,
                next_offset=maximum_line,
                complete=False,
                source_line_truncated=True,
            ),
            self._pagination_footer(
                path=path,
                start=maximum_line,
                end=maximum_line,
                total_lines=total_lines,
                next_offset=None,
                complete=True,
                source_line_truncated=False,
            ),
        )
        return max(len(value) for value in variants) + len(REPEATED_SEGMENT_HINT) + 2

    def _pagination_footer(
        self,
        *,
        path: str,
        start: int | None,
        end: int | None,
        total_lines: int,
        next_offset: int | None,
        complete: bool,
        source_line_truncated: bool,
    ) -> str:
        returned_range = f"{start}-{end}" if start is not None and end is not None else "none"
        if source_line_truncated:
            status = "line_truncated"
            if next_offset is not None:
                status = f"next_offset={next_offset} | {status}"
        elif complete:
            status = "complete"
        elif next_offset is not None:
            status = f"next_offset={next_offset}"
        else:
            status = "incomplete"
        return (
            f"[read_file: {path} | lines {returned_range} / {total_lines} | {status}]"
        )

    def _source_metadata(
        self,
        context,
        target,
        requested_path: str,
        sha256: str,
        *,
        total_lines: int,
    ) -> dict:
        try:
            source_path = target.relative_to(context.repo_path).as_posix()
        except ValueError:
            source_path = requested_path
        return {
            "path": str(target),
            "requested_path": requested_path,
            "resolved_path": str(target),
            "source_path": source_path,
            "source_sha256": sha256,
            "snapshot_sha256": sha256,
            "total_lines": total_lines,
            "reconstructible": True,
        }
