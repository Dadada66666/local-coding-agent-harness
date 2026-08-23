# Runtime Lifecycle & Provider Accounting Specification

## 1. Document Metadata

- **Version:** 1.1
- **Status:** FROZEN FOR IMPLEMENTATION
- **Repository main HEAD:** `2ae6c96eb29cd349e751a433f1d129f8cb27a81d`
- **Implementation tree baseline:** `94cd8b382725b7fa96141e4bb6391ed37d2e639c`
- **Implementation tree:** `ccaef4f6f6ecab489080805d00b74d229b8d9fef`
- **Tree difference:** none; the repository main merge commit and implementation baseline have
  the same tree.
- **Evidence run:** `20260821-155814-364019ed`
- **Scope:** provider usage reporting, stable-phase prompt determinism, verification
  classification, and final-response truthfulness
- **Production implementation in this revision:** none

This specification is evidence-driven. It authorizes only the deterministic corrections
defined by the requirement IDs below. It does not authorize adjacent Runtime optimization.

## 2. Relationship to CMV3

`docs/spec.md` remains authoritative for Context Manager V3. This specification does not
supersede CMV3.

The following CMV3 behavior remains frozen and is outside this specification:

- admission-first ToolResult shaping and the 12K aggregate bound;
- append-only Context Epochs and `context_generation` semantics;
- provider-pressure normalization in `runtime/context`;
- Full Rebase, semantic handoff, emergency rebase, recent raw retention, and recovery;
- Artifact, Source, and History recovery architecture;
- all CMV3 context constants and configuration.

If an implementation of this specification would require changing those behaviors, the
implementation MUST stop and report `SPEC DEVIATION REQUIRED`.

- **RTE-SCOPE-001:** Every production change MUST map to a requirement in this document.
- **RTE-SCOPE-002:** No production file under `src/runtime/context/` may change under this
  specification.
- **RTE-SCOPE-003:** No new production configuration field, compatibility mode, provider-name
  branch, retry state machine, or lifecycle state may be introduced.

## 3. Evidence Baseline

### 3.1 Run totals

| Field | Observed value |
|---|---:|
| Provider calls | 29 |
| Raw `input_tokens` | 1,646,135 |
| Raw `cache_read_input_tokens` | 1,435,888 |
| Raw `cache_creation_input_tokens` | 0 |
| Reported `logical_input_tokens` | 3,082,023 |
| Output tokens | 15,875 |
| Full Rebase events | 0 |
| ToolResult admission projection events | 2 |
| Source observations projected | 2 |
| Source rehydration reads / lines | 4 / 754 |
| Non-rehydration overlap lines | 460 |
| Task result | completed |

### 3.2 Evidence table

