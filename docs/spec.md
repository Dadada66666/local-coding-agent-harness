# Context Manager V2 Specification

> Context Retention, Tool-Result Admission, Epoch Rebase, Recoverability, and Prompt-Prefix Stability

| Field | Value |
| --- | --- |
| Spec ID | `CMV2` |
| Version | `1.1` |
| Status | `REVIEW REQUIRED` |
| Source | `docs/v1_prompt_spec.md` |
| Baseline | Git commit `7b29da62f0e1b6016ed8649e6efb03a3807ee370` plus the immutable prerequisite overlay identified in §1.1 |
| Scope | Context preparation, ToolResult admission, historical rebase, checkpointing, artifact/source recovery, provider usage accounting, and minimal cache diagnostics |

## Normative language

The terms **MUST**, **MUST NOT**, **SHOULD**, **SHOULD NOT**, and **MAY** are normative.

- **MUST / MUST NOT**: required for conformance.
- **SHOULD / SHOULD NOT**: expected unless a documented `SPEC DEVIATION REQUIRED` is approved.
- **MAY**: optional and cannot be relied upon by other requirements.

Every production change implementing this document MUST reference at least one requirement ID such as `CMV2-INV-01`. Changes that cannot be mapped to a requirement are out of scope.

Version 1.1 is a scope-controlled retention revision. It changes only full-rebase raw-tail policy, rebase economics/runway, retained-working-set calibration, candidate selection, and directly dependent configuration/tests/success criteria. All other approved V2 architecture remains unchanged.

---

# 1. Repository Reality Check

## 1.1 Reproducible review baseline

This revision is reviewed against an exact base commit plus an exact tracked-file overlay. It does not refer to an unspecified “current working tree.” The repository identity remains immutable while the retention values are under calibration.

```text
base_commit = 7b29da62f0e1b6016ed8649e6efb03a3807ee370
prerequisite_patch_id_stable = 8d4020544dda3e74255254d3767b27419a87b586
prerequisite_manifest_sha256 = afbb662638f68edf3060bf7ee59c2aa447c2bb2878fbaddd805bf39ee7fa8495
source_prompt_sha256 = 2fc5c5c45b42e519d4c65a3967d8e02c25c28adde7e6faa22324589000de4346
```

The prerequisite overlay contains exactly the following tracked replacements and no tracked additions or deletions. Hashes are SHA-256 over file bytes:

| Path | SHA-256 |
| --- | --- |
| `src/agent/loop.py` | `7e29cc877933e1ec3b8f3be8fceb8b2918822810e9fa00c9ad74e3d2e9177461` |
| `src/agent/prompts.py` | `7e052bba8d5a83a1501f9b29cf398ba1e326f8b0f1498f629816883a4409f786` |
| `src/runtime/context/manager.py` | `4ded8985ca5bbf8a82be10cfe35c4766627b9be65cb8d67c8a528b6dffc3a7a6` |
| `src/runtime/context/projection.py` | `ef3a1032ad41b0a53ed7214e75cad9cb51c8028c67dc7b0810f70187f666849e` |
| `src/runtime/observability/cost_tracker.py` | `053b90877361c8808ca85f53d226fb385d701cc3fba5579773d4d80fc6ce1bf9` |
| `src/tools/update_plan.py` | `781d1194d6f242251dd254e8b25bef1b5697db1f460cc1456aec88224ebe02f8` |
| `tests/unit/agent/test_prompts.py` | `8b1334cc6a456b3bec4a304be8153013bf43ed37e823561f06fc057693310a2a` |
| `tests/unit/runtime/context/test_manager.py` | `9fcc13aa3ad75e63038121d25aede9c74c498bd243889b83092415fe6805c600` |
| `tests/unit/runtime/observability/test_cost_tracker.py` | `946f383e4f12bf492a72f0c57a5cae21058f74aa956c0fb67aba5b8c92ac69e4` |
| `tests/unit/tools/test_plan_tools.py` | `a0fc65b4dedfde95b9feef4a1cd795b2ef0b209d86a5664a82d0be569753bef0` |

To verify the immutable repository baseline before calibration or implementation:

1. check out the exact base commit;
2. apply or restore the prerequisite overlay;
3. verify the stable Git patch ID and every file hash above;
4. refuse implementation if any value differs.

A subsequent prerequisite commit MAY replace the overlay only through a Spec-only amendment that records the new full commit SHA and verifies that its tree is content-equivalent to this manifest. Otherwise `SPEC DEVIATION REQUIRED` applies.

`docs/spec.md` is intentionally excluded from its own baseline hash. `docs/v1_prompt_spec.md` is identified separately by `source_prompt_sha256`.

## 1.2 Verified configuration

The current `RunConfig` defaults are:

| Configuration | Current value | V2 decision |
| --- | ---: | --- |
| `max_turns` | `40` | Keep |
| `max_tool_result_chars` | `18000` | Keep |
| `max_tool_round_tokens` | `12000` | Keep; move enforcement to admission |
| `compact_threshold_chars` | `180000` | Keep as fallback only when no token target/window is known |
| `context_window_tokens` | `None` | Keep; deployment MUST configure the real provider window when known |
| `context_target_tokens` | `272000` | Keep as the default high watermark/economic capacity target |
| `context_eager_projection_tokens` | `0` | Remove; its current `0 = auto` behavior is ambiguous and duplicates the V2 pressure trigger |
| `context_soft_limit_ratio` | `0.8` | Keep for provider-window safety |
| `context_safety_margin_tokens` | `4096` | Keep |
| `context_recent_target_tokens` | `12000` | Calibration required; no V2 production default selected yet |
| `context_recent_max_tokens` | `24000` | Calibration required; no V2 production default selected yet |
| `context_min_recent_rounds` | `2` | Keep |
| `context_checkpoint_max_chars` | `6000` | Provisional change to `12000`, held constant during retention calibration |
| `context_task_boundary_tokens` | `12000` | Keep |
| `max_context_recovery_attempts` | `1` | Keep |
| `max_context_compaction_failures` | `3` | Keep |
| `artifact_read_max_chars` | `6000` | Keep |

`context_eager_projection_tokens=0` currently does **not** disable eager projection. `ContextManager._eager_watermarks()` derives an automatic high watermark of:

```text
272000 × 0.85 = 231200 tokens
```

and a lower hysteresis point of:

```text
231200 × 0.84 = 194208 tokens
```

That implicit behavior is a confirmed source of policy ambiguity.

The previously proposed `136000/160000` values are no longer a frozen default. At the 272K high watermark they can leave too little post-rebase runway:

```text
high watermark                         = 272000 tokens
post-rebase ceiling (fixed 65% max)    = 176800 tokens
possible local-after range             = approximately 150000-176800 tokens
possible runway                        = approximately 95200-122000 tokens
conservative raw-tail reference        = 136000/160000 tokens
checkpoint benchmark bound             = 12000 chars
```

This design protects raw history, but it may reclaim too little capacity after deliberately breaking the prompt prefix. A long task could then enter another rebase after only about 96K of growth, multiplying prefix-breaking epochs, uncached input, and latency. The 65% value is therefore only a maximum post-rebase request size, not a target to fill, and `136000/160000` is only the conservative upper calibration reference. Section 12 selects the smallest sufficient working set before any production default is frozen.

## 1.3 Verified implementation facts

The following are confirmed from the current working tree:

1. `large_output_hook()` bounds a single oversized result before it is appended to `AgentContext.messages`; full output is persisted to `ArtifactStore` first.
2. `read_file` bounds source at the tool layer using a line ceiling and character ceiling. It returns exact pagination metadata and SHA-bound source metadata.
3. `AgentLoop` executes all ToolCalls, collects their result strings, then calls `AgentContext.add_tool_results()` once for the batch.
4. `ContextManager.prepare_context()` currently calls `ToolResultProjector.enforce_round_budget()` on the already-appended message history at the start of the next provider request.
5. Therefore `max_tool_round_tokens` is currently enforced as a delayed historical rewrite, not as first-admission shaping.
6. `prepare_context()` can perform three distinct historical transformations: round-budget projection, eager consumed-result projection, and pressure-driven projection/full compaction.
7. `compact_consumed_results()` only considers successful compactable ToolResults already consumed by a prior model request and outside the protected recent suffix.
8. The current local patch adds a target savings value, but still selects non-source candidates before source candidates while preserving oldest-first order inside each tier. This can rewrite an early `list_dir` or `grep` result and invalidate a much longer stable suffix.
9. Full compaction already refuses a checkpoint-only prefix and refuses a candidate whose local token estimate does not decrease.
10. `conversation_messages` is the append-only audit; `messages` is the provider-facing working context and may be rebased.
11. Source rehydration is range-aware and appends a new `read_file` result. It does not replace an old source stub in place.
12. Artifact rehydration through `read_artifact` appends a new ToolResult. It does not replace the original artifact reference.
13. `RuntimeCheckpointBuilder` already includes bounded current task, plan, changed-file, verification, source-manifest, recent ToolCall, and artifact-reference information.
14. It does not parse and merge a previous Runtime checkpoint into the next checkpoint; repeated epochs can therefore lose earlier constraints, decisions, rationale, corrections, findings, and failures.
15. An optional `summarizer` hook exists in `RuntimeCheckpointBuilder` and `ContextManager`, but production `build_runtime()` constructs plain `ContextManager()`. The production summarizer is therefore `None` and `semantic_summary` is not produced.
16. Provider raw cache usage fields are parsed and stored. Context pressure uses a local-estimate baseline plus conservative provider-usage normalization.
17. The current local observability patch records hashes for the system prompt, tool schemas, and whether the previous message sequence remains an exact prefix. No full prompt is copied into logs.

