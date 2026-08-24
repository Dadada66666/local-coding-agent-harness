# Verification Execution Metadata Specification

## 1. Document Metadata

| Field | Value |
|---|---|
| Title | Verification Execution Metadata Specification |
| Version | 1.0 |
| Status | **FROZEN FOR IMPLEMENTATION** |
| Repository HEAD reviewed | `27c8113cebf666fec1d57b02258938666a78893e` |
| Evidence run | `20260824-025843-db572004` |
| Review date | 2026-08-24 (Asia/Shanghai) |
| Production implementation in this revision | None |

This specification defines one narrow contract between `BashTool` and the existing
verification tracking hook. It exists because a successful shell launcher exit is not proof that
the work launched by that command completed or passed.

This document does not replace `docs/spec.md`, `docs/runtime-economics-spec.md`, or
`docs/mcp-client-spec.md`. It supersedes `RTE-TOOL-001` only to the exact extent required to add
the Bash `result_scope` input/metadata contract below. All other CMV3, Runtime Economics, MCP,
Plan, Permission, Sandbox, and lifecycle requirements remain authoritative.

## 2. Reviewed Repository Reality

### 2.1 Current Bash execution

`src/tools/bash.py` currently:

- accepts an arbitrary shell `command` plus optional `timeout`, `input`, `purpose`, and
  `exit_expectation`;
- invokes one platform shell synchronously through `subprocess.run()`;
- returns the invoked shell's return code, timeout, output, purpose, and environment/Sandbox
  metadata;
- does not own or monitor descendant processes after the invoked shell returns;
- does not distinguish a command result from a launcher/submission result.

### 2.2 Current ToolResult shape

`ToolResult` already contains an extensible `metadata: dict[str, Any]`. No `ToolResult` class,
serialization format, new result type, or persistence layer is required.

### 2.3 Current verification authority

`src/runtime/hooks/tracking.py::test_result_hook()` currently decides whether to overwrite
`context.last_test_result` and `context.task_test_result`. It treats either of these as a
verification candidate:

- an existing recognized test command; or
- a Bash call whose purpose is `verify`.

After existing mutation/discovery exclusions, the hook treats `ToolResult.ok` as the
authoritative verification outcome. It does not know whether that outcome describes the actual
check or only a launcher.

### 2.4 Confirmed failure

Run `20260824-025843-db572004` executed:

```text
nohup python3 -m http.server 8765 ... &
```

with `purpose="verify"`. The shell launcher returned zero, so tracking recorded `Verification:
passed`, while subsequent browser navigation failed with `ERR_CONNECTION_REFUSED`.

The result metadata contained `purpose=verify`, `returncode=0`, and ordinary Bash metadata. The
operation was only `process.exec/action=unknown`. No structured field identified the result as a
launcher/submission result.

## 3. Problem Statement

The Runtime must distinguish two meanings of a successful Bash ToolResult:

1. **command outcome** — the result is intended to represent the outcome of the command/check;
2. **launcher outcome** — the result only says that infrastructure, background work, or another
   longer-lived operation was launched/submitted.

`tracking.py` must not infer that distinction from shell syntax, program names, output text,
return codes, timeouts, or task wording.

## 4. Goals

- Make verification eligibility an explicit Bash execution-result contract.
- Prevent a launcher result from overwriting an existing authoritative verification result.
- Preserve foreground `pytest`, `node --check`, `ruff check`, syntax checks, and custom
  `purpose="verify"` checks.
- Fail closed when the required execution-result scope is missing or invalid.
- Reuse the current Bash execution, ToolResult metadata, post-tool hook, trace, and task
  verification state.

## 5. Non-Goals

This specification does not authorize:

- parsing shell command strings to find `&`, `nohup`, `Start-Process`, jobs, pipes, or daemon
  syntax;
- command keyword rules for detecting background/setup behavior;
- inspecting process trees, process groups, job tables, ports, PIDs, or child liveness;
- starting, retaining, stopping, restarting, or reconnecting background processes;
- a process manager, background-task framework, verification manager, ledger, or state machine;
- automatic correction of a caller's incorrect `result_scope` declaration;
- MCP, Sandbox, Provider, Context Manager, Plan, Permission, Artifact, or lifecycle changes.

## 6. Terminology

### 6.1 Verification candidate

A Bash result selected by the existing candidate rules: recognized test command or explicit
`purpose="verify"`. Candidate identification remains separate from result authority.

### 6.2 Result scope

