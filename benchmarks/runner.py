from __future__ import annotations

import builtins
import json
import platform
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from agent.factory import build_agent_runner
from benchmarks.cases import BenchmarkCase, resume_cases
from benchmarks.evaluator import evaluate_case, snapshot_workspace
from runtime.config import RunConfig
from runtime.plan import ExecutionPath, PlanApprovalPolicy, PlanPhase
from runtime.security import PermissionMode


RESULTS_DIR = Path(__file__).resolve().parent / "results"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_VERSION = "1.0"


def main() -> int:
    results = []
    for case in resume_cases():
        print(f"[benchmark] {case.case_id}: {case.title}")
        result = run_case(case)
        results.append(result)
        print(f"[benchmark] {case.case_id}: {'PASS' if result['pass'] else 'FAIL'}")

    payload = build_payload(results)
    write_results(payload)
    return 0 if all(result["pass"] for result in results) else 1


def run_case(case: BenchmarkCase) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix=f"lcah-resume-{case.case_id}-") as temp_dir:
        workspace = Path(temp_dir) / "workspace"
        shutil.copytree(
            case.fixture,
            workspace,
            ignore=shutil.ignore_patterns(".pytest_cache", "__pycache__", "*.pyc"),
        )
        before = snapshot_workspace(workspace)

        runner = None
        context = None
        execution_error = None
        model = None
        try:
            with reject_interactive_input():
                config = RunConfig(
                    permission_mode=PermissionMode.ACCEPT_EDITS,
                    permission_prompt_policy="deny",
                    plan_policy=case.plan_policy,
                    plan_approval_policy=PlanApprovalPolicy.AUTO,
                )
                runner = build_agent_runner(
                    repo_path=workspace,
                    permission_mode=PermissionMode.ACCEPT_EDITS,
                    config=config,
                )
                model = getattr(runner.model_client, "model", None)
                context = runner.start_interactive("Resume benchmark session")
                runner.start_task(context, case.task)
        except Exception as exc:  # benchmark boundary: preserve failure as evidence
            execution_error = f"{exc.__class__.__name__}: {exc}"
        finally:
            if runner is not None and context is not None:
                runner.finish(context)

        after = snapshot_workspace(workspace)
        oracle = evaluate_case(case, workspace, before, after)
        return case_result(case, context, oracle, execution_error, model=model)


def case_result(
    case,
    context,
    oracle,
    execution_error: str | None,
    *,
    model: str | None,
) -> dict[str, Any]:
    runtime_success = bool(context is not None and context.success)
    plan_state = getattr(context, "plan_state", None)
    verification = getattr(context, "task_test_result", None) if context is not None else None
    cost = context.cost_tracker.delta(context.task_cost_start) if context is not None else {}
    case_invariants_passed = True
    if case.plan_policy == "required":
        case_invariants_passed = bool(
            plan_state is not None
            and plan_state.execution_path is ExecutionPath.PLAN
            and plan_state.phase is PlanPhase.COMPLETED
            and plan_state.approved_version == plan_state.version
            and plan_state.approval_source == "auto_policy"
        )
    task_correct = bool(oracle.passed)
    end_to_end_pass = bool(
        task_correct and runtime_success and execution_error is None and case_invariants_passed
    )
    failure_category = classify_failure(
        runtime_success=runtime_success,
        task_correct=task_correct,
        execution_error=execution_error,
        unauthorized_mutations=oracle.unauthorized_mutations,
        case_invariants_passed=case_invariants_passed,
    )
    return {
        "case": case.case_id,
        "title": case.title,
        "difficulty": case.difficulty,
        "category": case.category,
        "pass": end_to_end_pass,
        "end_to_end_pass": end_to_end_pass,
        "task_correct": task_correct,
        "oracle_pass": task_correct,
        "runtime_success": runtime_success,
        "runtime_status": getattr(getattr(context, "task_status", None), "value", None),
        "runtime_error": (
            execution_error
            or (
                getattr(context, "final_text", "")[:1000]
                if context is not None and not runtime_success
                else None
            )
        ),
        "case_invariants_passed": case_invariants_passed,
        "runtime_oracle_agreement": runtime_success == task_correct,
        "failure_category": failure_category,
        "model": model,
        "model_calls": int(getattr(context, "task_model_calls", 0)),
        "input_tokens": int(cost.get("input_tokens", 0)),
        "output_tokens": int(cost.get("output_tokens", 0)),
        "cache_read_input_tokens": int(cost.get("cache_read_input_tokens", 0)),
        "tool_failures": len(getattr(context, "task_tool_failures", [])),
        "repair_attempts": int(getattr(context, "repair_attempts", 0)),
        "verification_status": (
            "passed"
            if verification and verification.get("ok")
            else "failed"
            if verification
            else "not_recorded"
        ),
        "changed_file_count": len(getattr(context, "task_changed_files", set())),
        "oracle_changed_paths": list(oracle.changed_paths),
        "unauthorized_mutations": list(oracle.unauthorized_mutations),
        "execution_path": getattr(getattr(plan_state, "execution_path", None), "value", None),
        "plan_phase": getattr(getattr(plan_state, "phase", None), "value", None),
        "approved_version": getattr(plan_state, "approved_version", None),
        "plan_version": getattr(plan_state, "version", None),
        "approval_source": getattr(plan_state, "approval_source", None),
        "external_pytest_passed": oracle.pytest_passed,
        "external_pytest_returncode": oracle.returncode,
        "external_pytest_output": oracle.output,
    }