## 1.4 Architectural conclusion

The repository does not need a new memory subsystem. It already has the four required representations:

1. provider-visible hot messages;
2. reconstructible source stubs using path, SHA, and line range;
3. ArtifactStore-backed cold ToolResults;
4. bounded runtime checkpoints.

The defect is policy overlap and timing: admission shaping, historical projection, and full compaction currently overlap in `prepare_context()`.

---

# 2. Current Context Flow

## 2.1 Current ToolResult path

```text
AgentLoop receives assistant ToolCalls
    ↓
ToolExecutor.execute()
    ↓
PRE_TOOL_VALIDATE / PRE_TOOL_USE hooks
    ↓
BaseTool.call()
    ↓
POST_TOOL_USE hooks
    ├─ secret_redaction_hook()
    ├─ large_output_hook()
    ├─ record_tool_budget_hook()
    └─ trace / mutation / verification hooks
    ↓
AgentLoop collects result.content
    ↓
AgentContext.add_tool_results()
    ├─ append to provider-facing messages
    └─ append to conversation_messages audit
    ↓
next AgentLoop model turn
    ↓
ContextManager.prepare_context()
    ├─ measure request
    ├─ enforce_round_budget()        ← delayed rewrite today
    ├─ eager consumed projection     ← historical rewrite today
    ├─ pressure consumed projection  ← historical rewrite today
    └─ full history compaction
    ↓
ModelClient.call(system, messages, tools)
    ↓
Anthropic-compatible Provider
```

## 2.2 Current admission transformations

| Stage | File / function | Current behavior | Classification |
| --- | --- | --- | --- |
| Source pagination | `src/tools/read_file.py`, `ReadFileTool.call()` | Limits source by lines and characters before returning | Correct admission shaping |
| Grep count | `src/tools/grep.py`, `GrepTool.call()` | Limits matches before returning | Correct admission shaping |
| Secret redaction | `src/runtime/hooks/policy.py`, `secret_redaction_hook()` | Redacts before result is appended | Correct admission shaping |
| Single-result large output | `src/runtime/hooks/policy.py`, `large_output_hook()` | Persists raw content, returns bounded preview/reference | Correct admission shaping |
| Round aggregate budget | `src/runtime/context/projection.py`, `enforce_round_budget()` | Rewrites the appended ToolResult message on the next request | Incorrect timing; historical rewrite |

## 2.3 Current provider request path

`AgentLoop.run_until_idle()` rebuilds the system prompt and visible tool schemas, calls `ContextManager.prepare_context()`, and sends the resulting `context.messages` to `ModelClient.call()`.

The prompt/tool prefix is deterministic within a stable lifecycle phase, except for intended changes when:

- Plan phase/capabilities change;
- a user continuation changes control-plane state;
- the call-limit warning becomes active;
- a task boundary or Context rebase occurs.

These lifecycle changes are intentional and remain outside the Context V2 rewrite policy.

---

# 3. Historical Mutation Inventory

| ID | File / function | Trigger | Historical object changed | Purpose | Prefix-cache risk | V2 disposition |
| --- | --- | --- | --- | --- | --- | --- |
| HM-01 | `ContextManager.prepare_context()` → `enforce_round_budget()` | Any prior ToolResult batch over `max_tool_round_tokens` | One or more old ToolResult blocks | Per-round safety | High; runs on next request | Move to admission |
| HM-02 | `prepare_context()` eager branch → `compact_consumed_results()` | `used_tokens >= eager high` and not at soft pressure | Old consumed ToolResults | Economic cleanup | High; may occur repeatedly | Remove |
| HM-03 | `prepare_context()` pressure branch → `compact_consumed_results()` | Soft pressure or force | Old consumed ToolResults | Avoid full compaction | High; may be followed by full compaction | Replace with one atomic rebase decision |
| HM-04 | `_compact_history()` | Still pressured after projection or forced recovery | Old prefix replaced by checkpoint | Recover capacity | Expected; intentional epoch boundary | Keep as one V2 rebase option |
| HM-05 | `compact_task_boundary()` | A new task starts and prior task history exceeds threshold | Prior-task history replaced by checkpoint | Task isolation/bounded carry-over | Expected task boundary | Keep |
| HM-06 | Provider overflow recovery | Explicit provider overflow | Same paths as HM-03/HM-04 | Hard safety recovery | Acceptable | Simplify to one forced full rebase |

`conversation_messages` is not modified by these operations and remains the audit source of truth. Only provider-facing `messages` is eligible for rebase.

---

# 4. Admission Flow

## 4.1 V2 target flow

```text
Tool executes
    ↓
result redacted
    ↓
single-result admission bound applied
    ├─ oversized non-source raw content → ArtifactStore
    └─ provider-visible content → bounded preview/reference
    ↓
entire ToolResult batch evaluated against 12K round budget
    ├─ already within budget → unchanged
    └─ over budget → shape this new batch only
    ↓
append the final representation once to messages
    ↓
future normal model turns append only
```

## 4.2 Admission requirements

### `CMV2-ADM-01` — First visibility

A ToolResult MUST be fully normalized, redacted, persisted when required, and bounded before its first provider-visible append.

### `CMV2-ADM-02` — No admission rewrite of older history

Round-budget enforcement MUST inspect and transform only the new ToolResult batch. It MUST NOT scan or rewrite prior messages.

### `CMV2-ADM-03` — Round hard bound

The serialized token estimate of ToolResult blocks in one newly admitted batch MUST be at most `max_tool_round_tokens` whenever a smaller valid representation exists.

If source-only results cannot fit without projection, source results MAY be admitted as source stubs. This is a hard output-safety fallback, not a source-importance decision.

### `CMV2-ADM-04` — Admission victim order

Within a single new batch that exceeds the round budget:

1. shrink already-persisted previews to artifact stubs;
2. persist and shorten non-source results, largest reduction first;
3. project source slices to source stubs only if the batch still exceeds the bound.

This order applies only before first provider visibility. It MUST NOT be reused as a historical eviction policy.

### `CMV2-ADM-05` — Context generation

Admission shaping MUST NOT increment `context_generation`, because no provider request has observed the unshaped batch. A historical rebase MUST increment it exactly once.

### `CMV2-ADM-06` — Audit and recovery

Every non-source result shortened during admission MUST have an ArtifactStore record containing the complete redacted content. Every source result shortened during admission MUST retain path, SHA, returned line range, and recovery cursor metadata.

## 4.3 Exact Tool admission semantics

| Tool/result | First-admission rule |
| --- | --- |
| `read_file` | Use existing line/character bound. Append normal pages unchanged. If the aggregate round budget still cannot be met, source-stub pages only as the last admission victim. Do not create generic source artifacts. |
| `grep` | Keep tool-level match bound. If content exceeds the single-result character limit or round budget, persist full redacted output and append bounded matches/preview plus artifact reference. |
| `list_dir` | Append unchanged when within bounds. Do not later remove it merely because it is cheap to rerun. Persist and shorten only if its new batch violates admission bounds. |
| `bash` | Persist complete oversized stdout/stderr at admission. Visible content contains command context, exit status/error, bounded head/tail, and artifact reference through the existing result/hook metadata. |
| Test output | Same as `bash`; failing diagnostics MUST retain a salient bounded preview. Successful but huge output is persisted and bounded. |
| `view_diff` | Append normal output unchanged. Persist and bound oversized output at admission. |
| `read_artifact` | Append the requested bounded slice as new evidence. It may itself be admission-bounded, but MUST NOT expand the original historical stub. |
| Generic oversized result | Persist full redacted content and append a bounded preview/reference before first model visibility. |
| Error result | Preserve a bounded actionable error. Historical projection MUST NOT silently erase error results. |

---

# 5. Prompt Cache Risk Analysis

## 5.1 Cache model

V2 assumes only the provider-neutral property that prompt caching is commonly prefix-oriented: an exact stable prefix is potentially reusable, while changing an early historical element can invalidate reuse after that point.

V2 MUST NOT assume:

- a specific cache TTL;
- a specific provider breakpoint API;
- cache availability for every `ANTHROPIC_BASE_URL` implementation;
- that cache creation/read fields use identical accounting across providers;
- that a hash match guarantees a provider cache hit.

## 5.2 Prefix-preserving operations

The following MUST preserve the previous provider-visible message sequence as an exact prefix:

- normal assistant responses;
- normal ToolResult admission after the current tail;
- runtime retry messages appended at the tail;
- `read_file` rehydration;
- `read_artifact` rehydration;
- normal turns below Context pressure.