| Finding | Evidence | Root cause | Impact | Existing Spec relation | Severity | Change needed? |
|---|---|---|---|---|---|---|
| A failed whitespace verification was omitted and later described as passed | `trace.jsonl` turns 21 and 26 returned `command exited 3` for `git diff --no-index --check`; both emitted `verification_ignored`; `report.md` Summary claimed the whitespace check passed | `src/runtime/hooks/tracking.py` classifies every command beginning with `git diff` as discovery; `src/agent/prompts.py` cannot render the existing `task_test_result` into the Completed request | A failed validation can be absent from authoritative verification state, and the final model lacks the compact Runtime fact needed to report it truthfully | CMV3 preserves verification state but does not define Bash verification classification or Completed reporting | P0 correctness | Yes |
| `logical_input_tokens` double-counts this Provider's cached subset and drives unsupported input-category allocation | `cost.json` stable-phase turns show each `cache_read_input_tokens` closely matching prior total input while `input_tokens - cache_read_input_tokens` matches the appended tail; `report.md` inflates the run by 1,435,888 tokens (87.23% over raw input) | `src/agent/messages.py` and `src/runtime/observability/cost_tracker.py` always compute `input + cache_creation + cache_read`; the tracker then allocates that ambiguous total across locally estimated input categories | Reports, category allocations, and benchmark comparisons are misleading; Context pressure is unaffected | CMV3 requires raw usage preservation and forbids unconditional cache addition as Context truth | P2 observability | Yes |
| Stable Executing prompts change when the call budget crosses five remaining calls | `src/agent/prompts.py` appends a warning whenever `call_budget.approaching_limit` becomes true; `src/agent/loop.py` rebuilds the system prompt on every request | A dynamic budget threshold is encoded in the otherwise phase-stable system prompt | The system hash changes, invalidating a large reusable prefix without changing phase or capabilities | CMV3 requires deterministic prompt construction within a stable lifecycle phase | P1 economics/reliability | Yes |
| Planning to Executing breaks the static provider prefix | `cost.json` turns 7 to 8 change system hash, tool hash, and tool count 10 to 14; turn 8 has zero cache read | `src/agent/prompts.py:58-121` changes phase instructions and `src/runtime/plan/capabilities.py:61-96` hides mutation tools until execution | 54,631 uncached tokens, 25.98% of run uncached input | CMV3 explicitly permits required plan-phase capability changes | Intentional lifecycle tradeoff | No |
| Executing to Completed breaks most of the static provider prefix | `cost.json` turns 28 to 29 change system hash, tool hash, and tool count 14 to 0; final input is 79,827 with only 4,379 cache-read tokens | `src/agent/prompts.py:113-121`, `src/runtime/plan/capabilities.py:74-96`, and `src/agent/loop.py:212-271` build a dedicated zero-tool Completed request | 75,448 uncached tokens, 35.89% of run uncached input and 4.58% of all raw provider input | CMV3 permits required plan-phase system/tool changes | P1 economics, safety of alternative unproven | No in v1.1 |
| Provider pressure anchor remained operationally accurate | `trace.jsonl` contains 29 `context_measurement` and matching `model_call_end` events; across 28 anchored calls, median relative error was 0.32%, p90 7.33%, maximum 17.67%; no pressure or overflow occurred | `src/runtime/context/manager.py:646-697` applies the frozen CMV3 base interpretation normalization | No demonstrated pressure-timing or safety defect | Governed by CMV3 | No defect | No |
| Source overlap includes both recovery and model-selected overlap | `report.md` and `trace.jsonl` show 1,214 duplicate lines comprising 754 rehydrated and 460 non-rehydration lines; two Source observations were projected | `src/runtime/context/projection.py:35-197` enforced the 12K first-visibility bound; `src/tools/read_file.py:108-210` classified recovery and overlap | No demonstrated Runtime projection/reread loop | Governed by CMV3 admission and Source recovery | No defect | No |
| ToolCall compatibility fallback | The run contained isolated out-of-range, malformed, stale-text, quoting, and exit-status mistakes, but every error was explicit and the model recovered | Model-correctable input errors, not a repeated Runtime contract failure | A fallback would add ambiguity without proven reliability benefit | Outside CMV3 | Rejected | No |

### 3.3 Provider anchor measurements

The local estimator and Provider anchor are different mechanisms and MUST remain reported as
such:

| Error statistic | Local estimator | Provider anchor |
|---|---:|---:|
| Median relative error | 29.24% | 0.32% |
| P90 relative error | 67.54% | 7.33% |
| Maximum relative error | 75.09% | 17.67% |
| Median absolute error | 17,950 | 175.5 |
| Maximum absolute error | 22,599 | 3,662 |

These data do not justify a CMV3 pressure-policy change. The Provider anchor remained far more
accurate than the local estimator, and the run never approached pressure.

## 4. Confirmed Problems

### 4.1 Verification classification defect

`git diff` used for inspection is discovery. `git diff --check` is validation. The current
prefix-only classifier conflates them. A failed `purpose=verify` invocation is therefore removed
from the same authoritative path that drives recovery, task success, and the Test Result section.

### 4.2 Provider usage reporting defect

The Provider adapter preserves raw fields but does not declare whether cache fields are inclusive
or exclusive. The observability layer nevertheless constructs an unconditional additive total
and calls it logical input. That number is not provider-independent and MUST NOT be presented as
task input truth.

The run is consistent with inclusive Terra-style accounting:

- turn 1 input: 5,336;
- turn 2 input/cache-read: 5,445 / 5,333;
- turn 3 input/cache-read: 5,635 / 5,442;
- stable Executing turns repeat the same pattern.

This is sufficient to reject unconditional addition for this run. It is not permission to encode
`gpt-5.6-terra`, a model ID, or a base URL in production logic.

The same ambiguity makes proportional allocation of raw Provider input/cache fields across local
message categories unsupported. Category diagnostics have a valid local estimate, but no
adapter-normalized Provider total that can be allocated as category truth.

### 4.3 Stable-phase system prompt mutation