The declared meaning of the Bash ToolResult:

- `command`: the ToolResult is the outcome of the command/check itself. A success, exit-status
  mismatch, or timeout may therefore be authoritative for that verification attempt.
- `launcher`: the ToolResult is only the outcome of launching/submitting work. It does not prove
  the launched work completed, remained available, or passed.

`result_scope` is a required, caller-declared execution-intent argument. It is a tool-contract
declaration, not an inference about arbitrary shell text and not a Runtime attestation that the
declared process topology, lifetime, or outcome is true.

### 6.3 Authoritative verification result

The existing `task_test_result`/`last_test_result` record installed by `test_result_hook()` after
all of these are true:

- the Bash ToolResult is not denied or blocked;
- the result is a verification candidate;
- `result.metadata.result_scope == "command"`;
- existing mutation and discovery exclusions permit recording.

## 7. Ownership and Trust Boundary

- **BashTool owns production of execution metadata.** It validates the model-visible
  `result_scope` argument and copies the normalized value into every Bash ToolResult it creates,
  including normal return and timeout results. This propagation preserves caller intent; it does
  not convert that intent into a Runtime trust proof.
- **ToolExecutor owns ordinary validation/error propagation.** Missing or invalid scope produces
  the existing `ToolValidationError` result before command execution.
- **Tracking owns task verification state.** It consumes `result_scope` only as a verification
  eligibility gate; it must not derive, rewrite, repair, or treat the declaration as proof that an
  execution actually had the declared behavior.
- **The caller owns the declaration.** Declaring launcher work as `command` is a tool-contract
  violation. Detecting that violation from arbitrary shell content is explicitly outside scope.

This separation is intentional: the Runtime guarantees deterministic handling of the declared
execution contract without claiming it can infer arbitrary descendant-process behavior.

`result_scope` is not security authority. It MUST NOT change operation classification,
PermissionGate decisions, Sandbox wrapping or policy, filesystem protections, or any other
execution safety boundary. The same Bash command remains subject to the same security path for
either scope value.

## 8. Bash Input Contract

Add exactly one required Bash input field:

```json
{
  "result_scope": {
    "type": "string",
    "enum": ["command", "launcher"],
    "description": "Use command when this ToolResult is the command/check outcome; use launcher when it only starts or submits longer-lived work. Launcher results never become authoritative verification."
  }
}
```

The Bash schema `required` list becomes:

```json
["command", "result_scope"]
```

There is no default. Missing scope is a validation error so an old or malformed call cannot
silently acquire verification authority.

- **VEM-DAT-001:** `result_scope` MUST be required for every Bash ToolCall.
- **VEM-DAT-002:** Its only valid values are `command` and `launcher`.
- **VEM-DAT-003:** `BashTool.validate()` MUST reject missing, non-string, or unknown values before
  executing the command.
- **VEM-DAT-004:** The schema and tool description MUST state that launcher success is not
  verification success.
- **VEM-DAT-005:** No default, compatibility alias, inference, or automatic repair is permitted.

## 9. Bash ToolResult Metadata Contract

Every ToolResult created directly by `BashTool.call()` MUST contain:

```json
{
  "result_scope": "command | launcher"
}
```

The value is the validated input value and is copied unchanged.

- A `command` result with `ok=true` may represent a passed verification.
- A `command` result with `ok=false`, including timeout or exit mismatch, may represent a failed
  verification.
- A `launcher` result is never authoritative, regardless of `ok`, return code, output, timeout,
  `purpose`, or `exit_expectation`.

- **VEM-DAT-006:** Normal Bash return and timeout ToolResults MUST retain the validated
  `result_scope`.
- **VEM-DAT-007:** ToolExecutor-generated validation, denial, or unexpected-exception results
  lacking valid scope are verification-ineligible.
- **VEM-DAT-008:** No second boolean such as `verification_authoritative` or
  `background_detected` may duplicate `result_scope`.
- **VEM-DAT-009:** `ToolResult` itself MUST NOT gain a new dataclass field; existing metadata is
  sufficient.
- **VEM-DAT-010:** `result_scope` MUST be treated as caller-declared execution intent, not as a
  Runtime proof of process topology, child-process survival, completion, or semantic correctness.

## 10. Verification Authority Contract

`test_result_hook()` keeps its current candidate, mutation, discovery, level, mutation-version,
trace, and state-update behavior, with one new fail-closed gate.

Deterministic pseudocode:

