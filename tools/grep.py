from __future__ import annotations

import re
from pathlib import Path

from runtime.operation import Operation
from tools.base import BaseTool, ToolResult, ToolValidationError


SKIP_DIRS = {".agent", ".git", ".venv", "venv", "node_modules", "__pycache__"}
DEFAULT_MAX_MATCHES = 50


class GrepTool(BaseTool):
    name = "grep"
    description = "Search repository text with a regex pattern."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["pattern"],
    }

    read_only = True
    dangerous = False
    concurrency_safe = True

    def classify_operation(self, args: dict, context) -> Operation:
        requested_path = args.get("path", ".")
        return Operation(
            kind="fs.read",
            action="search",
            subject=str(requested_path),
            paths=[str(requested_path)],
            scope_key=f"read:grep:{requested_path}",
            is_read_only=True,
            metadata={"pattern": str(args.get("pattern", ""))},
        )

    def validate(self, args: dict, context) -> None:
        if not args.get("pattern"):
            raise ToolValidationError("grep requires pattern")
        try:
            re.compile(str(args["pattern"]))
        except re.error as exc:
            raise ToolValidationError(f"invalid regex: {exc}") from exc

    def call(self, args: dict, context) -> ToolResult:
        requested_pattern = str(args["pattern"])
        requested_path = args.get("path", ".")
        pattern = re.compile(requested_pattern)
        max_matches = self._max_matches(context)
        root = context.safe_path(requested_path)

        if not root.exists():
            return ToolResult(ok=False, content=f"Path not found: {requested_path}", error="path not found")

        files = [] if self._is_skipped(root, context) else [root] if root.is_file() else self._iter_files(root, context)
        matches: list[str] = []
        scanned_files = 0

        for file_path in files:
            scanned_files += 1
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue

            for line_no, line in enumerate(lines, start=1):
                if pattern.search(line):
                    rel_path = file_path.relative_to(context.repo_path)
                    preview = line.strip()
                    matches.append(f"{rel_path}:{line_no}: {preview}")
                    if len(matches) >= max_matches:
                        return ToolResult(
                            ok=True,
                            content="\n".join(matches),
                            metadata={
                                "searches_names": False,
                                "searches_content": True,
                                "pattern": requested_pattern,
                                "path": requested_path,
                                "resolved_path": str(root),
                                "match_count": len(matches),
                                "scanned_files": scanned_files,
                                "truncated": True,
                                "max_matches": max_matches,
                                "hint": "Results truncated. Narrow the path or pattern to inspect more precisely.",
                            },
                        )

        content = "\n".join(matches) if matches else "No matches found."
        return ToolResult(
            ok=True,
            content=content,
            metadata={
                "searches_names": False,
                "searches_content": True,
                "pattern": requested_pattern,
                "path": requested_path,
                "resolved_path": str(root),
                "match_count": len(matches),
                "scanned_files": scanned_files,
                "truncated": False,
            },
        )

    def _max_matches(self, context) -> int:
        try:
            value = int(getattr(context.config, "grep_max_matches", DEFAULT_MAX_MATCHES))
        except (TypeError, ValueError):
            value = DEFAULT_MAX_MATCHES
        return max(value, 1)

    def _iter_files(self, root: Path, context):
        workdir = context.repo_path.resolve()
        for path in root.rglob("*"):
            if self._is_skipped(path, context):
                continue
            if not path.is_file():
                continue
            if not path.resolve().is_relative_to(workdir):
                continue
            yield path

    def _is_skipped(self, path: Path, context) -> bool:
        try:
            parts = path.resolve().relative_to(context.repo_path.resolve()).parts
        except ValueError:
            parts = path.parts
        return any(part in SKIP_DIRS for part in parts)