`build_system_prompt()` appends a call-budget warning when remaining calls cross from six to five.
No Plan phase or capability changes at that boundary, yet the system hash changes. This is a
confirmed cache-economics defect, not a Context Manager defect. Version 1.1 removes that dynamic
system section and requires no replacement warning mechanism.

## 5. Rejected or Unproven Problems

The following MUST NOT become production requirements in v1.1:

1. **Planning to Executing cache preservation.** The capability change is intentional and
   security-relevant.
2. **Executing to Final append-only conversion.** The economic cost is confirmed, but preserving
   Executing tool schemas in Completed would advertise unavailable capabilities and has not been
   shown reliable. No lifecycle change is approved.
3. **Provider cache failure based on ratios.** Stable-phase absolute cache-read tokens show prefix
   reuse. A falling ratio caused by a new tail is not a cache break.
4. **Context estimator correctness defect.** The local estimate is low, but the Provider anchor
   compensated in this run; no pressure failure occurred.
5. **Source admission thrashing.** Rehydration was bounded recovery after first-visibility shaping,
   not historical microcompaction.
6. **Browser verification state expansion.** Missing browser binaries are an environment
   limitation, not evidence for a new Plan state.
7. **Tool argument auto-repair.** Existing validation feedback was actionable; silent correction,
   fuzzy edits, or compatibility fallbacks are forbidden.

## 6. Goals

1. Make failed validation enter the existing authoritative verification path.
2. Prevent model-authored final text from overriding authoritative verification facts.
3. Preserve raw Provider usage while removing the unsupported additive total.
4. Keep the system prompt deterministic inside a stable Plan phase.
5. Give the Completed model the existing current authoritative verification fact.
6. Keep Context pressure, capability isolation, and CMV3 behavior unchanged.
7. Reduce production semantics and misleading fields rather than introduce a usage framework.

## 7. Non-Goals

- changing Context thresholds, retention, admission, recovery, or Full Rebase;
- eliminating lifecycle cache breaks;
- calculating Provider billing cost;
- inferring a universal cache convention;
- adding a Provider economics engine or usage strategy abstraction;
- adding verification Plan states or a verification scheduler;
- parsing arbitrary shell syntax;
- repairing malformed model ToolCalls;
- changing Artifact or Source recovery.

## 8. Terminology

| Term | Definition |
|---|---|
| Raw provider input | The unchanged `usage.input_tokens` value returned by the adapter. |
| Raw cache read | The unchanged `usage.cache_read_input_tokens` value. |
| Raw cache creation | The unchanged `usage.cache_creation_input_tokens` value. |
| Additive input total | `input + cache_read + cache_creation`; forbidden as a provider-independent total. |
| Authoritative verification | The current `task_test_result` produced by `test_result_hook`; absence means unavailable. |
| Discovery | A read-only environment/repository inspection that does not establish code correctness. |
| Verification claim | A statement that a named check, command, behavior, or environment validation passed. |
| Lifecycle prefix break | A request whose system or tool-schema prefix differs because the Plan phase changed. |

## 9. Provider Usage Semantics

- **RTE-PROV-001:** `ModelClient` MUST continue preserving every raw usage field unchanged.
- **RTE-PROV-002:** Raw `input_tokens`, cache-read tokens, and cache-creation tokens MUST remain
  separately observable.
- **RTE-PROV-003:** Production code MUST NOT infer cache convention from model ID, host name, base
  URL, or a Provider-name branch.
- **RTE-PROV-004:** In the absence of an explicit adapter-normalized billing contract, Runtime MUST
  NOT invent a provider-independent billing-token total.
- **RTE-PROV-005:** No change may be made to CMV3 provider anchor normalization under this spec.

## 10. Cache Accounting Contract

- **RTE-CACHE-001:** Human-facing cost summaries MUST report raw `input_tokens`, raw cache-read,
  raw cache-creation, and output tokens separately.
- **RTE-CACHE-002:** `logical_input_tokens` and `logical_total_tokens` MUST be removed from current
  production summaries and JSON output because their unconditional additive definition is not
  provider-independent.
- **RTE-CACHE-003:** Input category breakdown is a local-estimate diagnostic only. Each input
  category MUST report only `chars`, `estimated_tokens`, and `estimated_share`, where the share is
  derived solely from local category estimates.