## 5.3 Allowed prefix breaks

The following MAY intentionally break the previous prefix:

- one atomic pressure-triggered Context rebase;
- one forced rebase after provider overflow/hard-limit pressure;
- task-boundary compaction between distinct tasks;
- lifecycle-required system/tool-schema changes, including Plan phase and permissions/capabilities.

No other operation may mutate provider-visible historical messages.

## 5.4 Rebase locality

If a ToolResult-only rebase can meet the rebase target, candidate API rounds MUST be considered newest-to-oldest outside the protected recent tail. This preserves the longest possible unchanged prefix.

Within one selected API round, non-source results MAY be shortened before source results because changing any result in that round already establishes the same prefix boundary. This tie-breaker reduces avoidable source rehydration without sacrificing an earlier prefix.

Historical selection MUST NOT globally prioritize `list_dir`, `grep`, or another Tool type ahead of prefix locality.

## 5.5 Minimal diagnostics

The existing per-call diagnostics are sufficient:

- `system_hash`;
- `tools_hash`;
- `previous_messages_preserved`;
- `context_generation`;
- `plan_phase`;
- provider raw usage fields.

No full prompt content, per-message importance score, cache strategy state, or new aggregate metric framework is permitted.

A cache miss is attributable to Runtime prefix instability only when one of the following also changed:

- system hash;
- tools hash;
- previous messages were not preserved;
- context generation;
- intended lifecycle phase.

If all fingerprints are stable and the provider reports no cache read, the report MUST classify the cause as provider-side cache behavior unknown, not as a ContextManager defect.

---

# 6. Root Causes

## P0 — Must change

### `CMV2-RC-P0-01` — Round budget is delayed

The 12K round budget currently rewrites a ToolResult batch only at the next `prepare_context()`. The batch could have been shaped before first visibility; delaying it needlessly turns admission into historical mutation.

### `CMV2-RC-P0-02` — Eager projection duplicates pressure management

`context_eager_projection_tokens=0` activates an automatic 231.2K threshold. It creates a second cleanup policy before the 272K Context target and can repeatedly rewrite consumed history.

### `CMV2-RC-P0-03` — Pressure recovery can mutate history twice

The current pressure path may project ToolResults and then immediately full-compact the same request. A single pressure event should produce no more than one provider-visible historical rewrite.

### `CMV2-RC-P0-04` — Historical candidate order can invalidate too much prefix

The current local candidate order prioritizes non-source type globally and retains oldest-first order. Rewriting an early, cheap-to-rerun result can invalidate a much longer suffix than selecting newer results.

## P1 — High value

### `CMV2-RC-P1-01` — Cache accounting needs semantic caution

Raw cache fields are useful evidence but cannot be blindly summed as a provider-neutral Context truth or actual cost. Local serialized estimate remains the reliable baseline; normalized provider usage remains a conservative safety anchor.

### `CMV2-RC-P1-02` — Rebase must be atomic and worthwhile

Target-bounded projection alone is insufficient if it commits a small partial rewrite. V2 requires preflight, a meaningful target, and one atomic commit.

## P2 — Later experiments

- Explicit provider cache controls, only after the configured provider contract is verified.
- Assistant `tool_use` history compression, because protocol risk is higher than ToolResult projection.
- Historical source snapshots for old SHA recovery after edits.
- Alternative rebase ratios, only through the benchmark protocol in this document.

## Not a problem

- `read_file` line/character admission bounds;
- range-aware source residency and append-only rehydration;
- SHA invalidation after file mutation;
- ArtifactStore as run-local authoritative cold storage;
- large-output persistence before first append;
- checkpoint-only and no-token-reduction full-compaction guards;
- task-boundary compaction between separate tasks;
- Plan, permission, task, and verification lifecycle behavior.

---

# 7. Context Manager V2 Spec

## 7.1 Goals

### `CMV2-GOAL-01`

Keep normal Context epochs append-only and maximize reusable prompt prefix length.

### `CMV2-GOAL-02`

Shape oversized and over-budget ToolResults before first provider visibility.

### `CMV2-GOAL-03`

Perform no more than one meaningful historical rebase for one pressure event.

### `CMV2-GOAL-04`

Keep every removed ToolResult recoverable through ArtifactStore or source path/SHA/range metadata.

### `CMV2-GOAL-05`

Optimize task success, model calls, uncached provider input, repeated reads/searches, latency, and actual cost as a system—not per-call Context size in isolation.

### `CMV2-GOAL-06`

Reduce policy layers and production complexity.

### `CMV2-GOAL-07`

Select the minimum sufficient raw working set that preserves long-task continuity, while consolidating semantic execution state across repeated epochs without requiring another model call.

The optimization objective is:

```text
minimize post-rebase Context
subject to:
    task continuity remains acceptable
    semantic checkpoint fidelity passes
    Runtime-induced reread/re-grep does not materially regress
    model-call count does not materially regress
    task success and provider safety do not regress
```

Normal epochs are conservative and append-only. Once a full rebase intentionally breaks the prefix, it must be decisive enough to create meaningful runway. V2 neither maximizes retained Context nor minimizes Context at any cost.

## 7.2 Non-goals

V2 does not introduce:

- semantic memory or retrieval;
- vector storage;
- SourceMemory/SourceCache;
- resident-range manager beyond existing observation metadata;
- ML/LLM importance classification;
- reuse probability or semantic eviction scores;
- LRU/TTL source heuristics;
- a new Context policy/state-machine subsystem;
- provider-specific cache branches;
- explicit cache breakpoint/TTL configuration;
- assistant ToolCall compression;
- historical source-version storage;
- Plan, permission, approval, verification, or task lifecycle changes;
- higher Context, round, page, output, or model-call limits.

## 7.3 Terms

| Term | Definition |
| --- | --- |
| Hot Context | `context.messages`, serialized into the next provider request. |
| Audit history | `context.conversation_messages`, append-only record not used as mutable provider Context. |
| Admission | Transformation completed before a new ToolResult is first visible to a provider request. |
| Historical mutation | Any change to an element already present in a completed provider request. |
| Context epoch | Interval between two intentional historical rebases. Within it, history is append-only. |
| Rebase | One atomic replacement of provider-visible historical messages that increments `context_generation` once. |
| Recent raw tail | Newest complete API rounds retained verbatim across a full rebase. |
| Recoverable source | Source observation recoverable using workspace path, matching SHA, and line range/cursor. |
| Cold ToolResult | Non-source result persisted in ArtifactStore and represented by a bounded artifact reference. |
| Consumed result | ToolResult included in a successfully completed prior model request. |
| High watermark | `ContextMeasurement.soft_limit_tokens`. |
| Hard pressure | `used_tokens >= hard_limit_tokens` when a hard limit is known, or an explicit provider overflow recovery. |
| Post-rebase ceiling | Maximum local request size accepted after a normal-pressure rebase; never a target to fill. |
| Minimum sufficient working set | Smallest calibrated raw-tail profile that passes every retention acceptance gate. |
| Post-rebase runway | `high - local_after`; capacity available before the next normal pressure point. |

## 7.4 Invariants

### `CMV2-INV-01` — Epoch append-only

Below high watermark, `prepare_context()` MUST NOT alter existing message identity or content.

### `CMV2-INV-02` — Admission/eviction separation

Admission shaping MUST operate only on the new result batch. Rebase logic MUST operate only after a real pressure trigger.

### `CMV2-INV-03` — One pressure event, one rewrite

A single `prepare_context()` invocation MUST commit at most one historical rebase.

### `CMV2-INV-04` — Complete API rounds

Rebase MUST preserve valid assistant `tool_use` and user `tool_result` pairing. A retained or removed boundary MUST be an API-round boundary.

### `CMV2-INV-05` — Atomicity

Preflight MUST use copies/estimates. Source projected markers, `context.messages`, `context_generation`, and projection/compaction counters MUST change only after a candidate is accepted.

Artifact persistence MAY occur before commit because persistence and eviction are independent. A persisted but unused artifact is safe; an incompletely rewritten message history is not.

### `CMV2-INV-06` — Recoverability

Every shortened non-source result MUST reference an existing artifact containing complete redacted content. Every shortened source result MUST retain reconstruction metadata.

### `CMV2-INV-07` — Append-only rehydration

`read_artifact` and repeated `read_file` calls MUST append new evidence at the current tail. They MUST NOT expand or replace old stubs in place.

### `CMV2-INV-08` — Checkpoint immutability

A Runtime checkpoint MUST remain unchanged within its epoch. A new checkpoint is created only by the next full/task-boundary rebase.

Every new checkpoint MUST consolidate the previous checkpoint and the newly removed trajectory according to §7.6. Repeated rebases MUST accumulate bounded semantic state rather than summarize only the most recent removed prefix.

### `CMV2-INV-09` — Provider safety

An explicitly configured provider window, minus output reservation and safety margin, always has priority over the 272K target. No V2 policy may bypass the hard limit.

### `CMV2-INV-10` — Local savings accounting

Projection and compaction savings MUST equal local estimate before minus local estimate after. Provider usage anchors MUST NOT be subtracted from local post-rebase estimates.

