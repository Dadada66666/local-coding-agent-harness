from __future__ import annotations

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

        snapshot = context.record_file_snapshot(target, raw, partial=True)
        segment = (offset, returned_end_offset, snapshot.sha256)
        segment_store = getattr(context, "read_file_segments", None)
        if segment_store is None:
            segment_store = {}
            context.read_file_segments = segment_store
        seen = segment_store.setdefault(str(target), set())
        repeated_segment = segment in seen
        seen.add(segment)
        partial = not self._covers_file(seen, snapshot.sha256, len(lines))
        snapshot = context.record_file_snapshot(target, raw, partial=partial)

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
                "snapshot_sha256": snapshot.sha256,
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

    def _covers_file(
        self,
        segments: set[tuple[int, int, str]],
        sha256: str,
        total_lines: int,
    ) -> bool:
        if total_lines == 0:
            return True
        covered_until = 0
        for start, end, segment_sha in sorted(segments):
            if segment_sha != sha256 or start > covered_until:
                continue
            covered_until = max(covered_until, end)
            if covered_until >= total_lines:
                return True
        return False
