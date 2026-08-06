from __future__ import annotations

import re

from runtime.security.permission_models import BashRisk, BashRiskDecision
from runtime.security.shell_analysis import (
    analyze_shell_effects,
    mask_quoted_text,
    redirection_targets,
    split_shell_segments,
)


class RiskClassifier:
    PYTHON_OPEN_WRITE_RE = re.compile(
        r"""\bopen\s*\(\s*(['\"])(?P<path>[^'\"]+)\1\s*,\s*(?:mode\s*=\s*)?(['\"])(?P<mode>[^'\"]*[wax][^'\"]*)\3""",
        re.IGNORECASE,
    )
    PYTHON_PATH_WRITE_RE = re.compile(
        r"""(?:\bPath|\bpathlib\.Path)\s*\(\s*(['\"])(?P<path>[^'\"]+)\1\s*\)\s*\.\s*(?P<method>write_text|write_bytes)\s*\(""",
        re.IGNORECASE,
    )
    PYTHON_PATH_DELETE_RE = re.compile(
        r"""(?:\bPath|\bpathlib\.Path)\s*\(\s*(['\"])(?P<path>[^'\"]+)\1\s*\)\s*\.\s*unlink\s*\(""",
        re.IGNORECASE,
    )
    PYTHON_OS_DELETE_RE = re.compile(
        r"""\bos\s*\.\s*(?:remove|unlink)\s*\(\s*(['\"])(?P<path>[^'\"]+)\1""",
        re.IGNORECASE,
    )
    SHELL_DELETE_COMMAND_RE = re.compile(
        r"""(?:^|&&|\|\||\||;|[\r\n])\s*(?P<command>rm|unlink|remove-item|del|erase)(?:\s|$)""",
        re.IGNORECASE,
    )
    APPLY_PATCH_COMMAND_RE = re.compile(
        r"""(?:^|&&|\|\||\||;|[\r\n])\s*apply_patch(?:\s|$)""",
        re.IGNORECASE,
    )
    HEREDOC_START_RE = re.compile(
        r"""<<-?\s*(?P<quote>['"]?)(?P<tag>[A-Za-z_][A-Za-z0-9_]*) (?P=quote)""".replace(
            " ", ""
        ),
        re.IGNORECASE,
    )
    DYNAMIC_SHELL_RE = re.compile(r"\$\(|`|<\(|>\(", re.IGNORECASE)

    DESTRUCTIVE_PATTERNS = [
        "rm -rf",
        "rm -fr",
        "git reset --hard",
        "git clean",
        "sudo",
        "chmod 777",
        "mkfs",
        "dd if=",
        "shutdown",
        "reboot",
        ":(){ :|:& };:",
        "remove-item -recurse",
        "shutil.rmtree",
        "rmdir",
    ]
    FILE_WRITE_PATTERNS = {
        "set-content": ("PowerShell Set-Content writes file content.", "write_file"),
        "out-file": ("PowerShell Out-File writes command output to a file.", "write_file"),
        "add-content": ("PowerShell Add-Content appends file content.", "edit_file"),
        "sed -i": ("sed -i edits a file in place through shell.", "edit_file"),
        "tee ": ("tee can write command output to a file.", "write_file"),
        "tee-object": ("PowerShell Tee-Object can write command output to a file.", "write_file"),
        "new-item": ("PowerShell New-Item can create files or directories.", "write_file"),
        "copy-item": ("PowerShell Copy-Item writes a destination path.", "write_file"),
        "move-item": ("PowerShell Move-Item mutates filesystem paths.", "edit_file"),
        "clear-content": ("PowerShell Clear-Content clears file content.", "edit_file"),
    }
    NETWORK_PATTERNS = [
        "curl",
        "wget",
        "invoke-webrequest",
        "invoke-restmethod",
        "irm ",
        "iwr ",
        "pip install",
        "npm install",
        "git clone",
        "git pull",
        "git fetch",
    ]
    SAFE_CHECK_PREFIXES = [
        "pytest",
        "python -m pytest",
        "python3 -m pytest",
        "py -m pytest",
        "python -m py_compile",
        "python3 -m py_compile",
        "py -m py_compile",
        "python -m compileall",
        "python3 -m compileall",
        "py -m compileall",
        "uv run pytest",
        "poetry run pytest",
        "ruff check",
        "mypy",
        "npm test",
        "node --check",
        "sh -n",
        "bash -n",
        "python -m unittest",
        "python3 -m unittest",
        "test",
        "[",
    ]
    READ_ONLY_PREFIXES = [
        "git status",
        "git diff",
        "git log",
        "dir",
        "ls",
        "pwd",
        "tree",
        "get-childitem",
        "gci",
        "get-location",
        "gl",
        "select-string",
        "sls",
        "type",
        "cat",
        "get-content",
        "findstr",
        "grep",
        "rg",
        "echo",
        "printf",
        "head",
        "tail",
        "wc",
        "stat",
        "file",
        "which",
        "where",
        "test-path",
    ]
    PYTHON_INLINE_PREFIXES = ["python -c", "python3 -c", "py -c"]

    def classify_bash(self, command: str) -> BashRiskDecision:
        syntax_command = self._strip_heredoc_bodies(command)
        normalized = f" {mask_quoted_text(syntax_command).strip().lower()} "
        command_prefix = self._command_prefix(command)
        effects = analyze_shell_effects(syntax_command)

        destructive_pattern = self._matched_pattern(normalized, self.DESTRUCTIVE_PATTERNS)
        if destructive_pattern:
            return BashRiskDecision(
                risk=BashRisk.DESTRUCTIVE,
                reason=f"Matched destructive shell pattern: {destructive_pattern}.",
                target_paths=[],
                command_prefix=command_prefix,
                suggested_tool=None,
                confidence="high",
            )

        delete_paths, delete_kind = self._delete_details(command, normalized)
        if delete_kind:
            return BashRiskDecision(
                risk=BashRisk.FILE_DELETE_VIA_BASH,
                reason=f"{delete_kind} deletes files through shell.",
                target_paths=delete_paths,
                command_prefix=command_prefix,
                suggested_tool="delete_file",
                confidence="high" if delete_paths else "medium",
            )

        python_paths, python_write_kind, python_suggested_tool = self._python_write_details(command)
        if python_paths or python_write_kind:
            return BashRiskDecision(
                risk=BashRisk.FILE_WRITE_VIA_BASH,
                reason=f"Python {python_write_kind} writes a file through shell.",
                target_paths=python_paths,
                command_prefix=command_prefix,
                suggested_tool=python_suggested_tool,
                confidence="high" if python_paths else "medium",
            )

        if self._is_apply_patch_command(mask_quoted_text(syntax_command)):
            return BashRiskDecision(
                risk=BashRisk.FILE_WRITE_VIA_BASH,
                reason="apply_patch through shell can write files.",
                target_paths=[],
                command_prefix=command_prefix,
                suggested_tool="edit_file",
                confidence="high",
                execution_route="structured_tool",
            )

        file_write = self._matched_file_write_pattern(normalized)
        network_pattern = self._matched_pattern(normalized, self.NETWORK_PATTERNS)
        if effects.has_network or network_pattern:
            network_program = effects.network_program or network_pattern or command_prefix
            effect_names = ["network"]
            if effects.has_file_mutation:
                effect_names.append("file_write")
            return BashRiskDecision(
                risk=BashRisk.NETWORK,
                reason=f"Matched network command: {network_program}.",
                target_paths=list(effects.mutation_paths),
                command_prefix=network_program,
                suggested_tool=None,
                confidence="high",
                effects=tuple(effect_names),
                network_hosts=effects.network_hosts,
            )

        if file_write is not None:
            pattern, reason, suggested_tool = file_write
            return BashRiskDecision(
                risk=BashRisk.FILE_WRITE_VIA_BASH,
                reason=f"{reason} Matched pattern: {pattern}.",
                target_paths=[],
                command_prefix=command_prefix,
                suggested_tool=suggested_tool,
                confidence="high",
                effects=("file_write",),
            )

        if not self._is_python_inline(command_prefix) and effects.has_file_mutation:
            if effects.metadata_write_paths:
                reason = "chmod mutates file metadata through shell."
                suggested_tool = None
            elif effects.directory_write_paths:
                reason = "Shell command creates filesystem directories."
                suggested_tool = "write_file"
            else:
                reason = "Shell redirection writes command output to a file."
                suggested_tool = "write_file"
            return BashRiskDecision(
                risk=BashRisk.FILE_WRITE_VIA_BASH,
                reason=reason,
                target_paths=list(effects.mutation_paths),
                command_prefix=command_prefix,
                suggested_tool=suggested_tool,
                confidence="high",
                effects=("file_write",),
            )

        segment_risk, segment_prefix = self._classify_shell_segments(command)
        if segment_risk == BashRisk.SAFE_CHECK:
            return BashRiskDecision(
                risk=BashRisk.SAFE_CHECK,
                reason="Every shell segment is a recognized verification or read-only command.",
                target_paths=[],
                command_prefix=segment_prefix,
                suggested_tool=None,
                confidence="high",
            )

        if segment_risk == BashRisk.READ_ONLY_COMMAND:
            return BashRiskDecision(
                risk=BashRisk.READ_ONLY_COMMAND,
                reason="Every shell segment is a recognized read-only command.",
                target_paths=[],
                command_prefix=segment_prefix,
                suggested_tool=None,
                confidence="high",
            )

        return BashRiskDecision(
            risk=BashRisk.UNKNOWN,
            reason="No deterministic risk rule matched this shell command.",
            target_paths=[],
            command_prefix=command_prefix,
            suggested_tool=None,
            confidence="low",
        )

    def _classify_shell_segments(self, command: str) -> tuple[str | None, str | None]:
        if self.DYNAMIC_SHELL_RE.search(command):
            return None, None

        segments = [segment.strip().lower() for segment in split_shell_segments(command)]
        if not segments:
            return None, None

        first_prefix = None
        has_safe_check = False
        for segment in segments:
            safe_prefix = self._matched_prefix(segment, self.SAFE_CHECK_PREFIXES)
            read_only_prefix = self._matched_prefix(segment, self.READ_ONLY_PREFIXES)
            matched_prefix = safe_prefix or read_only_prefix
            if matched_prefix is None:
                return None, None
            if first_prefix is None:
                first_prefix = matched_prefix
            has_safe_check = has_safe_check or safe_prefix is not None

        risk = BashRisk.SAFE_CHECK if has_safe_check else BashRisk.READ_ONLY_COMMAND
        return risk, first_prefix

    def _delete_details(self, command: str, normalized: str) -> tuple[list[str], str | None]:
        paths = []
        for pattern in (self.PYTHON_PATH_DELETE_RE, self.PYTHON_OS_DELETE_RE):
            for match in pattern.finditer(command):
                path = self._clean_target_path(match.group("path"))
                if path:
                    paths.append(path)

        if paths:
            return self._unique(paths), "Python file deletion API"

        shell_syntax = mask_quoted_text(self._strip_heredoc_bodies(command))
        matched = self.SHELL_DELETE_COMMAND_RE.search(shell_syntax)
        if matched:
            return [], f"Matched file deletion command: {matched.group('command').lower()}"
        return [], None

    def _is_apply_patch_command(self, command: str) -> bool:
        return bool(self.APPLY_PATCH_COMMAND_RE.search(command))

    def _python_write_details(self, command: str) -> tuple[list[str], str | None, str | None]:
        paths: list[str] = []
        modes: list[str] = []
        methods: list[str] = []

        for match in self.PYTHON_OPEN_WRITE_RE.finditer(command):
            path = self._clean_target_path(match.group("path"))
            if path:
                paths.append(path)
            modes.append(match.group("mode").lower())

        for match in self.PYTHON_PATH_WRITE_RE.finditer(command):
            path = self._clean_target_path(match.group("path"))
            if path:
                paths.append(path)
            methods.append(match.group("method"))

        if not modes and not methods:
            return [], None, None

        if modes and methods:
            kind = "open(..., write mode) or Path.write_* API"
        elif modes:
            kind = "open(..., write mode)"
        else:
            kind = "Path.write_text/write_bytes API"

        suggested_tool = "edit_file" if any(mode.startswith("a") for mode in modes) else "write_file"
        return self._unique(paths), kind, suggested_tool

    def _redirection_targets(self, command: str) -> list[str]:
        command = self._strip_heredoc_bodies(command)
        return list(redirection_targets(command))

    def _strip_heredoc_bodies(self, command: str) -> str:
        lines = command.splitlines()
        if not lines:
            return command

        kept: list[str] = []
        active_tag: str | None = None

        for line in lines:
            if active_tag is not None:
                if line.strip() == active_tag:
                    active_tag = None
                continue

            kept.append(line)
            match = self.HEREDOC_START_RE.search(line)
            if match:
                active_tag = match.group("tag")

        return "\n".join(kept)

    def _matched_file_write_pattern(self, normalized: str) -> tuple[str, str, str] | None:
        for pattern, details in self.FILE_WRITE_PATTERNS.items():
            if pattern in normalized:
                reason, suggested_tool = details
                return pattern, reason, suggested_tool
        return None

    def _matched_pattern(self, normalized: str, patterns: list[str]) -> str | None:
        for pattern in patterns:
            if pattern in normalized:
                return pattern
        return None

    def _matched_prefix(self, stripped_command: str, prefixes: list[str]) -> str | None:
        for prefix in prefixes:
            if stripped_command == prefix or stripped_command.startswith(f"{prefix} "):
                return prefix
        return None

    def _command_prefix(self, command: str) -> str | None:
        stripped = command.strip()
        if not stripped:
            return None

        lowered = stripped.lower()

        if lowered.startswith(("python -c", "python3 -c", "py -c")):
            return "python -c"

        if lowered.startswith(("python -m", "python3 -m", "py -m")):
            parts = stripped.split()
            if len(parts) >= 3:
                return f"{parts[0]} -m {parts[2]}".lower()
            return "python -m"

        if lowered.startswith(("python ", "python3 ", "py ")):
            return "python script"

        if lowered.startswith("powershell"):
            return "powershell"

        if lowered.startswith("pwsh"):
            return "pwsh"

        if lowered.startswith("cmd /c"):
            return "cmd /c"

        known_prefixes = sorted(
            self.SAFE_CHECK_PREFIXES + self.READ_ONLY_PREFIXES,
            key=len,
            reverse=True,
        )
        for prefix in known_prefixes:
            if lowered == prefix or lowered.startswith(f"{prefix} "):
                return prefix

        return " ".join(stripped.split()[:2])

    def _is_python_inline(self, command_prefix: str | None) -> bool:
        return command_prefix in self.PYTHON_INLINE_PREFIXES

    def _clean_target_path(self, path: str) -> str | None:
        cleaned = path.strip().strip("'\"").rstrip(",)")
        if not cleaned:
            return None
        if cleaned.lower() in {"nul", "/dev/null"}:
            return None
        if cleaned.startswith(("$", "%")):
            return None
        return cleaned

    def _unique(self, paths: list[str]) -> list[str]:
        seen: set[str] = set()
        unique_paths: list[str] = []
        for path in paths:
            if path not in seen:
                seen.add(path)
                unique_paths.append(path)
        return unique_paths