### `CMV2-INV-11` — Decisive rebase

Between rebases, historical mutation is forbidden below real pressure. At a normal-pressure rebase, an accepted candidate MUST both remain below the post-rebase ceiling and satisfy the independent meaningful-reclaim requirement in §7.5. Spare ceiling capacity MUST NOT be filled with unnecessary history.

### `CMV2-INV-12` — Calibrated retention

Full rebase MUST retain the selected minimum sufficient working-set profile, complete API rounds, and `context_min_recent_rounds`. It MUST NOT assume that `136K/160K`, 65% of high, or any larger profile is inherently safer.

## 7.5 Exact pressure semantics

### `CMV2-TRG-01` — Measurement

For every model request:

```text
local = estimate_request_tokens(system, messages, tools)
provider_anchor = normalized previous provider usage + appended local estimate
used = max(local, provider_anchor when valid)
hard = context_window_tokens - max_output_tokens - safety_margin
high = min(context_target_tokens, floor(hard × context_soft_limit_ratio))
```

If only one of the high-limit inputs exists, use it. If neither exists, the existing character fallback applies.

The provider anchor is valid only for the same `context_generation` and a valid prior response index.

### `CMV2-TRG-02` — No-pressure path

If `force=false` and `used < high`, return the original `context.messages` unchanged. No projection event, compaction event, source projected marker, artifact created for historical eviction, or context-generation change is allowed.

### `CMV2-TRG-03` — Normal-pressure economics

When `used >= high` but hard pressure is false, evaluate candidates against two independent constraints:

```text
post_rebase_ceiling = floor(high × 0.65)
reclaimed_tokens = local_before - local_after
reclaim_ratio = reclaimed_tokens / local_before
minimum_rebase_gain_ratio = 0.50  # V2 starting hypothesis; benchmark before freeze
post_rebase_runway = high - local_after
```

`post_rebase_ceiling` is a maximum accepted request size. It is not a desired size, a raw-tail budget, or permission to fill spare capacity. A candidate MAY finish substantially below it.

`minimum_rebase_gain_ratio` is the independent economic test for intentionally breaking the current prefix. `0.50` is the initial closed-benchmark hypothesis because a normal rebase near the high watermark should recover approximately half of that capacity and create comparable runway. It is not a RunConfig field and MUST be confirmed or revised through §12 before this Spec is frozen. It MUST NOT silently inherit the old 35% implied by the ceiling.

A normal-pressure candidate is accepted only when:

```text
local_after < local_before
local_after <= post_rebase_ceiling
reclaim_ratio >= minimum_rebase_gain_ratio
```

No separate reclaim configuration or persistent metric is introduced. All four values are derived during preflight or benchmark reporting from existing local measurements.

For a deployment whose effective `high` is below 272K, the same formulas apply. Complete API-round integrity takes precedence over an exact token value; if an indivisible required round causes a candidate to exceed the ceiling or miss meaningful reclaim, the normal-pressure candidate is rejected rather than silently cutting the round.

### `CMV2-TRG-04` — Candidate A: ToolResult rebase

At normal pressure, build a non-mutating candidate that:

1. considers only successful compactable ToolResults;
2. considers only results consumed by a previous provider call;
3. excludes the recent raw tail;
4. visits complete API rounds newest-to-oldest;
5. within a selected round, shortens non-source results before source results;
6. preserves all other message content and order;
7. stops once the candidate is at or below `post_rebase_ceiling` and satisfies `minimum_rebase_gain_ratio`.

If Candidate A satisfies both economic constraints and every non-source stub has a valid artifact, commit it atomically and start a new epoch.

### `CMV2-TRG-05` — Candidate B: Full checkpoint rebase

If Candidate A cannot satisfy the normal-pressure constraints, discard Candidate A without changing Context and build a full checkpoint candidate from the original messages.

Candidate B MUST:

- replace only the old prefix;
- retain complete newest API rounds;
- keep at least `context_min_recent_rounds`;
- retain the selected calibrated raw working-set budget from §12, using newest complete API rounds;
- treat the selected raw target as a sufficiency budget: stop after it is met, except for one indivisible round needed to preserve the boundary, and never continue merely to approach the raw max or post-rebase ceiling;
- treat the selected raw max as a hard candidate bound, not an amount to fill;
- reject the normal-pressure candidate instead of cutting an indivisible API round when round integrity would exceed the allowed ceiling;
- prepend one bounded Runtime checkpoint;
- semantically consolidate the previous checkpoint and removed trajectory according to §7.6;
- include bounded artifact references and source manifest already available in runtime state;
- have a lower local token estimate than the original;
- remain at or below `post_rebase_ceiling` and satisfy `minimum_rebase_gain_ratio` under normal pressure.

If it meets these conditions, commit it atomically. Candidate A MUST NOT be committed first.

### `CMV2-TRG-06` — Hard pressure/overflow

When `used >= hard` or `force=true` due to provider overflow:

1. skip ToolResult-only Candidate A;
2. build Candidate B directly;
3. commit Candidate B if `local_after < local_before`, even if it does not satisfy the normal-pressure ceiling or minimum-gain hypothesis;
4. if no token-reducing candidate exists, return failure to the existing bounded overflow recovery path.

Safety recovery may relax the economic minimum gain, but it may not commit a non-reducing compaction.

### `CMV2-TRG-07` — Checkpoint churn guard

If the removable prefix consists only of an existing Runtime checkpoint, skip full rebase. A Runtime checkpoint MUST NOT be compacted into another Runtime checkpoint without new old trajectory.

### `CMV2-TRG-08` — Failure handling

Artifact persistence failure for Candidate A cancels Candidate A. The manager MAY attempt Candidate B from the untouched original history. It MUST NOT leave a partial message rewrite or projected source marker.

No additional retry manager, compatibility mode, or fallback state is allowed.

## 7.6 Semantic checkpoint consolidation

### `CMV2-CHK-01` — Production wiring decision

The current optional summarizer is not wired in production: `build_runtime()` creates `ContextManager()` without a summarizer, so `RuntimeCheckpointBuilder.summarizer` is `None`.

V2 resolves this before implementation as follows:

- semantic consolidation MUST be implemented deterministically inside the existing `RuntimeCheckpointBuilder`;
- it MUST NOT make an additional model/provider call;
- it MUST NOT add a summarization agent, manager, state machine, provider branch, or RunConfig field;
- the dormant optional `summarizer` constructor argument and `_add_semantic_summary()` hook MUST be removed.

In this Spec, “semantic” means consolidation into explicit execution-state categories, not LLM-generated prose.

The required raw-tail size depends on the measured fidelity of this deterministic checkpoint. V2 MUST NOT assume deterministic consolidation is equivalent to provider-native or model-assisted semantic compaction, but it also MUST NOT compensate for uncertainty by retaining an arbitrary 50%-60% of the high watermark. Section 12 calibrates the minimum raw tail that closes the observed fidelity gap. Model-assisted or provider-native compaction MAY be recorded as a future experiment, but it is not part of this revision.

### `CMV2-CHK-02` — Consolidation inputs

Every full or task-boundary checkpoint build MUST consume:

```text
previous Runtime checkpoint payload, if present
+ trajectory removed by the current rebase
+ authoritative current runtime state
```

The builder MUST parse its own prior deterministic JSON payload. Failure to parse a malformed historical checkpoint MUST be logged and the current runtime/removed trajectory MUST still produce a valid checkpoint; it MUST NOT introduce a secondary parser or compatibility format.

### `CMV2-CHK-03` — Required semantic fields

The consolidated payload MUST preserve bounded values for:

| Field | Required content |
| --- | --- |
| `current_task` | Current authoritative user task |
| `user_constraints` | Explicit requirements and prohibitions from the prior checkpoint plus removed user trajectory |
| `user_corrections` | User revisions/corrections that change earlier assumptions |
| `decisions` | Architectural/implementation decisions and their recorded rationale |
| `failures` | Important failed approaches, tool errors, validation failures, and why they matter |
| `findings` | Repository facts and conclusions still relevant to execution |
| `runtime_state` | Task status, changed/created files, mutation version, verification state |
| `plan` | Current plan phase/version, completed/current/pending work |
| `pending_work` | Work not yet completed or verified |
| `source_context` | Bounded path/SHA/range manifest for relevant source observations |
| `artifact_references` | Bounded artifact IDs needed to recover removed non-source results |

The payload MUST contain facts and location/recovery information, not copied source pages or large ToolResult bodies.

The deterministic builder MUST NOT pretend to infer semantic intent from free text. It uses these evidence rules:

- non-ToolResult user text is retained as bounded constraint/correction evidence in chronological order;
- assistant narrative text is retained as bounded decision/rationale/finding evidence in chronological order;
- ToolResults with `is_error=true`, verification failures, and recorded mutation failures populate failure/error evidence;
- current Plan/runtime objects populate plan, pending-work, changed-file, and verification fields;
- the previous checkpoint supplies already-consolidated semantic fields.

These broad evidence categories guarantee survival of explicit content without adding keyword classifiers or importance scoring. The checkpoint fidelity tests use explicit tagged facts so loss is deterministic and observable.

