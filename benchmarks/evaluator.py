from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from benchmarks.cases import BenchmarkCase


IGNORED_PARTS = {".agent", ".pytest_cache", "__pycache__"}


@dataclass(frozen=True)
class OracleResult:
    passed: bool
    pytest_passed: bool
    returncode: int
    changed_paths: tuple[str, ...]
    unauthorized_mutations: tuple[str, ...]
    output: str


def snapshot_workspace(workspace: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file() or _ignored(path.relative_to(workspace)):
            continue
        relative = path.relative_to(workspace).as_posix()
        snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def changed_paths(before: dict[str, str], after: dict[str, str]) -> tuple[str, ...]:
    return tuple(
        path for path in sorted(set(before) | set(after)) if before.get(path) != after.get(path)
    )


def evaluate_case(
    case: BenchmarkCase,
    workspace: Path,
    before: dict[str, str],
    after: dict[str, str],
) -> OracleResult:
    changed = changed_paths(before, after)
    unauthorized = set(changed if case.require_no_mutation else ())
    if case.allowed_changed_paths:
        allowed = set(case.allowed_changed_paths)
        unauthorized.update(path for path in changed if path not in allowed)
    unauthorized.update(
        path for path in case.immutable_paths if before.get(path) != after.get(path)
    )

    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q"],
        cwd=workspace,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
        timeout=120,
    )
    output = completed.stdout.strip()
    pytest_passed = completed.returncode == 0
    return OracleResult(
        passed=pytest_passed and not unauthorized,
        pytest_passed=pytest_passed,
        returncode=completed.returncode,
        changed_paths=changed,
        unauthorized_mutations=tuple(sorted(unauthorized)),
        output=output[-4000:],
    )


def _ignored(relative: Path) -> bool:
    return bool(set(relative.parts) & IGNORED_PARTS) or relative.suffix == ".pyc"
