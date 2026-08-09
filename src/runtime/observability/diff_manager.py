from __future__ import annotations

import subprocess
from pathlib import Path


class DiffManager:
    def __init__(self, repo_path: Path, run_dir: Path) -> None:
        self.repo_path = repo_path
        self.path = run_dir / "diff.patch"
        self.availability = "unknown"
        self.reason = "not generated"

    def write_patch(self, context=None) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.probe_availability() != "available":
            self.path.write_text("No git repository; diff unavailable.\n", encoding="utf-8")
            return self.path

        completed = subprocess.run(
            ["git", "diff", "--"],
            cwd=self.repo_path,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if completed.returncode == 0:
            self.availability = "available"
            self.reason = "git diff completed"
            content = completed.stdout
        else:
            self.availability = "failed"
            self.reason = f"git diff exited with {completed.returncode}"
            content = (
                f"git diff failed with exit code {completed.returncode}.\n\n"
                f"{completed.stderr or completed.stdout or ''}"
            )
        redactor = getattr(context, "redactor", None)
        if redactor is not None:
            content = redactor.redact(content)
        self.path.write_text(content, encoding="utf-8")
        return self.path

    def probe_availability(self) -> str:
        if self.availability != "unknown":
            return self.availability
        if self._is_git_work_tree():
            self.availability = "available"
            self.reason = "git repository detected"
        else:
            self.availability = "unavailable"
            self.reason = "workdir is not a git repository"
        return self.availability

    def _is_git_work_tree(self) -> bool:
        completed = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=self.repo_path,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        return completed.returncode == 0 and completed.stdout.strip() == "true"
