from __future__ import annotations

import builtins
import json
import shutil
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
        context = None
        execution_error = None
        try:
            with reject_interactive_input():
                context = runner.start_interactive("Resume benchmark session")
                runner.start_task(context, case.task)
        except Exception as exc:  # benchmark boundary: preserve failure as evidence
            execution_error = f"{exc.__class__.__name__}: {exc}"
        finally:
            if context is not None:
                runner.finish(context)

        after = snapshot_workspace(workspace)
        oracle = evaluate_case(case, workspace, before, after)
        return case_result(case, context, oracle, execution_error)


def case_result(case, context, oracle, execution_error: str | None) -> dict[str, Any]:
    runtime_success = bool(context is not None and context.success)
    plan_state = getattr(context, "plan_state", None)
    verification = getattr(context, "task_test_result", None) if context is not None else None
    cost = context.cost_tracker.delta(context.task_cost_start) if context is not None else {}
    plan_contract_passed = True
    if case.plan_policy == "required":
        plan_contract_passed = bool(
            plan_state is not None
            and plan_state.execution_path is ExecutionPath.PLAN
            and plan_state.phase is PlanPhase.COMPLETED
            and plan_state.approved_version == plan_state.version
            and plan_state.approval_source == "auto_policy"
        )
    oracle_pass = bool(oracle.passed and plan_contract_passed)
    return {
        "case": case.case_id,
        "title": case.title,
        "pass": oracle_pass and execution_error is None,
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
        "oracle_pass": oracle_pass,
        "runtime_oracle_agreement": runtime_success == oracle_pass,
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
    return {
        "benchmark": "resume",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": results,
        "summary": {
            "correctness": f"{sum(result['pass'] for result in results)}/{len(results)}",
            "runtime_oracle_agreement": (
                f"{sum(result['runtime_oracle_agreement'] for result in results)}/{len(results)}"
            ),
            "unauthorized_mutations": sum(
                len(result["unauthorized_mutations"]) for result in results
            ),
        },
    }


def write_results(payload: dict[str, Any]) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "resume.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (RESULTS_DIR / "resume.md").write_text(render_markdown(payload), encoding="utf-8")


def render_markdown(payload: dict[str, Any]) -> str:
    rows = [
        "| Case | Pass | Runtime Success | Calls | Input Tokens | Cache Read | Tool Failures | Repairs | Verification |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for result in payload["cases"]:
        rows.append(
            "| {case} | {passed} | {runtime} | {calls} | {input_tokens} | {cache} | "
            "{failures} | {repairs} | {verification} |".format(
                case=result["case"],
                passed="PASS" if result["pass"] else "FAIL",
                runtime="yes" if result["runtime_success"] else "no",
                calls=result["model_calls"],
                input_tokens=result["input_tokens"],
                cache=result["cache_read_input_tokens"],
                failures=result["tool_failures"],
                repairs=result["repair_attempts"],
                verification=result["verification_status"],
            )
        )
    summary = payload["summary"]
    return "\n".join(
        [
            "# Resume Benchmark",
            "",
            *rows,
            "",
            f"correctness: {summary['correctness']}",
            f"runtime/oracle agreement: {summary['runtime_oracle_agreement']}",
            f"unauthorized mutations: {summary['unauthorized_mutations']}",
            "",
        ]
    )


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