- **RTE-CACHE-004:** A cache hit or break MUST be diagnosed using system hash, tools hash,
  previous-message-prefix preservation, context generation, lifecycle phase, and absolute raw
  cache-read tokens. Ratio alone is insufficient.
- **RTE-CACHE-005:** Existing deterministic prefix fingerprints MUST remain unchanged.
- **RTE-CACHE-006:** Raw `input_tokens`, cache-read tokens, and cache-creation tokens MUST NOT be
  proportionally allocated to input categories. Version 1.1 adds no adapter-normalized allocation
  contract. Output-token breakdown remains independent and unchanged.

This contract deliberately removes an unsupported aggregate instead of replacing it with another
ambiguous metric.

## 11. Pressure Accounting Boundary

- **RTE-PROV-006:** Observability changes MUST NOT feed raw or derived cost totals back into
  `ContextManager.measure_context()`.
- **RTE-PROV-007:** Context pressure remains the CMV3 conservative maximum of local input and the
  normalized Provider anchor.
- **RTE-PROV-008:** Saved-token arithmetic remains local input before minus local input after.
- **RTE-PROV-009:** Tests MUST demonstrate that removal of observability-only logical totals does
  not change a Context measurement, trigger, or hard limit.

## 12. Lifecycle Prefix Contract

- **RTE-LIFE-001:** Within a stable Plan phase, system hash, tool hash, tool ordering, schema
  serialization, and prior messages MUST remain deterministic as already required by CMV3.
- **RTE-LIFE-002:** Required capability changes between Planning and Executing remain intentional
  cache breaks.
- **RTE-LIFE-003:** Version 1.1 does not authorize a change to Completed/final-response system or
  tool capability selection.
- **RTE-LIFE-009:** Crossing the call-budget warning threshold MUST NOT change the system prompt
  inside an otherwise stable Plan phase. The dynamic system-prompt warning MUST be removed. No
  replacement warning mechanism is required or authorized in version 1.1.

## 13. Planning to Executing Transition

Planning exposes ten read-only/control tools in the evidence run. Executing exposes fourteen tools,
including mutation and Bash capabilities after approval. Both system and tool hashes change.

- **RTE-LIFE-004:** Implementation MUST preserve the current read-only Planning boundary and
  approved Executing capability boundary.
- **RTE-LIFE-005:** No cache optimization may expose mutation tools before approval.

## 14. Executing to Final Transition

The evidence run proves a material cache cost, but it does not prove that retaining Executing
schemas during final generation is reliable. The current zero-tool Completed phase is therefore
an intentional tradeoff for this version.

- **RTE-LIFE-006:** No implementation change is authorized for final-response capability
  selection in v1.1.
- **RTE-LIFE-007:** A future revision may reconsider this only after an integration test proves
  that prior provider-visible input remains an exact prefix, unavailable tools cannot execute,
  final-response ToolCalls are bounded, and final answer success does not regress.

## 15. Runtime Tail Notice Rules

This version does not add a lifecycle tail notice.

- **RTE-LIFE-008:** Implementations MUST NOT introduce a finalization message, preserve hidden
  schemas, or alter AgentLoop final transitions under this specification.

## 16. Verification Fact Model

The existing `task_test_result`, mutation version, RecoveryPolicy, trace event, and report section
remain the fact path. No new verification state machine is required.

- **RTE-VER-001:** A Bash command with `purpose=verify` whose effective validation command is
  `git diff --check` or `git diff --no-index --check` MUST be recorded as verification, not ignored
  as discovery.
- **RTE-VER-002:** Plain `git diff`, `git status`, `command -v`, `which`, `find`, and equivalent
  read-only inspection remain discovery and MUST NOT overwrite a real verification result.
- **RTE-VER-003:** The existing single leading `cd <path> &&` normalization is the only wrapper
  normalization authorized. This specification does not authorize a general shell parser.
- **RTE-VER-004:** A failed tracked verification MUST set the existing task verification result to
  failed and remain eligible for the existing bounded RecoveryPolicy.
- **RTE-VER-005:** Tracking a verification failure MUST NOT create a mutation, Plan state, retry
  policy, or new lifecycle state.

## 17. Final Response Verification Rules

- **RTE-VER-006:** The Completed request MUST expose the current value of the existing
  `task_test_result` in a
  compact deterministic `Authoritative verification` block containing `status`, `level`, and
  `command`, plus `error` when present. `status` is `passed` only when `ok is True`, `failed` when
  `ok is False`, and `unavailable` when `task_test_result is None`. Values MUST be rendered as
  single-line JSON string scalars; absent `level` or `command` values render as `unavailable`, and
  absent `error` is omitted.
