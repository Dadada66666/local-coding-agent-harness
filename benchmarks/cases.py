from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    title: str
    fixture: Path
    task: str
    plan_policy: str = "off"
    require_no_mutation: bool = False
    immutable_paths: tuple[str, ...] = ()


def resume_cases() -> tuple[BenchmarkCase, ...]:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    return (
        BenchmarkCase(
            case_id="fix_bug",
            title="Deterministic bug fix",
            fixture=fixtures / "fix_bug",
            task=(
                "Inspect this tiny Python repository, fix the implementation bug, run "
                "`python -m pytest -q` as authoritative verification, and finish. "
                "Do not modify the tests."
            ),
            immutable_paths=("test_calculator.py",),
        ),
        BenchmarkCase(
            case_id="validation_only",
            title="Validation only",
            fixture=fixtures / "validation_only",
            task=(
                "Inspect and validate this healthy Python repository. Run "
                "`python -m pytest -q` as authoritative verification and report the result. "
                "This is validation-only: do not modify repository files."
            ),
            require_no_mutation=True,
        ),
        BenchmarkCase(
            case_id="required_plan",
            title="Required Plan bug fix",
            fixture=fixtures / "required_plan",
            task=(
                "Inspect this tiny Python repository, create the required plan, fix the "
                "implementation bug without changing tests, run `python -m pytest -q` as "
                "authoritative verification, complete the plan, and finish."
            ),
            plan_policy="required",
            immutable_paths=("test_parity.py",),
        ),
    )