### `CMV2-CHK-04` — Merge precedence

Consolidation MUST apply this deterministic precedence:

1. authoritative current runtime and Plan state override stale prior-checkpoint state;
2. explicit user constraints/corrections from the removed trajectory append to prior values and are de-duplicated by normalized exact text;
3. decisions, rationale, findings, and failures append chronologically and are de-duplicated by normalized exact text;
4. current source/artifact manifests replace stale entries for the same identifier while preserving recoverable historical references;
5. completed work is removed from `pending_work` when current Plan/runtime state proves completion.

No relevance score, semantic classifier, TTL, or probabilistic selection is allowed.

### `CMV2-CHK-05` — Bounded truncation priority

`context_checkpoint_max_chars=12000` remains a hard character bound. If the consolidated payload exceeds it, the builder MUST reduce it in this order:

1. remove low-value raw assistant/user excerpts that are already represented in structured fields;
2. reduce completed-task history and routine successful ToolCalls;
3. trim oldest duplicated findings/decisions while keeping the newest form and an omitted-count field;
4. shorten individual strings using the existing deterministic clipping helper.

The builder MUST preserve at least one bounded entry, when present, for each of: user constraints, corrections, decisions/rationale, failures, findings, current Plan/pending work, changed files, and verification status. Omitted counts MUST reuse payload-local integer fields; they MUST NOT become new `AgentContext` metrics/state.

### `CMV2-CHK-06` — Repeated epochs

After any number of rebases, the next checkpoint is the consolidation of all prior checkpoint state plus newly removed trajectory under the bounded rules above. A new checkpoint MUST NOT reset semantic state to only the newest old prefix.

### `CMV2-CHK-07` — Determinism and immutability

For identical checkpoint input and runtime state, output bytes MUST be identical. The checkpoint is immutable until the next rebase. Serialization MUST remain sorted deterministic JSON under the existing Runtime checkpoint prefix.

### `CMV2-CHK-08` — Fidelity gate

Implementation is not conformant until the sequential-rebase fidelity test in §11.4 passes and the checkpoint-size/retained-tail benchmark in §12.2 confirms that semantic fidelity is not achieved by sacrificing the raw working set.

## 7.7 Exact commit semantics

An accepted rebase commit executes in this order:

1. verify artifact references used by the candidate;
2. assign the complete candidate list to `context.messages` once;
3. mark only source observations actually replaced by committed stubs;
4. increment `context_generation` once and invalidate the provider anchor;
5. increment the appropriate existing compaction/projection counters;
6. emit one context event with local before/after/savings;
7. return the final measurement.

It MUST NOT emit both a ToolResult-projection event and a full-compaction event for one pressure event.

## 7.8 Source recovery semantics

Source availability remains range-aware. Historical coverage and current residency are separate:

- historical coverage answers whether the task has seen a line range for a SHA;
- unprojected observation metadata answers whether the requested range is still hot;
- a projected requested range may be re-read even if another range of the same file remains resident;
- the rehydrated result is appended and becomes resident;
- a subsequent broad duplicate while that range remains resident is still rejected;
- an edit changes the SHA and invalidates old coverage/residency under the existing rules.

V2 MUST NOT introduce resident-range storage beyond the existing observation IDs, projected IDs, result metadata, and SHA-bound state.

## 7.9 Artifact semantics

ArtifactStore is authoritative cold storage for persisted non-source ToolResults. Persistence and Context residency are independent:

- a result may be persisted while a bounded preview remains hot;
- artifact creation does not imply eviction;
- source projection is not artifact persistence;
- `read_artifact` returns bounded append-only slices;
- artifact count is not a success target.

Known tradeoff: a projected source slice for an old SHA may not be reconstructible after the workspace file changes. V2 does not add source-version snapshots.

---

# 8. Config Spec

## 8.1 Removal

### `CMV2-CFG-01`

Remove `RunConfig.context_eager_projection_tokens` and its validation.

Rationale: V2 has one pressure trigger (`soft_limit_tokens`) and one rebase decision. Keeping an independent eager threshold would preserve the duplicate policy the specification removes.

### `CMV2-CFG-02`

Remove `AUTO_EAGER_HIGH_RATIO` and `EAGER_LOW_RATIO` from `context/manager.py`.

## 8.2 Retention/checkpoint calibration

Retention follows an explicit three-stage decision:

```text
SPEC CALIBRATION PHASE
    benchmark the closed profiles in §12.2
    keep all unrelated settings fixed

RETENTION DECISION
    select the smallest profile satisfying every acceptance gate
    record why smaller profiles failed and why larger profiles are unnecessary

FINAL SPEC FREEZE
    write the selected context_recent_target_tokens and
    context_recent_max_tokens into §1, §8, and §15
```

Until that decision is recorded, the current `12000/24000` defaults describe repository reality only; they are not the V2 recommendation. The former `136000/160000` proposal is the conservative upper calibration reference only and has no default priority.

`context_checkpoint_max_chars=12000` remains the provisional checkpoint candidate and is held constant across retention profiles so the benchmark isolates raw-tail effects. Its deterministic fidelity and bound MUST pass §11. If it fails, the Spec remains `REVIEW REQUIRED`; the implementation MUST NOT compensate silently by selecting a larger raw tail.

## 8.3 Unchanged defaults

The following MUST remain unchanged in the V2 implementation:

```text
max_turns = 40
max_tool_result_chars = 18000
max_tool_round_tokens = 12000
context_target_tokens = 272000
context_soft_limit_ratio = 0.8
context_min_recent_rounds = 2
```

## 8.4 Deployment requirement

When the real model window is known, deployment MUST set `MODEL_CONTEXT_WINDOW_TOKENS` to that value. The Runtime MUST NOT hardcode a model-name/window database or infer the window from provider/domain names.

For the current 1,000K-window deployment, `272000` remains the economic high watermark, while the configured 1,000K window remains the hard safety source.

## 8.5 New config fields

None.

---

# 9. Data Structure Spec

## 9.1 Removed state

| Field | Writer | Reader | Removal reason |
| --- | --- | --- | --- |
| `AgentContext.eager_projection_active` | `ContextManager.prepare_context()` | Same | Eager hysteresis is removed; no consumer remains |

## 9.2 Retained state

| Field | Purpose | Writer | Reader | Lifecycle |
| --- | --- | --- | --- | --- |
| `context_generation` | Identifies provider-visible historical epochs | Rebase/task boundary | Provider anchor, diagnostics | Session |
| `last_model_consumed_message_count` | Bounds results proven visible to the model | Agent loop | Rebase preflight | Until next request/rebase |
| `last_model_usage*` | Conservative provider pressure anchor | Agent context | Context measurement | Same generation only |
| `tool_result_artifacts` | ToolCall → artifact recovery mapping | large-output/admission/rebase persistence | stubs/checkpoint/read path | Run |
| `tool_result_metadata` | Bounded source reconstruction facts | post-tool tracking | projection/source recovery | Run, bounded |
| `observation_ids` / `projected_observation_ids` | Source range residency evidence | source tracking/rebase commit | read-file residency | Current source SHA state |
| `conversation_messages` | Append-only audit | Agent context append methods | reports/audit | Session |
| CostTracker previous message hashes | Detect exact prefix preservation without logging prompt text | CostTracker | next model-call diagnostic | In-memory run only |

## 9.3 New fields

No new `AgentContext`, RunConfig, per-message, metric, or provider fields.

The checkpoint JSON payload gains the structured semantic fields defined in `CMV2-CHK-03`. They are written by `RuntimeCheckpointBuilder.build()`, read by the next `build()` during consolidation, and live only in the immutable checkpoint message for that epoch. Each field participates directly in cross-epoch recovery; none is speculative runtime state.

Preflight candidate data MUST use local variables or immutable return values. It MUST NOT be added to `AgentContext` as a new state machine.

---

# 10. Migration / Deletion Plan

## 10.1 Production changes

### `src/runtime/config.py`

- Remove `context_eager_projection_tokens`.
- Remove its validation.
- Change the checkpoint bound and the two retention defaults only after §12 records a retention decision and this Spec is frozen again.

### `src/runtime/session.py`

- Remove `eager_projection_active` and task-reset assignments.
- Retain source observation/residency state unchanged.

### `src/runtime/context/manager.py`

- Remove the unused `summarizer` constructor argument when constructing `RuntimeCheckpointBuilder`.
- Remove eager watermark constants and `_eager_watermarks()`.
- Remove the eager projection branch from `prepare_context()`.
- Remove delayed round-budget enforcement from `prepare_context()`.
- Replace incremental pressure projection followed by full compaction with one preflight/commit rebase decision.
- Preserve provider normalization, local savings accounting, task-boundary compaction, complete-round grouping, full-compaction guards, and bounded overflow recovery.
- Ensure one pressure preparation emits at most one historical mutation event.

### `src/runtime/context/checkpoint.py`

- Remove the dormant optional summarizer and `_add_semantic_summary()` path.
- Parse the previous Runtime checkpoint produced by the same deterministic format.
- Consolidate previous checkpoint, removed trajectory, and current authoritative runtime state into the §7.6 schema.
- Apply deterministic merge, de-duplication, priority, omitted-count, and hard character-bound rules.
- Do not copy source bodies or large ToolResult content into checkpoints.