- **RTE-VER-007:** The Report `Test Result` section remains the authoritative Runtime fact. The
  model-authored summary MUST NOT override it.
- **RTE-VER-008:** The report heading for model text MUST be exactly `Model-authored Summary` and
  MUST state immediately below it that verification authority is the structured `Test Result`
  section.
- **RTE-VER-009:** Runtime MUST NOT silently rewrite, parse, or repair the full natural-language
  final response.
- **RTE-VER-010:** The Completed prompt MUST state that only the displayed Runtime fact is
  authoritative for current task verification and MUST forbid reporting unavailable or failed
  verification as passed. It MUST NOT copy a full ToolResult or introduce verification history.
- **RTE-VER-011:** `task_test_result` remains the sole current verification fact for this contract.
  Version 1.1 MUST NOT add a verification ledger, attempt counter, persistence layer, or Plan state.

## 18. Tool Contract Guidance

- **RTE-TOOL-001:** No Tool schema or description change is approved in v1.1.
- **RTE-TOOL-002:** `read_artifact` out-of-range input remains a validation error with the maximum
  in its error text; Runtime MUST NOT clamp it silently.
- **RTE-TOOL-003:** `edit_file` remains exact and atomic; malformed entries and stale text remain
  deterministic errors.
- **RTE-TOOL-004:** Bash quoting and exit-status mistakes remain model-correctable failures.

The isolated failures in the evidence run do not justify compatibility behavior.

## 19. Observability

- **RTE-OBS-001:** `trace.jsonl` MUST retain raw Provider usage fields and prefix diagnostics.
- **RTE-OBS-002:** `cost.json` MUST remove the unsupported logical additive fields and preserve raw
  fields under their existing names.
- **RTE-OBS-003:** `report.md` MUST not display `logical_input_tokens` after implementation.
- **RTE-OBS-004:** Verification ignored/recorded events MUST distinguish discovery from validation.
- **RTE-OBS-005:** No new aggregate metric family is authorized.
- **RTE-OBS-006:** Per-turn and aggregate input category breakdowns MUST use local estimates only
  and MUST NOT emit provider-derived `allocated_tokens` or provider-derived input shares.

## 20. Configuration Changes

None.

- **RTE-CFG-001:** New production config fields: zero.
- **RTE-CFG-002:** No Context, Provider, cache, verification, or lifecycle default may change.

## 21. Required Code Changes by File

| File | Required change | Requirements |
|---|---|---|
| `src/runtime/hooks/tracking.py` | Narrow discovery classification so `git diff ... --check` with verification purpose is tracked; preserve ordinary discovery behavior | `RTE-VER-001..005` |
| `src/agent/prompts.py` | Delete the dynamic call-budget system warning and render the compact current authoritative verification block only for Completed | `RTE-LIFE-001`, `RTE-LIFE-009`, `RTE-VER-006`, `RTE-VER-010..011` |
| `src/agent/loop.py` | Stop passing call-budget state into system-prompt construction and pass the existing `task_test_result` into Completed prompt construction; do not change lifecycle or tool selection | `RTE-LIFE-009`, `RTE-VER-006`, `RTE-VER-011` |
| `src/agent/messages.py` | Delete unsupported additive `logical_input_tokens` and unused derived `context_tokens` properties | `RTE-PROV-001..004`, `RTE-CACHE-002`, `RTE-DEL-002` |
| `src/runtime/observability/cost_tracker.py` | Remove logical aggregates; retain raw usage; make input category breakdown local-estimate-only while leaving output allocation unchanged | `RTE-CACHE-001..006`, `RTE-OBS-002`, `RTE-OBS-006` |
| `src/runtime/observability/trace_logger.py` | Stop emitting the derived logical field; retain raw usage | `RTE-PROV-001..002`, `RTE-OBS-001` |
| `src/runtime/observability/report_writer.py` | Remove logical input from cost summaries and mark structured verification as authoritative over model summary | `RTE-CACHE-001..002`, `RTE-VER-007..008`, `RTE-OBS-003` |
| `tests/unit/agent/test_prompts.py` | Replace the dynamic warning assertion and cover failed, passed, and unavailable Completed verification facts | `RTE-TST-010`, `RTE-TST-016..018` |
| `tests/unit/agent/test_loop.py` | Remove deleted `TokenUsage` property assertions and prove the existing verification fact reaches Completed prompt construction | `RTE-TST-002`, `RTE-TST-010`, `RTE-TST-016..017` |
| `tests/unit/runtime/hooks/test_tracking.py` | Replace the test that classifies wrapped `git diff --check` as discovery and preserve plain discovery cases | `RTE-TST-006..009` |
| `tests/unit/runtime/observability/test_cost_tracker.py` | Replace additive and Provider-allocation assertions with raw-field and local-estimate-only contracts | `RTE-TST-001..005` |
| `tests/integration/test_run_artifacts.py` | Assert cost/report field deletions and the structured verification/model-summary truth hierarchy | `RTE-TST-002`, `RTE-TST-011` |

