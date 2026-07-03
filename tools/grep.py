from __future__ import annotations

import re
from pathlib import Path

from tools.base import BaseTool, ToolResult, ToolValidationError


SKIP_DIRS = {".agent", ".git", ".venv", "venv", "node_modules", "__pycache__"}
DEFAULT_MAX_MATCHES = 50


class GrepTool(BaseTool):
    name = "grep"
    description = "Search repository text and return matching file paths, line numbers, and previews."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex or literal search pattern."},
            "path": {"type": "string", "description": "Optional path relative to WORKDIR."},
        },
        "required": ["pattern"],
    }

    read_only = True
    dangerous = False
    concurrency_safe = True

    def validate(self, args: dict, context) -> None:
        if not args.get("pattern"):
            raise ToolValidationError("grep requires pattern")
        try:
            re.compile(str(args["pattern"]))
        except re.error as exc:
            raise ToolValidationError(f"invalid regex: {exc}") from exc

    def call(self, args: dict, context) -> ToolResult:
        pattern = re.compile(str(args["pattern"]))
        max_matches = self._max_matches(context)
        root = context.safe_path(args.get("path", "."))

        if not root.exists():
            return ToolResult(ok=False, content=f"Path not found: {args.get('path', '.')}", error="path not found")

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
            metadata={"match_count": len(matches), "scanned_files": scanned_files, "truncated": False},
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