```python
if tool.name != "bash":
    return

if denied_or_blocked(result):
    return

candidate = existing_test_candidate(command) or purpose_is_verify(tool_call, result)
if not candidate:
    return

scope = result.metadata.get("result_scope")
if scope != "command":
    record_verification_ignored(
        reason=(
            "launcher_result"
            if scope == "launcher"
            else "missing_execution_result_scope"
        )
    )
    return

apply_existing_mutation_and_discovery_exclusions()
record_existing_authoritative_test_result()
```

- **VEM-AUT-001:** Tracking MUST require exact `result_scope == "command"` before updating
  verification state.
- **VEM-AUT-002:** `purpose="verify"` MUST NOT override the scope gate.
- **VEM-AUT-003:** `result_scope="launcher"` MUST leave `last_test_result`, `task_test_result`, and
  `task_verification_version` unchanged.
- **VEM-AUT-004:** An ignored launcher candidate MUST set `verification_ignored=true`, set
  `verification_ignored_reason="launcher_result"`, and emit the existing
  `verification_ignored` trace event.
- **VEM-AUT-005:** Missing scope MUST fail closed with reason
  `missing_execution_result_scope`; tracking MUST NOT infer a value.
- **VEM-AUT-006:** A failed `command`-scope verification remains authoritative failure; authority
  does not mean success.
- **VEM-AUT-007:** Existing foreground test recognition, explicit verification purpose,
  mutation exclusion, discovery exclusion, verification level, and mutation-version semantics
  remain unchanged after the scope gate.
- **VEM-AUT-008:** No shell string, program name, task text, output content, PID, port, or timing
  signal may determine `result_scope`.
- **VEM-AUT-009:** Tracking MUST use `result_scope` only to decide whether an otherwise existing
  verification candidate is eligible to update the existing verification state.
- **VEM-AUT-010:** `result_scope` MUST NOT alter PermissionGate, Sandbox, operation-risk,
  filesystem-safety, or other security decisions.
- **VEM-AUT-011:** `result_scope` MUST NOT alter timeout duration, timeout detection, process
  termination, ToolResult construction, or any existing timeout interpretation; it only gates
  verification eligibility after the existing Bash result is produced.

## 11. Data Flow

### 11.1 Before

```text
Model Bash ToolCall
    purpose=verify
        ↓
BashTool subprocess.run
        ↓
ToolResult
    ok / returncode / purpose
        ↓
test_result_hook
    command/purpose classification
        ↓
task_test_result overwritten
```

The hook has no structured information describing what the successful result means.

### 11.2 After

```text
Model Bash ToolCall
    command
    purpose
    result_scope=command|launcher
        ↓
BashTool.validate
    exact enum validation
        ↓
existing subprocess.run
        ↓
Bash ToolResult metadata
    result_scope copied unchanged
        ↓
existing POST_TOOL hooks
        ↓
test_result_hook
    candidate? then require scope=command
        ├─ launcher/missing → existing verification_ignored path
        └─ command → existing verification recording path
```

There is no additional queue, process owner, manager, retry, or lifecycle transition.

## 12. Foreground Verification Preservation

The following calls remain authoritative when they use `result_scope="command"`:

```json
{
  "command": "pytest",
  "purpose": "verify",
  "result_scope": "command"
}
```

```json
{
  "command": "node --check game/game.js",
  "purpose": "verify",
  "result_scope": "command"
}
```

```json
{
  "command": "ruff check .",
  "purpose": "verify",
  "result_scope": "command"
}
```

For command scope:

- zero/expected exit continues to record pass;
- unexpected exit continues to record failure;
- timeout continues to record failure;
- current verification level and mutation-version logic remain unchanged.

These statements preserve the existing timeout contract. `result_scope` does not add a timeout
mode or reinterpret whether a call timed out. A command-scope timeout follows the existing failed
verification path; a launcher-scope timeout remains an ordinary failed Bash result that is
ineligible to update verification state.

The incident launcher is represented as:

```json
{
  "command": "nohup python3 -m http.server 8765 ... &",
  "purpose": "verify",
  "result_scope": "launcher"
}
```

Its ToolResult may report launcher success, but it cannot modify authoritative verification.

## 13. Failure Semantics

- Missing/invalid `result_scope`: existing argument-validation failure; command is not executed;
  verification state is unchanged.
- `launcher` with `ok=true`: launcher success is returned to the model; verification state is
  unchanged.