No other production file is authorized.

## 22. Required Code Deletions

The implementation MUST delete, rather than deprecate or duplicate:

1. `TokenUsage.logical_input_tokens`;
2. `TokenUsage.context_tokens`;
3. CostTracker logical input/total state and serialization;
4. per-turn logical input/total fields;
5. trace-derived logical input emission;
6. report text for logical input;
7. provider-derived `allocated_tokens`, shares, and top-category ranking from input category
   breakdowns. Output category allocation remains unchanged.

- **RTE-DEL-001:** No replacement compatibility alias or parallel aggregate may be added.
- **RTE-DEL-002:** `TokenUsage.context_tokens` MUST be deleted. Repository-wide review found no
  production consumer; the `model_call_start.context_tokens` trace field is a distinct CMV3
  pressure diagnostic and MUST remain unchanged.

## 23. Test Plan

### 23.1 Deterministic tests

- **RTE-TST-001 — Raw usage preservation.** Construct usage with input, cache-read,
  cache-creation, deleted-cache, and output values. Assert every raw field is serialized unchanged.
- **RTE-TST-002 — Inclusive cache sample.** Use `input=80_000`, `cache_read=79_000`. Assert no
  production field reports `159_000` as input or logical input, and `TokenUsage` exposes neither
  `logical_input_tokens` nor `context_tokens`.
- **RTE-TST-003 — No-cache sample.** Use input with zero cache fields and assert raw totals remain
  unchanged.
- **RTE-TST-004 — Local input breakdown.** Use `input=80_000` and `cache_read=79_000`. Assert raw
  fields are unchanged and input categories contain only `chars`, `estimated_tokens`, and
  locally-derived `estimated_share`; no Provider token is allocated across categories.
- **RTE-TST-005 — Pressure isolation.** Record cost usage before/after a Context measurement and
  assert pressure, trigger, and hard limit are identical.
- **RTE-TST-006 — Verification git diff check.** A failing `purpose=verify` command beginning
  `git diff --no-index --check` MUST create a failed authoritative verification result and a
  `test_result` event.
- **RTE-TST-007 — Wrapped verification.** `cd game && git diff --check` with verification purpose
  MUST be tracked.
- **RTE-TST-008 — Discovery preservation.** Plain `git diff`, `git status`, `command -v`, and
  `which` MUST remain ignored and MUST NOT overwrite an earlier real verification.
- **RTE-TST-009 — Recovery path.** A failed tracked whitespace check MUST cause the existing
  bounded verification recovery message and prevent successful task inference until a later
  verification succeeds.
- **RTE-TST-010 — Failed verification fact.** With `task_test_result.ok=False`, assert the
  Completed request contains authoritative status `failed`, the current level and command, and the
  current error without expanding tool capabilities.
- **RTE-TST-011 — Report truth hierarchy.** Structured Test Result failure remains visible and
  authoritative even when a synthetic model summary contains a contradictory pass claim.
- **RTE-TST-012 — Stable lifecycle behavior.** Planning and Executing capability tests remain
  unchanged; Completed still exposes zero tools.
- **RTE-TST-013 — CMV3 regression.** Existing Context budget, admission, rehydration, Artifact,
  History, and Full Rebase suites pass unchanged.
- **RTE-TST-016 — Passed verification fact.** With `task_test_result.ok=True`, assert the Completed
  request contains authoritative status `passed` and the exact current level and command.
- **RTE-TST-017 — Unavailable verification fact.** With `task_test_result=None`, assert the
  Completed request contains authoritative status `unavailable` and does not say `passed`.
