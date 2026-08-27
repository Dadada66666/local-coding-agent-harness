# Agent Evaluation Benchmark

- Benchmark version: `1.0`
- Commit: `4839abfc2d23bef78abd45eb37b9bc9113f71696`
- Model: `gpt-5.6-terra`
- Generated: `2026-08-27T05:03:29.953311+00:00`
- Python: `3.12.3`
- Platform: `Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39`

## Summary

- End-to-end pass rate: **5/6**
- Task correctness: **6/6**
- Runtime/oracle agreement: **5/6**
- Unauthorized mutations: **0**
- Total model calls: **36**
- Total input tokens: **241,515**
- Total cache-read tokens: **160,000**

## Cases

| Case | E2E | Oracle | Runtime | Calls | Input | Cache | Tool Failures | Repairs | Verification |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| fix_bug | PASS | PASS | PASS | 5 | 31,038 | 21,504 | 0 | 0 | passed |
| validation_only | FAIL | PASS | FAIL | 5 | 32,092 | 16,896 | 1 | 1 | failed |
| required_plan | PASS | PASS | PASS | 9 | 63,554 | 41,728 | 0 | 0 | passed |
| cross_module_bug | PASS | PASS | PASS | 6 | 40,631 | 33,792 | 1 | 0 | passed |
| regression_repair | PASS | PASS | PASS | 5 | 32,513 | 20,480 | 0 | 0 | passed |
| failed_verification_recovery | PASS | PASS | PASS | 6 | 41,687 | 25,600 | 0 | 0 | passed |

## Case details

### Deterministic bug fix (`fix_bug`)

- Difficulty: `smoke`, Category: `mutation`, Result: **PASS**
- Changed paths: calculator.py

### Validation only (`validation_only`)

- Difficulty: `smoke`, Category: `validation`, Result: **FAIL**
- Failure category: `runtime_failure`
- Runtime error: **Summary**

Inspection found a small, coherent Python repository with `unittest`-style tests for `normalize_label` and `build_record`. No code or test defects were identified from the source review.

Authoritative verification could not run. `python -m pytest -q` exited `127` because the `python` executable was not permitted to execute:

```text
/bin/sh: 1: python: Permission denied
```

This is an environment/executable-permission failure, not a code or test failure. Follow-up runtime diagnostics were also blocked by the execution environment with `Permission denied: operation is not pre-authorized`.

**Changed files**

None. This was validation-only.

**Checks run**

- Source and test inspection:
  - `records.py`
  - `strings.py`
  - `test_records.py`
  - `test_strings.py`
- Authoritative command: `python -m pytest -q`
  - Result: could not execute `python`; exited `127`.

**Risks**

The repository’s test suite remains unverified in this environment. Restore execute permission/acces

### Required Plan bug fix (`required_plan`)

- Difficulty: `smoke`, Category: `planning`, Result: **PASS**
- Changed paths: pricing.py

### Cross-module bug (`cross_module_bug`)

- Difficulty: `medium`, Category: `mutation`, Result: **PASS**
- Changed paths: app/parser.py

### Regression repair (`regression_repair`)

- Difficulty: `medium`, Category: `mutation`, Result: **PASS**
- Changed paths: textnorm.py

### Failed-verification recovery (`failed_verification_recovery`)

- Difficulty: `medium`, Category: `recovery`, Result: **PASS**
- Changed paths: flags.py