- `launcher` with `ok=false` or timeout: ordinary failed Bash ToolResult; verification state is
  unchanged.
- `command` with `ok=true`: existing passed verification behavior.
- `command` with `ok=false` or timeout: existing failed verification behavior.
- Denied/blocked command: existing non-execution behavior; no verification update.
- Unexpected executor exception without scope metadata: fail closed; no verification update.

- **VEM-FAIL-001:** Launcher results MUST never erase, pass, fail, or stale an existing
  authoritative verification record.
- **VEM-FAIL-002:** Validation failure MUST occur before process execution.
- **VEM-FAIL-003:** The Runtime MUST return the ordinary launcher ToolResult to the model; it MUST
  NOT silently convert it into a verification pass or failure.
- **VEM-FAIL-004:** No retry, correction, process cleanup, or alternate execution path is added.

## 14. Required Production Changes

| File | Function/class | Exact change | Requirements |
|---|---|---|---|
| `src/tools/bash.py` | `BashTool.description`, `input_schema`, `validate()`, `call()`, `_metadata()` | Add required `result_scope`, validate exact enum, and copy it into normal/timeout ToolResult metadata | `VEM-DAT-001..010`, `VEM-FAIL-002` |
| `src/runtime/hooks/tracking.py` | `test_result_hook()` | Gate existing verification candidates on exact structured result scope and reuse the existing ignored-event path | `VEM-AUT-001..011`, `VEM-FAIL-001..004` |

No other production file is authorized.

In particular, `src/tools/base.py` is unchanged because `ToolResult.metadata` already carries the
contract.

## 15. Required Test Changes

All Bash ToolCall fixtures must provide explicit `result_scope`. This is a deliberate strict
contract migration, not compatibility work.

Existing fixture migration is mechanical: add only the `result_scope` value that describes the
fixture's existing intent. Existing commands, purposes, mocks, expected ToolResults, assertions,
Permission expectations, Plan behavior, Sandbox behavior, and test flow MUST NOT be rewritten.
Only the focused Bash and tracking tests may add new assertions required by this contract.

| File | Required coverage |
|---|---|
| `tests/unit/tools/test_bash.py` | schema required field; exact enum validation; normal, failed, and timeout metadata preservation; launcher metadata preservation |
| `tests/unit/runtime/hooks/test_tracking.py` | launcher verify result cannot overwrite prior verification; foreground pass/fail remains authoritative; missing scope fails closed; identical command text is governed only by structured scope |
| `tests/integration/test_plan_loop.py` | scripted verification Bash calls include command scope and preserve current Plan verification behavior |
| `tests/unit/runtime/security/test_permission_gate.py` | Bash fixtures include explicit scope; Permission behavior remains byte-for-byte equivalent otherwise |
| `tests/integration/test_interactive_session.py` | add explicit scope to Bash ToolCall fixtures; assertions remain unchanged |
| `tests/integration/test_security_boundary.py` | add explicit scope to Bash ToolCall fixtures; security assertions remain unchanged |
| `tests/unit/runtime/test_bootstrap.py` | add explicit scope to Bash ToolCall fixtures; hook/registry assertions remain unchanged |
| `tests/unit/tools/test_plan_tools.py` | add explicit scope to Bash ToolCall fixtures; Plan assertions remain unchanged |
| `tests/unit/runtime/plan/test_gate_store.py` | add explicit scope to Bash ToolCall fixtures; Plan gate assertions remain unchanged |
| `tests/unit/runtime/security/test_sandbox.py` | add explicit scope to Bash ToolCall fixtures; Sandbox assertions remain unchanged |

### 15.1 Deterministic tests

- **VEM-TST-001:** Bash schema requires `result_scope` and exposes exactly `command`/`launcher`.
- **VEM-TST-002:** Missing or invalid scope fails validation before `subprocess.run()`.
- **VEM-TST-003:** Successful and failed foreground `pytest` results with command scope retain
  existing authoritative behavior.
- **VEM-TST-004:** Successful and failed `node --check` results with command scope retain existing
  authoritative behavior.
- **VEM-TST-005:** A successful `purpose=verify`, launcher-scope server command does not overwrite
  an existing successful or failed verification record.
- **VEM-TST-006:** A launcher-scope result emits `verification_ignored` with reason
  `launcher_result`.
- **VEM-TST-007:** Missing scope on an injected/synthetic ToolResult fails closed and does not
  mutate verification state.