### `src/runtime/context/projection.py`

- Replace historical `enforce_round_budget(context)` with admission-only batch shaping.
- Delete or replace incremental `compact_consumed_results()` behavior.
- Implement a non-mutating ToolResult rebase candidate builder using newest-to-oldest API-round locality.
- Defer source projected markers and Context mutation until commit.
- Reuse existing artifact/source stub formats and `ArtifactStore`.

### `src/agent/loop.py`

- Invoke round-budget admission shaping after POST_TOOL hooks and before `AgentContext.add_tool_results()`.
- Append only the final admitted representation.
- Do not modify Plan, permission, progress, recovery, or task execution semantics.

## 10.2 Expected deletions

- eager high/low constants;
- eager state field/reset code;
- eager branch and event reason;
- `_eager_watermarks()`;
- delayed prepare-time round-budget call;
- old incremental consumed-result projection path that can commit before full compaction.
- dormant checkpoint `summarizer` constructor argument and `_add_semantic_summary()` hook.

## 10.3 Explicitly not added

- no `ContextEpoch` class;
- no new manager/policy abstraction;
- no provider cache config;
- no persistent rebase-candidate state;
- no compatibility switch preserving V1 and V2 behavior;
- no new production file.

## 10.4 Migration rule

V2 replaces V1 behavior directly. There is no `legacy_mode`, feature flag, safe mode, or dual policy. Existing configuration files that specify `context_eager_projection_tokens` must remove that field; silent acceptance is not required because this repository has no documented compatibility contract for that internal RunConfig argument.

---

# 11. Test Spec

## 11.1 Unit tests — admission

### `CMV2-TEST-ADM-01` Small result

Given a ToolResult batch below both character and round limits, admission returns byte-for-byte equivalent visible content, creates no artifact, and appends one message.

### `CMV2-TEST-ADM-02` Oversized single result

Given a non-source output above `max_tool_result_chars`, the first provider-visible form is bounded, the full redacted content exists in ArtifactStore, and `read_artifact` recovers it.

### `CMV2-TEST-ADM-03` Aggregate round limit

Given two individually valid ToolResults whose combined estimate exceeds 12K, the first provider request sees a batch at or below 12K. No later `prepare_context()` rewrite is required.

### `CMV2-TEST-ADM-04` Source last-resort victim

Given an active source result plus a large non-source result, admission shortens/persists the non-source result first. Given only oversized source results, it may create source stubs and marks only committed stub observations projected.

### `CMV2-TEST-ADM-05` Admission does not create an epoch

Admission shaping leaves `context_generation` unchanged and does not emit a historical projection/compaction event.

## 11.2 Unit tests — append-only epoch

### `CMV2-TEST-EPOCH-01`

Below the high watermark, capture object identity and deep content of every existing message, call `prepare_context()`, and assert:

- same message list identity or equivalent no-replacement behavior;
- same message identities/content;
- same `context_generation`;
- no source projected marker;
- no historical artifact created;
- no projection/compaction event.

### `CMV2-TEST-EPOCH-02`

Append several normal turns (`list_dir`, `grep`, `read_file`, test output) below pressure and assert every next request preserves the prior message sequence as an exact prefix.

## 11.3 Unit tests — normal pressure rebase

### `CMV2-TEST-REB-01` Atomic ToolResult rebase

Construct enough consumed old results for Candidate A to satisfy both the post-rebase ceiling and the benchmarked minimum-gain hypothesis. Assert one commit, one generation increment, one event, valid artifacts/stubs, and no full compaction.

### `CMV2-TEST-REB-02` Prefix-local candidate order

Create an early 1K `list_dir`, a long trajectory, and several newer large results. Assert the newer eligible rounds are selected first and the early `list_dir` remains unchanged when the target is reachable without it.

### `CMV2-TEST-REB-03` Projection insufficient

If Candidate A cannot reach the rebase target, assert it is not committed. Full checkpoint rebase is evaluated against the original messages, not a partially projected history.

### `CMV2-TEST-REB-04` One event

For one pressure call, assert exactly one of:

- ToolResult rebase event;
- full compaction event;
- skip event.

Never both projection and full compaction.

### `CMV2-TEST-REB-05` Minimum gain

If a normal-pressure candidate stays above `post_rebase_ceiling` or fails `minimum_rebase_gain_ratio`, assert no historical mutation.

## 11.4 Unit tests — full compaction

### `CMV2-TEST-FULL-01`

At genuine 272K pressure with insufficient projection candidates and enough eligible complete trajectory, run this test for every profile in §12.2. Full rebase preserves:

- one checkpoint no larger than 12K chars;
- complete recent API rounds;
- at least `context_min_recent_rounds`;
- the candidate's selected raw target, allowing only one complete indivisible boundary round above target and never exceeding its raw max;
- a total local candidate at or below 176.8K tokens.

Local tokens must decrease, `reclaim_ratio` must meet the current benchmark hypothesis, and no filler history may be retained merely because the ceiling has spare room.

### `CMV2-TEST-FULL-02`

If `local_after >= local_before`, full rebase is skipped.

### `CMV2-TEST-FULL-03`

An old prefix containing only a Runtime checkpoint is not compacted into another checkpoint.

### `CMV2-TEST-FULL-04`

Hard pressure/forced overflow may commit any positive-reduction full rebase even if the normal-pressure ceiling or meaningful-reclaim hypothesis cannot be met.

### `CMV2-TEST-FULL-05`

Assistant `tool_use` and user `tool_result` protocol pairs remain valid after boundary selection.

### `CMV2-TEST-FULL-06` Short history clamp

If less than the selected profile's raw target exists, retain all complete eligible recent trajectory that fits under the post-rebase ceiling. Do not insert filler, duplicate history, or cut a round merely to reach the target.

### `CMV2-TEST-FULL-07` Runway accounting

For every committed normal full rebase, derive from existing measurements and events:

```text
local_before
local_after
reclaimed_tokens
reclaim_ratio
post_rebase_runway
model_calls_until_next_rebase
```

Assert the arithmetic is exact and no new persistent runtime state is required. When both a smaller candidate and the conservative `136K/160K` reference are executed, report their runway difference; this comparison is informative and MUST NOT make R4 logically unable to pass its own gates.

### `CMV2-TEST-CHK-01` Production summarizer wiring

Assert `build_runtime()` does not depend on an optional semantic summarizer and that the removed `summarizer`/`_add_semantic_summary()` interface no longer exists.

### `CMV2-TEST-CHK-02` Sequential semantic consolidation

Perform at least three full rebases. Seed the first two removed trajectories with distinct:

- user constraints;
- user correction;
- architectural decision and rationale;
- repository finding;
- failed approach/error;
- changed file and verification state;
- pending Plan work;
- source and artifact recovery reference.

After the third rebase, parse the newest checkpoint and assert every required category survives under its bounded representation, current runtime/Plan state overrides stale values, and completed work is absent from pending work.

### `CMV2-TEST-CHK-03` Determinism and bound

Identical prior checkpoint, removed messages, and runtime state produce identical bytes. The output is at most 12K chars and exposes omitted counts when lower-priority entries are dropped.

### `CMV2-TEST-CHK-04` No source/result duplication

Checkpoint content contains recovery facts and bounded findings, but not copied source pages or large ToolResult bodies.

## 11.5 Unit tests — recovery

### `CMV2-TEST-REC-01` Artifact

After a non-source result becomes an artifact stub, `read_artifact` appends a new bounded ToolResult and leaves the old stub unchanged.

### `CMV2-TEST-REC-02` Source

After a source slice becomes a source stub, re-reading the exact range appends source content. A second broad read while the range remains resident triggers duplicate protection.

### `CMV2-TEST-REC-03` Partial range

Project page A while page B remains resident. Page A is rehydratable; page B remains duplicate-protected.

### `CMV2-TEST-REC-04` SHA invalidation

After file mutation changes SHA, old coverage/residency is not treated as current.

## 11.6 Unit tests — accounting and cache diagnostics

### `CMV2-TEST-ACC-01`

Cover no-cache, exclusive-cache, and inclusive/duplicated-cache provider usage. Pressure normalization stays close to local serialized estimate and does not automatically double Context pressure.

### `CMV2-TEST-ACC-02`

Saved tokens for admission/rebase/full compaction use only local before/after estimates.

### `CMV2-TEST-CACHE-01`

In one stable phase with append-only messages, system/tool hashes remain stable and `previous_messages_preserved=true` after the first call.

### `CMV2-TEST-CACHE-02`

An intentional rebase changes `context_generation` and sets `previous_messages_preserved=false` exactly once; subsequent append-only calls may stabilize again.

## 11.7 Long-horizon integration test

Add one deterministic 20–40-call trajectory containing:

- multiple `read_file` pages across several files;
- repeated return to an earlier source range;
- multiple `grep` calls;
- small and oversized Bash/test outputs;
- at least one round-admission shaping event;
- at least one genuine Context rebase;
- retention-boundary probes with required code/facts positioned approximately 24K, 48K, 64K, 96K, and 136K before the rebase boundary;
- artifact and source rehydration;
- final successful verification and task completion.