def build_payload(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    return {
        "benchmark": "resume",
        "benchmark_version": BENCHMARK_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": resolve_git_sha(),
        "model": next((result["model"] for result in results if result["model"]), None),
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "cases": results,
        "summary": {
            "end_to_end_pass": f"{sum(result['end_to_end_pass'] for result in results)}/{total}",
            "task_correct": f"{sum(result['task_correct'] for result in results)}/{total}",
            "runtime_oracle_agreement": (
                f"{sum(result['runtime_oracle_agreement'] for result in results)}/{total}"
            ),
            "unauthorized_mutations": sum(
                len(result["unauthorized_mutations"]) for result in results
            ),
            "total_model_calls": sum(result["model_calls"] for result in results),
            "total_input_tokens": sum(result["input_tokens"] for result in results),
            "total_cache_read_input_tokens": sum(
                result["cache_read_input_tokens"] for result in results
            ),
        },
    }


def classify_failure(
    *,
    runtime_success: bool,
    task_correct: bool,
    execution_error: str | None,
    unauthorized_mutations: tuple[str, ...],
    case_invariants_passed: bool,
) -> str:
    if (
        runtime_success
        and task_correct
        and execution_error is None
        and not unauthorized_mutations
        and case_invariants_passed
    ):
        return "passed"
    if execution_error is not None:
        return "execution_error"
    if unauthorized_mutations:
        return "unauthorized_mutation"
    if not runtime_success:
        return "runtime_failure"
    if not case_invariants_passed:
        return "plan_contract_failure"
    return "oracle_failure"


def resolve_git_sha() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.strip()
    return value if completed.returncode == 0 and value else None


def write_results(payload: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "resume.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (RESULTS_DIR / "resume.md").write_text(render_markdown(payload), encoding="utf-8")


def render_markdown(payload: dict[str, Any]) -> str:
    rows = [
        "| Case | E2E | Oracle | Runtime | Calls | Input | Cache | Tool Failures | Repairs | Verification |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in payload["cases"]:
        rows.append(
            "| {case} | {e2e} | {oracle} | {runtime} | {calls} | {input_tokens:,} | "
            "{cache:,} | {failures} | {repairs} | {verification} |".format(
                case=result["case"],
                e2e="PASS" if result["end_to_end_pass"] else "FAIL",
                oracle="PASS" if result["task_correct"] else "FAIL",
                runtime="PASS" if result["runtime_success"] else "FAIL",
                calls=result["model_calls"],
                input_tokens=result["input_tokens"],
                cache=result["cache_read_input_tokens"],
                failures=result["tool_failures"],
                repairs=result["repair_attempts"],
                verification=result["verification_status"],
            )
        )
    summary = payload["summary"]
    lines = [
        "# Agent Evaluation Benchmark",
        "",
        f"- Benchmark version: `{payload['benchmark_version']}`",
        f"- Commit: `{payload['git_sha'] or 'unknown'}`",
        f"- Model: `{payload['model'] or 'unknown'}`",
        f"- Generated: `{payload['generated_at']}`",
        f"- Python: `{payload['python_version']}`",
        f"- Platform: `{payload['platform']}`",
        "",
        "## Summary",
        "",
        f"- End-to-end pass rate: **{summary['end_to_end_pass']}**",
        f"- Task correctness: **{summary['task_correct']}**",
        f"- Runtime/oracle agreement: **{summary['runtime_oracle_agreement']}**",
        f"- Unauthorized mutations: **{summary['unauthorized_mutations']}**",
        f"- Total model calls: **{summary['total_model_calls']:,}**",
        f"- Total input tokens: **{summary['total_input_tokens']:,}**",
        f"- Total cache-read tokens: **{summary['total_cache_read_input_tokens']:,}**",
        "",
        "## Cases",
        "",
        *rows,
        "",
        "## Case details",
        "",
    ]
    for result in payload["cases"]:
        lines.extend(
            [
                f"### {result['title']} (`{result['case']}`)",
                "",
                (
                    f"- Difficulty: `{result['difficulty']}`, Category: "
                    f"`{result['category']}`, Result: "
                    f"**{'PASS' if result['end_to_end_pass'] else 'FAIL'}**"
                ),
            ]
        )
        if result["end_to_end_pass"]:
            changed = ", ".join(result["oracle_changed_paths"]) or "none"
            lines.extend([f"- Changed paths: {changed}", ""])
            continue
        lines.append(f"- Failure category: `{result['failure_category']}`")
        if result["runtime_error"]:
            lines.append(f"- Runtime error: {result['runtime_error']}")
        if not result["task_correct"]:
            lines.append(
                f"- External test: {_external_test_summary(result['external_pytest_output'])}"
            )
        if result["unauthorized_mutations"]:
            lines.append("- Unauthorized mutations: " + ", ".join(result["unauthorized_mutations"]))
        lines.append("")
    return "\n".join(lines)


def _external_test_summary(output: str) -> str:
    lines = [line.strip() for line in str(output).splitlines() if line.strip()]
    return (lines[-1] if lines else "no external test output")[:300]


@contextmanager
def reject_interactive_input() -> Iterator[None]:
    original = builtins.input

    def fail_input(*_args, **_kwargs):
        raise AssertionError("benchmark attempted to read interactive input")

    builtins.input = fail_input
    try:
        yield
    finally:
        builtins.input = original


if __name__ == "__main__":
    raise SystemExit(main())