- **VEM-TST-008:** Use the same opaque command string twice with different structured scopes;
  tracking records command scope and ignores launcher scope. This proves authority does not come
  from command parsing.
- **VEM-TST-009:** Existing discovery/mutation exclusions still apply to command scope.
- **VEM-TST-010:** Existing Permission, Plan, Sandbox, MCP, report, and CMV3 tests pass unchanged
  except for adding required Bash fixture arguments.
- **VEM-TST-011:** Existing Bash fixtures outside the focused Bash/tracking contract tests change
  only by adding `result_scope`; their prior setup, execution, and assertions remain unchanged.

Tests MUST NOT inspect real background process survival, ports, job tables, Playwright behavior,
or model turn choices.

## 16. Acceptance Criteria

Implementation is complete only when:

1. every Bash ToolCall requires explicit `result_scope`;
2. BashTool is the sole producer of normalized result-scope metadata;
3. tracking never derives result scope from command text or process heuristics;
4. launcher results never alter task verification state;
5. explicit verify purpose cannot bypass launcher scope;
6. foreground pytest/node/ruff/custom checks retain pass and failure semantics;
7. missing/invalid scope fails closed before execution;
8. result scope remains caller-declared intent and is used only for verification eligibility, not
   as a Runtime trust or security decision;
9. timeout behavior and interpretation remain unchanged;
10. existing fixture migrations add only `result_scope` outside focused contract tests;
11. no ToolResult dataclass field, config, manager, state machine, parser, detector, or fallback is
   added;
12. no MCP, Sandbox, Provider, Context, Permission, Plan, Artifact, or lifecycle production file is
   modified;
13. targeted tests, full `pytest`, `ruff check .`, and `ruff format --check .` pass.

## 17. Explicit Non-Changes

- Bash continues to use the current platform shell and `subprocess.run()`.
- Timeout, stdin, environment, output, exit expectation, fail-fast, and Sandbox wrapping remain
  unchanged.
- `result_scope` does not alter PermissionGate, Sandbox, operation-risk, filesystem-safety, or
  timeout semantics.
- ToolExecutor and hook ordering remain unchanged.
- Existing command/test/discovery classifiers remain unchanged and do not infer result scope.
- `task_test_result`, `last_test_result`, and mutation-version structures remain unchanged.
- No persistent background process becomes owned by the Runtime.
- No guarantee is added that launcher work survives, becomes reachable, or finishes.
- MCP Runtime, MCP gateways, MCP transports, and MCP result mapping remain unchanged.
- Sandbox policy and network/process isolation remain unchanged.
- Report wording and Auto/Direct prompt corrections are separate patches outside this document.

## 18. Implementation Sequence

1. Add the strict Bash input/schema validation and metadata propagation.
2. Update Bash unit tests for the execution-result contract.
3. Add the fail-closed tracking scope gate.
4. Add authority-preservation tracking regressions.
5. Mechanically add explicit scope to existing Bash fixtures without changing expectations.
6. Run targeted Bash/tracking/Plan/Permission tests.
7. Run full `pytest`, `ruff check .`, and `ruff format --check .`.
8. Audit that production diff contains only the two authorized files and maps every change to a
   `VEM-*` requirement.

## 19. Requirement Index

| Family | Scope |
|---|---|
| `VEM-DAT-*` | Bash input and ToolResult metadata contract |
| `VEM-AUT-*` | verification authority consumption and state preservation |
| `VEM-FAIL-*` | fail-closed and launcher-result semantics |
| `VEM-TST-*` | deterministic regression and quality gates |

## 20. Self Review

1. Verification eligibility is not inferred from `&`, `nohup`, program names, or output.
2. Tracking consumes one exact structured field and does not own shell semantics.
3. Bash execution and ToolResult infrastructure are reused without a new abstraction.
4. Foreground test/check pass, failure, and timeout semantics remain authoritative.
5. Launcher success/failure cannot overwrite an existing verification record.
6. Missing metadata fails closed.
7. There is no process manager, background framework, retry, compatibility default, or fallback.
8. MCP, Sandbox, Provider, Context, Plan, Permission, and lifecycle behavior are unchanged.
9. Result scope is caller-declared intent, not Runtime-attested execution truth, and tracking uses
   it only as an eligibility gate.
10. Timeout behavior is preserved without a new interpretation or policy.
11. Existing fixtures require only a mechanical `result_scope` addition; their test logic remains
   unchanged.
12. All implementation choices and test expectations are deterministic; no implementation choice
   remains open.