Acceptance:

- below pressure, historical prefix mutation count is zero, derived from existing prefix diagnostics;
- admission shaping is visible on the first request after the ToolCall;
- one pressure event causes one rebase;
- the post-rebase provider request retains only the candidate's selected raw working-set budget, without filling toward its raw max or the post-rebase ceiling;
- three sequential checkpoint consolidations preserve the §7.6 semantic categories;
- task succeeds;
- no Context overflow;
- repeated reads/searches are not higher than the V1 deterministic baseline without an explained recovery event;
- each boundary probe distinguishes a model-chosen reread from a Runtime-induced reread caused by removal of evidence still required by the fixture;
- the first profile that passes all gates establishes the point after which a larger raw tail provides no demonstrated task-level benefit.

No real provider cache is required for the deterministic integration test.

## 11.8 Required validation commands

Implementation validation MUST run:

```bash
pytest
ruff check .
ruff format --check .
```

If `ruff format --check .` fails only on documented pre-existing baseline files, the report MUST say so and list the result honestly. It MUST NOT claim the formatting check passed.

---

# 12. Benchmark Spec

## 12.1 Baseline and candidate isolation

Benchmark the same repository snapshot, task text, provider/model, permissions, Plan policy, tool schemas, and all non-Context settings.

Compare:

- **V1 Current**: delayed round budget plus eager/pressure projection;
- **V2**: admission round shaping plus append-only epoch and one pressure rebase.

Do not change `max_turns`, page sizes, output limits, round budget, Context target, or provider settings during the comparison. Retained-tail and checkpoint budgets may differ only in the isolated calibration matrix in §12.2.

## 12.2 Retained working-set calibration

The current `12K/24K` tail is not presumed sufficient, and the former `136K/160K` proposal is not presumed optimal. Before selecting production defaults, run this closed matrix with all unrelated settings fixed. The 12K checkpoint bound is held constant to isolate raw-tail retention:

| Candidate | Raw target | Raw max | Checkpoint max chars | Purpose |
| --- | ---: | ---: | ---: | --- |
| R0 — aggressive reference | 12K | 24K | 12K | Detect the failure mode of a clearly sub-64K tail |
| R1 | 32K | 64K | 12K | Small working-set candidate |
| R2 | 64K | 96K | 12K | Provisional 64K-level candidate |
| R3 | 96K | 128K | 12K | Medium retained set |
| R4 — conservative reference | 136K | 160K | 12K | Measure the cost and marginal benefit of the former proposed default |

The current production baseline (`12K/24K`, 6K checkpoint) remains in the before/after result table, but it is not mixed into the raw-tail isolation matrix.

Candidate execution and selection are deterministic:

```text
for candidate in [R0, R1, R2, R3, R4]:
    run deterministic long-horizon and sequential-checkpoint tests
    evaluate every acceptance gate
    if candidate fails:
        record the exact failing gate and continue
    else:
        selected_profile = candidate
        stop increasing retention
```

The selected profile is the first and smallest candidate satisfying all gates. A larger profile MUST NOT be chosen merely because it appears safer. Once a candidate passes, larger candidates are unnecessary unless the passing result is invalidated by an explicitly recorded benchmark defect. If no candidate passes, the Spec remains `REVIEW REQUIRED`; do not raise the high watermark or add adaptive policy.

Every candidate MUST satisfy:

1. task success does not regress;
2. all sequential semantic-checkpoint fidelity fixtures pass;
3. current Plan and pending work remain correct;
4. user constraints and corrections survive repeated epochs;
5. architectural decisions and recorded rationale survive;
6. important failures, errors, and current findings survive;
7. source and artifact recovery do not regress;
8. Runtime-induced `read_file` rehydration in the first post-rebase calls does not materially regress;
9. repeated `grep` activity does not materially regress;
10. model-call count does not materially regress;
11. no Context overflow regression occurs;
12. `post_rebase_runway` is positive and consistent with the meaningful-reclaim gate; when R4 is also executed, report the smaller candidate's additional runway;
13. actual cost or uncached input does not materially regress when provider semantics are observable.

A model choosing to reread for a new question is not a Runtime regression. A reread is Runtime-induced when the deterministic fixture requires evidence that was available before rebase, absent afterward, not preserved by the semantic checkpoint, and therefore must be fetched again before unchanged work can continue.

For every candidate derive, without adding persistent production state:

```text
local_before
local_after
reclaimed_tokens = local_before - local_after
reclaim_ratio = reclaimed_tokens / local_before
post_rebase_runway = high - local_after
model_calls_until_next_rebase
```

Also record actual retained raw-tail tokens, checkpoint fidelity after at least three sequential epochs, source/artifact recovery in the first three post-rebase calls, model calls, repeated reads/searches, and provider cost when available. These values come from existing messages, measurements, and event IDs; they are benchmark outputs, not a new metrics framework.

The calibration report MUST conclude:

```text
Selected profile = Rx
Why smaller candidates failed: ...
Why larger candidates are unnecessary: ...
```

Until that report exists, no retention profile is selected and implementation of the retention-default change is prohibited.

Use this calibration result shape:

| Candidate | Raw target/max | Local after | Reclaimed | Reclaim ratio | Runway | Task success | Model calls | Runtime-induced rereads | `grep` repeats | Artifact recovery | Checkpoint fidelity | Actual/uncached cost |
| --- | --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | ---: | --- | --- | ---: |
| R0 | | | | | | | | | | | | |
| R1 | | | | | | | | | | | | |
| R2 | | | | | | | | | | | | |
| R3 | | | | | | | | | | | | |
| R4 | | | | | | | | | | | | |

## 12.3 Required metrics

Use existing data only:

| Metric | Source / interpretation |
| --- | --- |
| Task success | Runtime task status |
| Model calls | Completed provider calls |
| Provider `input_tokens` | Raw provider field |
| Cache creation/read/deleted tokens | Raw provider fields |
| Uncached input / cache hit ratio | Report only when provider accounting semantics are known; otherwise `N/A` |
| Peak hot Context | Existing context measurements |
| Local before/after | Existing preflight and committed rebase measurements |
| Reclaimed tokens / ratio | Derived from local before/after |
| Post-rebase runway | Derived as `high - local_after` |
| Rebase count | Full plus ToolResult rebase events, clearly separated |
| Model calls between rebases | Derived from existing turn/event IDs |
| Tool calls / `read_file` / `grep` | Existing tool/source metrics |
| Rehydrated source lines | Existing source metric |
| Non-rehydration overlap | Existing source metric |
| Artifact reads and persisted characters | Existing ArtifactStore/tool metrics |
| Prefix stability | Existing hashes, `previous_messages_preserved`, and `context_generation` |
| Latency | Existing turn/model duration fields |
| Actual provider cost | Provider/billing data when available; otherwise `N/A` |

Do not add “memory value,” importance, reuse probability, or semantic relevance metrics.

## 12.4 Result table

| Metric | V1 Current | V2 | Change | Interpretation |
| --- | ---: | ---: | ---: | --- |
| Task success | | | | Must not regress |
| Model calls | | | | Lower is favorable, not mandatory |
| Provider input tokens | | | | Interpret with provider cache semantics |
| Cache read tokens | | | | Higher alone is not sufficient |
| Uncached input | | | | Primary cache-economics signal when valid |
| Peak hot Context | | | | Must remain provider-safe |
| Retained raw tail | | | | Must match the selected smallest sufficient profile without filler |
| Reclaimed tokens/ratio | | | | Normal rebase must satisfy the benchmarked meaningful-gain gate |
| Post-rebase runway | | | | Report against R4 when available; must agree with the meaningful-gain gate |
| Sequential checkpoint fidelity | | | | All required categories must survive |
| Prefix breaks | | | | Should occur only at lifecycle/rebase boundaries |
| Rebases | | | | Fewer meaningful events preferred |
| `read_file` calls | | | | Runtime-induced rereads should decline |
| Rehydrated lines | | | | Acceptable when pressure explains them |
| Non-rehydration overlap | | | | Should remain low |
| `grep` calls/repeats | | | | No pressure-induced loop |
| Artifact reads | | | | Recovery signal, not a target |
| Latency | | | | Must not materially regress without benefit |
| Actual cost | | | | Primary economic outcome when available |

## 12.5 Success criteria

V2 is accepted only if:

1. task success does not regress;
2. provider hard safety and overflow behavior do not regress;
3. normal epochs have no historical message mutation;
4. each pressure event performs at most one rebase;
5. uncached input or actual task cost improves, or remains statistically neutral while rebase/re-read stability improves;
6. model calls do not materially increase;
7. repeated source/search activity is explainable by model choice or explicit recovery, not Runtime churn;
8. all evicted content remains recoverable under the stated Artifact/source tradeoffs.
9. full rebase preserves the selected minimum sufficient raw working set rather than collapsing by default to 24K or filling toward 136K/160K;
10. repeated-epoch checkpoints preserve constraints, corrections, decisions/rationale, failures, findings, Plan, and pending work.
11. a normal full rebase satisfies both the post-rebase ceiling and the benchmarked meaningful-reclaim gate;
12. the selected profile is the first profile, ordered smallest to largest, that passes all acceptance gates.

