from __future__ import annotations

from pathlib import Path

from runtime.diff_manager import DiffManager


def test_diff_manager_reports_non_git_directory(tmp_path: Path) -> None:
    path = DiffManager(tmp_path, tmp_path / "run").write_patch()

    assert path.read_text(encoding="utf-8") == "No git repository; diff unavailable.\n"
