from __future__ import annotations

import math

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


DEFAULT_LIMIT = 200
PAGINATION_RESERVE_CHARS = 240


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a UTF-8 text file with line numbers."
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer"},
            "limit": {"type": "integer"},
            "force": {"type": "boolean"},
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
        if not args.get("path"):
            raise ToolValidationError("read_file requires path")
        if int(args.get("offset", 0)) < 0:
            raise ToolValidationError("offset must be >= 0")
        if int(args.get("limit", DEFAULT_LIMIT)) <= 0:
            raise ToolValidationError("limit must be > 0")
        if "force" in args and not isinstance(args["force"], bool):
            raise ToolValidationError("force must be a boolean")

    def call(self, args: dict, context) -> ToolResult:
        requested_path = args["path"]
        target = context.safe_path(requested_path)
        offset = int(args.get("offset", 0))
        limit = int(args.get("limit", DEFAULT_LIMIT))
        force = bool(args.get("force", False))

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

        if state.fully_scanned and broad_read and high_overlap and not force:
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
                    "Use grep for symbol discovery, a narrow read_file range for exact edit "
                    "context, or force=true for an intentional refresh."
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
        rendered, page_limited = self._render_bounded_page(
            selected,
            offset=offset,
            max_chars=int(context.config.max_tool_result_chars),
        )
        returned_count = len(rendered)
        returned_end_offset = offset + returned_count
        remaining = max(len(lines) - returned_end_offset, 0)
        has_more = remaining > 0
        next_offset = returned_end_offset if has_more else None

        already_seen, new_lines, became_fully_scanned = state.record_range(
            offset,
            returned_end_offset,
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
        if force and state.fully_scanned and high_overlap:
            if state.record_forced_rescan(offset, returned_end_offset):
                metrics.full_rescans += 1
        partial = not state.fully_scanned
        snapshot = context.record_file_snapshot(
            target,
            raw,
            partial=partial,
            reuse_hash=True,
        )

        if repeated_segment:
            rendered.append(
                "[read_file hint: this line segment was already returned unchanged; "
                "use next_offset, grep, or a narrower range when possible]"
            )
        rendered.append(
            "[read_file pagination: "
            f"returned={returned_count} lines; total_lines={len(lines)}; "
            f"next_offset={next_offset}; has_more={str(has_more).lower()}]"
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
                "repeated_segment": repeated_segment,
                "redundant_source": False,
                "overlap_ratio": round(returned_overlap_ratio, 4),
                "already_seen_lines": already_seen,
                "new_lines": new_lines,
                "snapshot_sha256": snapshot.sha256,
                "source_sha256": snapshot.sha256,
                "source_path": state.source_path,
                "projection_kind": "source_slice",
                "reconstructible": True,
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
    ) -> tuple[list[str], bool]:
        budget = max(max_chars - PAGINATION_RESERVE_CHARS, 1)
        rendered: list[str] = []
        used = 0
        for index, line in enumerate(lines):
            value = f"{offset + index + 1:>4} | {line}"
            added = len(value) + (1 if rendered else 0)
            if rendered and used + added > budget:
                break
            if not rendered and added > budget:
                # Preserve the full source line so the generic artifact path remains
                # available for unusual minified or generated files.
                rendered.append(value)
                return rendered, len(lines) > 1
            rendered.append(value)
            used += added
        return rendered, len(rendered) < len(lines)

    def _broad_read_threshold(self, total_lines: int) -> int:
        if total_lines <= 0:
            return DEFAULT_LIMIT
        return max(50, min(DEFAULT_LIMIT, math.ceil(total_lines * 0.15)))

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
