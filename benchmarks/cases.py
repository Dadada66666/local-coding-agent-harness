from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    title: str
    fixture: Path
    task: str
    difficulty: str
    category: str
    plan_policy: str = "off"
    require_no_mutation: bool = False
    immutable_paths: tuple[str, ...] = ()
    allowed_changed_paths: tuple[str, ...] = ()


def resume_cases() -> tuple[BenchmarkCase, ...]:
    fixtures = Path(__file__).resolve().parent / "fixtures"
    return (
        BenchmarkCase(
            case_id="fix_bug",
            title="Deterministic bug fix",
            fixture=fixtures / "fix_bug",
            task=(
                "Inspect this small Python project, diagnose and fix the calculation bug, "
                "run the repository's tests as authoritative verification, and finish. "
                "Do not modify tests."
            ),
            difficulty="smoke",
            category="mutation",
            immutable_paths=("test_calculator.py",),
            allowed_changed_paths=("calculator.py",),
        ),
        BenchmarkCase(
            case_id="validation_only",
            title="Validation only",
            fixture=fixtures / "validation_only",
            task=(
                "Inspect and validate this healthy Python repository using its documented "
                "authoritative verification command, then report the result. This is "
                "validation-only: do not modify repository files."
            ),
            difficulty="smoke",
            category="validation",
            require_no_mutation=True,
        ),
        BenchmarkCase(
            case_id="required_plan",
            title="Required Plan bug fix",
            fixture=fixtures / "required_plan",
            task=(
                "Inspect this small Python project, create the required plan, fix the pricing "
                "calculation bug without changing tests, run the repository's tests as "
                "authoritative verification, complete the plan, and finish."
            ),
            difficulty="smoke",
            category="planning",
            plan_policy="required",
            immutable_paths=("test_pricing.py",),
            allowed_changed_paths=("pricing.py",),
        ),
        BenchmarkCase(
            case_id="cross_module_bug",
            title="Cross-module bug",
            fixture=fixtures / "cross_module_bug",
            task=(
                "Diagnose the failing behavior across this small Python project's modules, "
                "make the minimal implementation fix, verify the complete test suite, and "
                "finish. Do not modify tests."
            ),
            difficulty="medium",
            category="mutation",
            immutable_paths=(
                "tests/test_integration.py",
                "tests/test_service.py",
            ),
            allowed_changed_paths=("app/parser.py",),
        ),
        BenchmarkCase(
            case_id="regression_repair",
            title="Regression repair",
            fixture=fixtures / "regression_repair",
            task=(
                "Repair the input-normalization regression while preserving existing behavior, "
                "verify the full test suite, and finish. Do not modify tests or unrelated files."
            ),
            difficulty="medium",
            category="mutation",
            immutable_paths=(
                "tests/test_catalog.py",
                "tests/test_textnorm.py",
            ),
            allowed_changed_paths=("textnorm.py",),
        ),
        BenchmarkCase(
            case_id="failed_verification_recovery",
            title="Failed-verification recovery",
            fixture=fixtures / "failed_verification_recovery",
            task=(
                "Restore the documented boolean-configuration parsing contract, use authoritative "
                "verification to catch any incomplete repair, and finish with the full suite "
                "passing. Do not modify tests or the contract."
            ),
            difficulty="medium",
            category="recovery",
            immutable_paths=(
                "tests/test_01_common_aliases.py",
                "tests/test_02_extended_aliases.py",
                "tests/test_03_settings.py",
            ),
            allowed_changed_paths=("flags.py",),
        ),
    )