A smaller average Context is not sufficient evidence. A larger average Context may be acceptable when cache reuse, calls, task success, latency, or actual cost improves.

## 12.6 Real-provider requirement

If a real provider is unavailable, report:

```text
Real provider regression not run.
```

Do not fabricate cache or cost results. Deterministic tests may prove prefix preservation but not provider cache hits.

---

# 13. Observability Spec

## 13.1 Retain

- raw provider usage fields;
- local Context measurements;
- local before/after/saved tokens for committed rebase events;
- existing artifact summary;
- existing source read/rehydration/overlap summary;
- current request-prefix fingerprints.

## 13.2 Remove or stop producing

- eager projection events and eager-active state;
- prepare-time round-budget projection events after admission is moved;
- duplicate events for one pressure action;
- any aggregate that treats raw provider cache fields as universally additive billing truth.

The admission round-budget event MAY retain the existing event type for report compatibility only if it clearly means “new batch shaped before first visibility.” It MUST NOT be counted as a historical rebase or full compaction.

## 13.3 Event terminology

| Event | Meaning |
| --- | --- |
| `tool_result_budget` | Admission shaping of the new ToolResult batch |
| `context_tool_results_projected` | One committed pressure-triggered ToolResult rebase |
| `context_compact` with `mode=full` | One committed full history rebase |
| `context_compact` with `mode=task_boundary` | Cross-task checkpoint boundary |
| `artifact_persisted` | Cold storage created; does not imply eviction by itself |
| Source rehydration event | New source evidence appended after a prior source projection |

Readable trace, cost JSON, and report output MUST use these meanings consistently.

---

# 14. Spec Self-Review

## 14.1 Is `136K/160K` still treated as correct without a benchmark?

No. It is R4, the conservative upper reference, with no default priority.

## 14.2 Is `post_rebase_ceiling` still treated as a target to fill?

No. It is a maximum request size. Candidate B stops at the selected sufficient budget and never adds filler.

## 14.3 Can full rebase release enough runway in one event?

Not yet proven for this repository. The independent 50% starting gain hypothesis requires decisive reclaim, but the R0-R4 benchmark must validate task continuity and actual calls until the next rebase. This unresolved item prevents freeze.

## 14.4 Can a rebase reclaim only a small amount of Context?

Not on the normal path: both the ceiling and meaningful-gain checks must pass. Hard pressure/overflow may accept any positive reduction because provider safety has priority.

## 14.5 Is selection based on the smallest sufficient candidate rather than the safest largest candidate?

Yes by specification: candidates run smallest-to-largest and selection stops at the first profile passing every gate. The selected profile itself remains unresolved until calibration.

## 14.6 Does this revision add complex runtime state?

No. It adds no production state, config field, manager, or persistent metric. Reclaim and runway values are derived from existing measurements/events.

## 14.7 Does it add dynamic importance or reuse prediction?

No. There is no per-message scoring, LRU, TTL, semantic classifier, or adaptive model.

## 14.8 Does it break the append-only epoch principle?

No. Normal epochs remain append-only; only an existing real-pressure rebase may rewrite history once.

## 14.9 Does it change admission design?

No. First-visibility shaping, round-budget admission, and source-last-resort behavior remain exactly as previously approved.

## 14.10 Can the final retained raw-tail value be explained?

Not yet. It will be the first R0-R4 profile passing all quality gates, but no profile has been selected. This unresolved item prevents freeze.

## 14.11 Can the Spec prove why smaller profiles failed?

Not until calibration. The benchmark must record each smaller profile's exact failing gate. This unresolved item prevents freeze.

## 14.12 Can the Spec explain why larger profiles are unnecessary?

The selection rule can: after the first profile passes all gates, a larger profile adds Context cost without a demonstrated requirement. The final report must apply that rule to actual results; until then the decision remains open.

Remaining deliberate tradeoffs are unchanged: historical source bodies may become unrecoverable after SHA change, provider cache hits are not guaranteed, and hard-safety admission/rebase may favor bounded execution over zero rehydration. Deterministic checkpoint consolidation preserves explicit evidence but does not infer unstated rationale. Model-assisted or provider-native compaction remains a future experiment, not a V2 requirement.

Because §14.3, §14.10, and §14.11 are unresolved, this Spec is not frozen and implementation must not begin.

---

# 15. Review Status

## 15.1 Review declaration

```text
SPEC STATUS: REVIEW REQUIRED
```

The admission, epoch, prefix-local rebase, atomicity, recovery, and deterministic-checkpoint architecture remains approved. Production implementation MUST NOT begin until:

1. the closed R0-R4 calibration is complete;
2. the smallest sufficient profile is recorded;
3. the meaningful-reclaim hypothesis is confirmed or explicitly revised;
4. the final retention defaults are written into this document; and
5. all §14 questions are resolved and the status is changed through review to `FROZEN`.

If repository or benchmark facts contradict this revision, publish:

```text
SPEC DEVIATION REQUIRED

- Incorrect original requirement
- Verified code fact
- Proposed correction
- Reason
- Impacted files/tests
```

The Spec must then be revised and reviewed before implementation begins.

The repository review baseline is the exact base commit and prerequisite overlay in §1.1. Calibration and any later implementation MUST verify that identity before relying on results.

## 15.2 Files expected to change

Production:

```text
src/runtime/config.py
src/runtime/session.py
src/runtime/context/manager.py
src/runtime/context/checkpoint.py
src/runtime/context/projection.py
src/agent/loop.py
```

Tests:

```text
tests/unit/runtime/context/test_manager.py
tests/unit/agent/test_loop.py
tests/integration/test_context_efficiency.py
tests/integration/test_security_boundary.py
```

No new production file is allowed.

## 15.3 Functions expected to change

```text
ContextManager.prepare_context
ContextManager._compact_history
ContextManager._finish_preparation              # only to maintain one-event/local-savings semantics
RuntimeCheckpointBuilder.build                  # previous-checkpoint + removed-trajectory consolidation
RuntimeCheckpointBuilder deterministic merge/truncation helpers
ToolResultProjector round-budget entry point     # converted to admission shaping
ToolResultProjector pressure rebase builder/commit path
AgentLoop.run_until_idle                         # call admission shaping before add_tool_results
RunConfig.__post_init__                          # remove eager-field validation
AgentContext task reset/initial state            # remove eager-active state
```

## 15.4 Functions/state expected to delete

```text
ContextManager._eager_watermarks
ContextManager eager projection branch
AUTO_EAGER_HIGH_RATIO
EAGER_LOW_RATIO
AgentContext.eager_projection_active
prepare-time delayed round-budget invocation
incremental compact_consumed_results commit path that may precede full compaction
ContextManager/RuntimeCheckpointBuilder summarizer constructor argument
RuntimeCheckpointBuilder._add_semantic_summary
```

## 15.5 Config expected to change

```text
Remove: context_eager_projection_tokens
Add: none
Rename: none
Provisional checkpoint candidate:
  context_checkpoint_max_chars 6000 → 12000
Retention defaults:
  context_recent_target_tokens 12000 → UNSELECTED
  context_recent_max_tokens 24000 → UNSELECTED
Selection source:
  first R0-R4 profile passing every §12 acceptance gate
```

The final two values MUST be replaced with exact integers in the retention decision before freeze. No new RunConfig field is permitted.

## 15.6 Tests expected to add/change

- first-visibility round-budget admission;
- append-only below pressure;
- atomic newest-first ToolResult rebase;
- no small-gain rebase;
- exactly one mutation/event per pressure call;
- full checkpoint fallback and hard overflow;
- parameterized R0-R4 retained raw-tail selection, no-filler behavior, and short-history clamp;
- independent post-rebase ceiling, meaningful reclaim, and runway arithmetic;
- three-epoch deterministic semantic checkpoint consolidation;
- checkpoint bound, field precedence, de-duplication, and omitted counts;
- artifact/source append-only recovery;
- range-aware source residency and SHA invalidation;
- provider cache-accounting normalization;
- prefix fingerprint stability;
- deterministic long-horizon integration trajectory.

## 15.7 Explicitly untouched areas

```text
Plan lifecycle and PlanController
Manual approval and Plan permissions
Permission Gate and filesystem safety
Task lifecycle and max_turns
Progress/recovery policies except existing Context overflow call site
Verification tracking
read_file page size, char size, pagination, duplicate guard, and SHA semantics
ArtifactStore architecture and on-disk format
ModelClient provider abstraction and explicit prompt-cache controls
Assistant tool-call history
Report/metrics framework beyond terminology alignment
```

## 15.8 Final engineering principle

```text
Persist aggressively where bounded recovery requires it.
Evict historical content conservatively.

Shape before first visibility.
Append throughout a normal epoch.
Rebase once under real pressure.

Preserve the longest useful prefix.
Recover by appending new evidence.

Optimize total task economics,
not the apparent size of one request.
```
