from __future__ import annotations

import subprocess
from pathlib import Path


class DiffManager:
    def __init__(self, repo_path: Path, run_dir: Path) -> None:
        self.repo_path = repo_path
        self.path = run_dir / "diff.patch"

    def write_diff(self) -> None:
        result = subprocess.run(
            ["git", "diff", "--"],
            cwd=self.repo_path,
            text=True,
            capture_output=True,
            check=False,
        )
        self.path.write_text(result.stdout if result.returncode == 0 else result.stderr, encoding="utf-8")