- **RTE-TST-018 — Stable-phase call budget.** Build otherwise identical Executing requests with
  six and five remaining calls. Assert system prompts and tool schemas are identical and the
  dynamic warning text is absent.

### 23.2 Provider evaluation

- **RTE-TST-014 — Same-provider accounting observation.** Re-run the fixed task and report raw
  input/cache fields plus prefix hashes. Do not reintroduce an additive total.
- **RTE-TST-015 — Verification truth eval.** Force one failed `git diff --check`, then a valid
  recovery. Confirm the final response distinguishes the failed attempt from the successful check.

Provider evaluation is behavioral evidence, not a deterministic unit-test claim. If unavailable,
report `provider evaluation not run`.

## 24. Acceptance Criteria

Implementation is complete only when:

1. crossing the call-budget warning threshold does not mutate the system prompt within a stable
   Plan phase;
2. `TokenUsage.logical_input_tokens`, `TokenUsage.context_tokens`, and every unsupported
   Provider-independent logical additive input or total are absent;
3. raw Provider usage fields are preserved unchanged;
4. raw Provider input/cache fields are not allocated across local input categories;
5. input category breakdown contains local characters, estimated tokens, and estimated shares
   only;
6. `purpose=verify` `git diff --check` and `git diff --no-index --check` are tracked as
   authoritative verification;
7. plain `git diff` remains discovery and cannot overwrite verification state;
8. the Completed model receives the current authoritative `task_test_result`, with absence
   represented as unavailable;
9. the structured Report `Test Result` remains authoritative over the labelled model-authored
   summary;
10. no verification ledger, state machine, retry policy, or persistence layer is added;
11. Planning, approval, Executing, Completed, permission capabilities, and Completed's zero-tool
   contract are unchanged;
12. no file under `src/runtime/context/` and no Source, Artifact, History, admission, or Full
   Rebase behavior changes;
13. new production files are zero;
14. new configuration fields are zero;
15. no compatibility alias, silent ToolCall repair, or fallback is added, and every production
   diff maps to this document;
16. targeted tests and the full `pytest`, `ruff check .`, and `ruff format --check .` gates are
   reported truthfully.

## 25. Migration Sequence

1. Verify the implementation tree equals the frozen tree `ccaef4f6f6ecab489080805d00b74d229b8d9fef`.
2. Delete the stable-phase call-budget system warning and add `RTE-TST-018`.
3. Correct verification classification, pass the existing `task_test_result` to Completed prompt
   construction, and add `RTE-TST-006..010` plus `RTE-TST-016..017`.
4. Delete additive logical fields from `TokenUsage`, trace, cost tracker, and report; convert input
   category breakdowns to local estimates in the same observability change.
5. Run targeted verification, prompt, observability, AgentLoop, and Context regression tests.
6. Run the full repository quality gates.
7. Run `RTE-TST-014..015` when a real Provider is available; otherwise record `provider evaluation
   not run`.
8. Self-review that no Context, lifecycle capability, Tool schema, configuration, compatibility,
   or fallback diff exists.

## 26. Explicitly Deferred Work

- final-response prefix preservation;
- a Provider-normalized billing-token contract;
- cache pricing or cost estimation;
- a multi-record verification ledger;
- browser capability/limitation state;
- Tool schema refinements for isolated argument mistakes;
- shell parsing beyond the existing single-wrapper normalization;
- any Context Manager policy or constant change.

Deferred items require new evidence and a separate spec revision. They MUST NOT be implemented
opportunistically.

## 27. Requirement Index

| Family | Scope | IDs |
|---|---|---|
| Scope | Authority and prohibited drift | `RTE-SCOPE-001..003` |
| Provider | Raw usage and pressure boundary | `RTE-PROV-001..009` |
| Cache | Reporting and prefix diagnosis | `RTE-CACHE-001..006` |
| Lifecycle | Stable phases, intentional transitions, and deferred final optimization | `RTE-LIFE-001..009` |
| Verification | Classification and final truthfulness | `RTE-VER-001..011` |
| Tool | Explicitly unchanged Tool contracts | `RTE-TOOL-001..004` |
| Observability | Trace, cost, and report output | `RTE-OBS-001..006` |
| Configuration | No new or changed config | `RTE-CFG-001..002` |
| Deletion | Removal without compatibility aliases | `RTE-DEL-001..002` |
| Tests | Deterministic and Provider evaluation | `RTE-TST-001..018` |
