from __future__ import annotations

import subprocess
from pathlib import Path


class DiffManager:
    def __init__(self, repo_path: Path, run_dir: Path) -> None:
        self.repo_path = repo_path
        self.path = run_dir / "diff.patch"

    def write_patch(self, context=None) -> Path:
        completed = subprocess.run(
            ["git", "diff", "--"],
            cwd=self.repo_path,
            text=True,
            capture_output=True,
            check=False,
        )
        content = completed.stdout if completed.returncode == 0 else completed.stderr
        self.path.write_text(content, encoding="utf-8")
        return self.path

