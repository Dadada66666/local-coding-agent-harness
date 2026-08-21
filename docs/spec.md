# Context Manager V3 Specification

## 1. Document Metadata

| Field | Value |
|---|---|
| Title | Context Manager V3 Specification |
| Version | 3.1.1 |
| Status | **FROZEN FOR IMPLEMENTATION** |
| Authority | This file is the sole normative Context Management specification for this repository. |
| Frozen local source | `c417e72acbbd7692f5ef589c45baf166017fb616` |
| Frozen reference source | `openai/codex@2151d3a5b78ca93128496b26333bc30187385a5f` |
| Review date | 2026-08-21 (Asia/Shanghai) |
| Supersedes | Context Manager V2 Specification |

This document governs every production change touching `src/runtime/context`, ToolResult admission, checkpointing, context configuration, history recovery, or context-related `AgentLoop` behavior. Every such change MUST cite one or more `CMV3-*` requirement IDs in code-review evidence.

There is no `SPEC DEVIATION REQUIRED` for this revision. The frozen design is implementable against the reviewed repository state. Implementation convenience does not authorize silent deviation.

Normative terms **MUST**, **MUST NOT**, **SHOULD**, and **MAY** have their usual requirements meaning. A benchmark may justify a later Spec revision; it does not alter this frozen version by itself.

---

## 2. Repository Reality Check

### 2.1 Local harness baseline

The reviewed workspace was clean before this document was revised.

| Item | Verified value |
|---|---|
| Repository | `https://github.com/Dadada66666/local-coding-agent-harness` |
| Local branch | `codex/context-v2-calibration` |
| Local HEAD | `c417e72acbbd7692f5ef589c45baf166017fb616` |
| Remote `main` HEAD | `b8e9f322253df8b1b9676ffde3fc6699a5441f79` |
| Tree relationship | GitHub comparison reported no changed files between the reviewed local tree and remote `main`. |
| Previous authority | This same `docs/spec.md`, CMV3 version 3.0, status `FROZEN FOR IMPLEMENTATION` |

The implementation facts below were verified directly in:

- `src/runtime/context/manager.py`
- `src/runtime/context/projection.py`
- `src/runtime/context/checkpoint.py`
- `src/runtime/context/budget.py`
- `src/runtime/config.py`
- `src/runtime/session.py`
- `src/runtime/session_factory.py`
- `src/agent/loop.py`
- `src/agent/model_client.py`
- `src/runtime/observability/*`
- related unit and integration tests

The current harness already has useful primitives that V3 reuses: admission-time ToolResult shaping, `ArtifactStore`, source path/SHA/range metadata, range-aware source rehydration, append-only `conversation_messages`, provider raw usage, `context_generation`, prompt-prefix fingerprints, task/plan state, verification state, mutation state, and tool-result provenance.

### 2.2 Codex reference baseline

| Item | Verified value |
|---|---|
| Repository | `https://github.com/openai/codex` |
| `main` HEAD | `2151d3a5b78ca93128496b26333bc30187385a5f` |
| Commit time | 2026-08-21T03:47:37Z |

The review followed the live call chain through these pinned sources:

- `codex-rs/core/src/context_manager/history.rs`
- `codex-rs/core/src/session/context_window.rs`
- `codex-rs/core/src/session/token_budget.rs`
- `codex-rs/core/src/session/turn.rs`
- `codex-rs/core/src/session/mod.rs`
- `codex-rs/core/src/compact.rs`
- `codex-rs/core/src/compact_remote.rs`
- `codex-rs/core/src/compact_remote_v2.rs`
- `codex-rs/core/src/compact_remote_v2_attempt.rs`
- `codex-rs/core/src/compact_remote_history.rs`
- `codex-rs/prompts/templates/compact/prompt.md`
- `codex-rs/protocol/src/openai_models.rs`
- `codex-rs/ext/history-notes/src/extension.rs`
- `codex-rs/ext/history-notes/src/tools.rs`

All Codex findings in this document refer to that exact commit, not to an assumed product contract.

---

## 3. Codex Source Findings

### 3.1 Tool output admission and normalization

`ContextManager::record_annotated_items` in `context_manager/history.rs` processes response items before recording them. `FunctionCallOutput` and `CustomToolCallOutput` payloads are normalized and truncated under the model truncation policy before they become stored prompt history. `for_prompt_annotated` constructs a prompt-safe clone, normalizes call/output relationships, and removes unsupported media.

The important architectural fact is that ordinary output bounding occurs at first admission. Codex does not depend on continuously rewriting already-visible old ToolResults by tool type, age, or predicted reuse.

### 3.2 Append-only normal history

Codex stores history oldest-to-newest in `Arc<Vec<ResponseItemEnvelope>>`. Normal conversation recording appends prepared items. `history_version` changes through replacement operations such as compaction, rollback, and explicit history replacement; normal sampling obtains a normalized prompt view without rewriting the live history.

`Session::replace_compacted_history` performs the compaction replacement as one operation, persists the replacement history and window metadata, then establishes the new window baseline. This provides the reference for V3's epoch boundary, not a mandate to copy Codex's provider protocol.

### 3.3 Auto-compaction pressure

Codex derives `auto_compact_token_limit` from model information, with a default 90% ratio. Its runtime distinguishes effective context window, auto-compaction scope, buffered auto limit, and the full usable-window safety boundary. `Total` and `BodyAfterPrefix` are model/provider configuration choices. Pre-sampling and mid-turn paths compact only when the configured limit or usable context window is reached, when a new window was explicitly requested, or when model compatibility requires a rollover.

V3 adopts the verified 90% pressure principle, not Codex's full model-configuration system.

### 3.4 Compaction forms

Codex has materially different compaction paths:

1. Local fallback compaction asks a model for a continuation handoff. It replaces history with newest selected real user messages under `COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000` plus the generated summary; it does not retain a raw assistant/tool tail.
2. Legacy remote compaction sends history to a provider compaction endpoint and installs the returned normalized replacement.
3. Remote V2 sends a `CompactionTrigger`, requires exactly one provider `Compaction` item, retains bounded selected messages, and atomically installs retained messages plus provider-produced opaque compact state.

Remote V2 uses `RETAINED_MESSAGE_TOKEN_BUDGET = 64_000`, selecting eligible groups newest-to-oldest. Eligibility covers user/developer/system messages and bounded selected agent messages, subject to client-developer retention rules; ordinary function calls and ToolResults are not a generic retained raw tail. Provider `ResponseItem::Compaction` carries opaque/encrypted state that this harness does not possess.

During a compaction request, Codex may trim or truncate a temporary cloned input to make that compaction request fit. That is not a normal-epoch historical ToolResult projection policy and does not justify Candidate A in this harness.

### 3.5 History windows and recovery

Codex advances an auto-compaction window identifier when compacted history is installed. Its History namespace exposes bounded read-only operations:

- `list_windows`
- `list_items`
- `read_item`
- `search_contents`

The reviewed limits are 100 windows, 20 items per listing, 2,000 preview characters per item, 20,000 read characters, 20 literal-search results, 1,000 query characters, and 10,000 result tokens. Notes are a separate read/write feature.

The reviewed Codex backend calls provider-hosted `alpha/history/v2/*` endpoints. It is not a local storage implementation that can be copied into this harness.

### 3.6 Why Codex can retain approximately 64K

Codex combines selected retained messages with provider-native opaque compact state and provider-hosted history recovery. Therefore its 64K retained-message budget does not imply that a deterministic 4K runtime record plus 64K raw messages is semantically equivalent.

The local equivalent frozen here is:

```text
up to 8K semantic handoff under a shared checkpoint budget
+ up to 4K authoritative deterministic state
+ checkpoint wrapper, all within 12,288 tokens
+ up to 64K recent complete raw rounds
+ read-only cold conversation history
+ ArtifactStore evidence
+ source path/SHA/range recovery
```

This is an independently testable local architecture, not an API-level clone.

---

## 4. What Codex Does Not Directly Solve For Us

Codex does not provide a portable implementation for any of the following:

- opaque `ResponseItem::Compaction` state on an Anthropic-compatible local harness;
- provider-hosted history endpoints over this repository's run files;
- local authoritative plan, permission, mutation, and verification checkpoint fields;
- the exact 272K/16K/4K capacity contract frozen here;
- this harness's source coverage and artifact identifiers;
- a provider-independent cache accounting convention.

V3 MUST NOT fabricate encrypted compact state, branch on provider name, or require a remote history service. It MUST use existing local runtime state, one low-frequency semantic handoff call, the append-only audit, and existing evidence recovery primitives.

---

## 5. Current Harness Problems

The frozen baseline has five overlapping or incomplete behaviors.

1. At normal token pressure, `ContextManager.prepare_context` first attempts a consumed ToolResult historical rebase through `_build_projection_candidate` and `_commit_projection`, then separately attempts full history rebase. One pressure event therefore has two competing history policies.
2. `compact_task_boundary` rewrites healthy history merely because a new task starts, and `AgentLoop.start_task` invokes it automatically.
3. Full rebase retains only the current `context_recent_target_tokens=12000` / `context_recent_max_tokens=24000` trajectory and a character-bounded deterministic checkpoint. This is not a specified long-task working set.
4. `RuntimeCheckpointBuilder` classifies user and assistant prose with string-position heuristics. It can place the same assistant narrative into both decisions and findings and cannot reliably distinguish confirmed, rejected, corrected, and unresolved knowledge.
5. The config combines `context_target_tokens`, `context_soft_limit_ratio`, a provider hard boundary, character fallback, recent target/max/min, and task-boundary thresholds. Their meanings overlap.

The baseline has no semantic compaction call and no model-facing way to recover old conversation trajectory after full rebase. `conversation_messages` already preserves the audit, but it is not queryable by the model.

---

## 6. V2 Mechanisms Being Removed

The V3 production implementation MUST remove, not disable behind compatibility switches:

- `ContextManager._build_projection_candidate`
- `ContextManager._commit_projection`
- `ToolResultRebaseCandidate`
- `ToolResultProjector.build_consumed_rebase_candidate`
- pressure-time consumed ToolResult historical projection
- `ContextPreparation.microcompacted`
- Candidate A-only projection counters and events, including `context_tool_results_projected`
- Candidate A-only artifact reason/counters for historical context projection
- `POST_REBASE_CEILING_RATIO` and `MINIMUM_REBASE_GAIN_RATIO`
- automatic `ContextManager.compact_task_boundary`
- the `AgentLoop.start_task` task-boundary compaction call
- the context-compaction failure circuit breaker and its config field
- deterministic checkpoint semantic classification heuristics
- CMV2 R0/R1/R2/R3/R4 recent-tail production profiles and tests

Admission-time `tool_result_budget` is not Candidate A and MUST remain.

---

## 7. V3 Goals

- **CMV3-INV-001:** Runtime MUST enforce provider context capacity and recoverability while leaving repository-reading strategy to the model.
- **CMV3-INV-002:** A normal context epoch MUST be append-only.
- **CMV3-INV-003:** One pressure event MUST commit at most one provider-visible history replacement.
- **CMV3-INV-004:** Every committed rebase MUST be atomic, recoverable, and produce meaningful headroom.
- **CMV3-INV-005:** Semantic continuity MUST survive repeated rebases through consolidation of the previous checkpoint and removed trajectory.
- **CMV3-INV-006:** Authoritative runtime state MUST remain deterministic and MUST NOT depend on an LLM's interpretation.
- **CMV3-INV-007:** Cold recovery MUST append evidence at the current tail and MUST NOT edit an old epoch.
- **CMV3-INV-008:** Admission shaping MUST occur before first provider visibility and MUST NOT increment `context_generation`.

---

## 8. V3 Non-Goals

V3 explicitly excludes:

- Candidate A or historical ToolResult microcompaction;
- eager projection or consumed-result projection;
- per-tool historical eviction;
- LRU, TTL, reuse prediction, semantic eviction score, or importance classification;
- embeddings, vector databases, RAG, or a general-purpose memory system;
- dynamic working-set injection;
- every-turn prompt reconstruction from a memory store;
- automatic task-boundary compaction;
- multiple successful rebases in one pressure event;
- assistant ToolCall history compression;
- historical source-version archives;
- model-writable Notes;
- provider-name branches or simulated encrypted compaction state;
- Plan, permission, task-lifecycle, or repository-strategy changes.

Any future proposal for these items requires a separate architecture revision.

---

## 9. Terminology

| Term | Definition |
|---|---|
| Hot Context | The current provider-visible system, tools, checkpoint, and message sequence. |
| Audit History | `conversation_messages`, the append-only in-session conversation source of truth. |
| Context Epoch | The interval after one successful rebase and before the next successful rebase. |
| Admission shaping | Bounding and persistence performed before a new ToolResult batch is first visible to a provider. |
| Historical mutation | Any change to provider-visible messages that a prior provider request already observed. |
| Pressure event | One `prepare_context` invocation that reaches auto or hard pressure, or one provider-overflow recovery invocation. |
| Full Rebase | One atomic replacement with a hybrid checkpoint plus recent complete raw rounds. |
| Hybrid checkpoint | Authoritative deterministic state plus an LLM-generated semantic handoff. |
| Recent raw tail | Newest complete API/model-tool rounds retained verbatim after rebase. |
| History window | The immutable audit interval associated with one `context_generation`. |
| Artifact evidence | Full redacted ToolResult content persisted by `ArtifactStore`. |
| Source evidence | Workspace content recoverable through path, SHA, and line range. |
| Recovery evidence | History, artifact, or source content read later and appended as a new ToolResult. |
| Local input tokens | Estimated tokens for the system prompt, serialized tool schemas, and provider-visible messages. Output reservations and safety margin are excluded. |
| Provider input tokens | Raw provider input usage normalized only for pressure anchoring; raw usage fields remain unchanged for cost and diagnostics. |
| Hard input limit | Maximum safe input-token usage for a call after subtracting that call's actual output cap and the safety margin from the context window. |
| Auto-compact trigger | Input-token pressure threshold at which automatic Full Rebase is attempted. |

---

## 10. Constants and Token Budget

### 10.1 Frozen production defaults

| Constant | Value | Semantics |
|---|---:|---|
| Model context window | 272,000 tokens | Total provider context capacity |
| Main-agent max output | 16,000 tokens | Existing production generation cap and default output reservation |
| Safety margin | 4,096 tokens | Non-negotiable provider safety reserve |
| Hard input limit | 251,904 tokens | `272000 - 16000 - 4096` |
| Auto-compact ratio | 0.90 | Single automatic pressure ratio |
| Auto-compact trigger | 244,800 tokens | `floor(272000 * 0.90)` |
| Semantic handoff maximum | 8,192 tokens | Upper bound before applying the shared-checkpoint calculation |
| Deterministic state max | 4,096 tokens | Authoritative checkpoint component cap |
| Combined checkpoint max | 12,288 tokens | Hard bound for the final serialized checkpoint, including wrapper |
| Recent raw maximum | 64,000 tokens | Upper bound for the final newest complete raw rounds |
| Post-rebase ceiling | 136,000 input tokens | Maximum provider-visible input context after successful rebase |
| ToolResult batch admission | 12,000 tokens | First-visibility aggregate hard budget |
| Per-result character bound | 18,000 chars | Existing admission/output serialization bound |

- **CMV3-CFG-001:** `context_window_tokens` MUST default to `272000` and represent model capacity, not an economic target.
- **CMV3-CFG-002:** The main request default `max_output_tokens` MUST remain `16000`; CMV3 MUST NOT alter normal agent generation behavior.
- **CMV3-CFG-003:** The hard input limit MUST always be recomputed as `window - actual_main_request_max_output - safety_margin`. The default equals `251904`; an explicit output override changes only this derived limit and MUST NOT change input-token measurement.
- **CMV3-CFG-004:** The auto trigger MUST be `min(floor(window * 0.90), hard_input_limit)`. The default equals `244800`.
- **CMV3-CFG-005:** Character limits MAY serve only as emergency serialization guards. Input tokens are the policy unit for pressure, rebase acceptance, post-rebase ceiling, and savings.
- **CMV3-CFG-008:** The final serialized hybrid checkpoint, including marker, labels, separators, deterministic state, semantic handoff, and closing notice, MUST be at most `12288` estimated tokens.
- **CMV3-CFG-009:** After deterministic state and the exact checkpoint wrapper are serialized, the semantic call limit MUST be `min(8192, 12288 - deterministic_actual_tokens - checkpoint_wrapper_tokens)`. The runtime MUST pass that computed value directly as the semantic call's `max_tokens` and MUST NOT generate 8,192 tokens and truncate afterward.
- **CMV3-CFG-010:** If the computed semantic allowance is smaller than the fixed mandatory-heading skeleton, checkpoint construction has failed. Normal and hard pressure MUST follow Section 26; deterministic state MUST NOT be truncated below its own authoritative rules to manufacture semantic space.

The semantic compaction call has a dynamically computed output cap no greater than 8,192 tokens and independent of the main agent's 16,000-token cap. The final serialization is measured again and rejected if it exceeds the shared 12,288-token hard bound. Output reservations and the safety margin participate only in deriving a call's safe input limit; they are never added to measured input usage. `SAFETY_MARGIN_TOKENS=4096` and `DETERMINISTIC_STATE_MAX_TOKENS=4096` are distinct constants with distinct accounting roles.

---

## 11. Context Epoch Model

- **CMV3-INV-009:** Within an epoch, `context.messages` MUST change only through tail append.
- **CMV3-INV-010:** Admission MUST finish before the admitted ToolResult message is appended to both `messages` and `conversation_messages`.
- **CMV3-INV-011:** The audit history MUST never be replaced by a rebase.
- **CMV3-INV-012:** A successful rebase closes the current history window, atomically replaces provider-visible history, increments generation once, and opens the next window.

Normal flow is:

```text
tool executes
  -> normalize and redact
  -> admission bounds and persistence
  -> append once
  -> normal append-only epoch
  -> measure before provider request
  -> below 244,800: no historical mutation
  -> at pressure: one full rebase
  -> new append-only epoch
```

A new task in the same session is a normal appended user/instruction boundary. Task change alone MUST NOT close an epoch. Full isolation requires an explicit new session or reset.

---

## 12. ToolResult Admission

- **CMV3-ADM-001:** Secret redaction and normalization MUST occur before content can be persisted or appended.
- **CMV3-ADM-002:** A single oversized non-source result MUST be persisted in `ArtifactStore` and replaced with a bounded preview/stub before first visibility.
- **CMV3-ADM-003:** `read_file` MUST retain its existing line pagination, character hard bound, long-line safety, SHA coverage, and exact `next_offset` contract.
- **CMV3-ADM-004:** The aggregate result batch MUST be at most 12,000 estimated tokens before it enters Hot Context.
- **CMV3-ADM-005:** Aggregate admission MAY prefer preserving source slices over reconstructible non-source results, but the 12,000-token hard budget wins when only source results remain.
- **CMV3-ADM-006:** Source admission stubs MUST retain path, SHA, returned range, and recovery instructions; they MUST NOT create generic source artifacts.
- **CMV3-ADM-007:** Admission MUST preserve every tool-call/tool-result identifier and error flag required by the provider protocol.
- **CMV3-ADM-008:** The unbounded pre-admission form MUST never be inserted into `messages` or `conversation_messages`.
- **CMV3-ADM-009:** Admission MUST NOT increment `context_generation` or emit a historical-rebase event.

The current POST_TOOL hook order remains conceptually valid: redaction, large-output persistence, budget observation, and trace. V3 MUST not move persistence ahead of redaction.

---

## 13. Context Measurement

- **CMV3-TRG-001:** `local_input_tokens` MUST include the system prompt, deterministic serialized tool schemas, and all provider-visible messages. It MUST NOT include the main-agent output reservation, semantic-compaction output reservation, or safety margin. Output reservation participates only in deriving the applicable hard input limit.
- **CMV3-TRG-002:** Raw provider usage fields MUST be retained unchanged for cost and diagnostics.
- **CMV3-TRG-003:** Provider cache fields MUST NOT be unconditionally added to `input_tokens` as context truth.
- **CMV3-TRG-004:** Provider pressure anchoring MUST evaluate both supported accounting interpretations, `input` and `input + cache_creation + cache_read`, choose the interpretation closest to `local_input_tokens` for the matching prior request, then add only known provider-visible input growth. Assistant output becomes input growth only after it is appended to history; an output reservation is never input growth.
- **CMV3-TRG-005:** A provider anchor is valid only for the same `context_generation` and the recorded matching response index. Rebase invalidates it.
- **CMV3-TRG-006:** Current pressure usage MUST be the conservative maximum of current `local_input_tokens` and normalized valid `provider_input_tokens`. Both operands are input-token quantities.
- **CMV3-TRG-007:** Savings MUST always be `local_input_tokens_before - local_input_tokens_after`. Provider usage, output reservation, and safety margin MUST NOT participate in saved-token arithmetic.
- **CMV3-TRG-014:** For a main-agent call, `hard_input_limit = context_window_tokens - actual_main_request_max_output - safety_margin_tokens`. Pressure compares normalized input usage against this limit. The actual output cap MUST NOT also be added to input usage. An explicit `max_output_tokens` override recomputes only `hard_input_limit`; it does not change the definition of `local_input_tokens`.

This normalization protects against exclusive-cache providers, inclusive-cache providers, and no-cache providers without model-, domain-, or provider-specific branches.

The default main-agent comparisons are therefore:

```text
local_input_tokens = estimate_input_tokens(system, messages, tools)
hard_input_limit = 272000 - 16000 - 4096 = 251904
auto_compact_trigger = min(floor(272000 * 0.90), 251904) = 244800

if pressure_input_tokens >= 251904:
    hard pressure
elif pressure_input_tokens >= 244800:
    automatic Full Rebase
```

`estimate_input_tokens` has input-only semantics. `pressure_input_tokens` is the conservative value required by `CMV3-TRG-006`; neither value includes output reservation or safety margin.

---

## 14. Auto-Compact Trigger

- **CMV3-TRG-008:** When input pressure usage is below the auto trigger, automatic preparation MUST be observational only.
- **CMV3-TRG-009:** When input pressure usage reaches or exceeds 244,800 under default capacity, preparation MUST attempt one Full Rebase.
- **CMV3-TRG-010:** Input pressure usage at or above the hard input limit, 251,904 under default capacity, is hard pressure and MUST use the hard-pressure failure policy.
- **CMV3-TRG-011:** Explicit provider context overflow is hard pressure regardless of the local estimate.
- **CMV3-TRG-012:** There is no economic target, eager threshold, tool-type cleanup threshold, or task-boundary threshold.
- **CMV3-TRG-013:** One pressure event MUST NOT fall through a sequence of projection, remeasurement, and compaction. It has one Full Rebase candidate and at most one commit.
- **CMV3-TRG-015:** `last_auto_compaction_failed_generation` MUST initialize to `None`. A failed or unavailable normal-pressure semantic attempt in generation N MUST set it to N. Later normal automatic pressure checks in generation N MUST skip semantic generation and leave history unchanged.
- **CMV3-TRG-016:** Hard pressure, provider overflow, and explicit/manual compaction MUST bypass the normal-failure generation guard. A successful Full Rebase advances generation, so normal automatic compaction is eligible again without clearing, counting, timing, or backing off the prior value. Explicit/manual failure below hard pressure does not authorize emergency replacement.

---

## 15. Full Rebase Algorithm

- **CMV3-RBS-001:** Rebase MUST begin from immutable snapshots of current provider-visible messages, audit boundaries, source observation metadata, and authoritative runtime state.
- **CMV3-RBS-002:** A semantic Full Rebase MUST build and serialize authoritative deterministic state before calculating semantic or raw-tail allowances. Deterministic emergency construction instead follows the reserved-budget algorithm in Section 26.2.
- **CMV3-RBS-003:** Static system/tool input cost, exact checkpoint-wrapper cost, deterministic actual tokens, and the reserved semantic allowance MUST be measured before selecting raw history.
- **CMV3-RBS-004:** The implementation MUST derive one final raw-tail allowance as `max(0, min(64000, 136000 - static_input_tokens - deterministic_actual_tokens - checkpoint_wrapper_tokens - semantic_actual_max))`.
- **CMV3-RBS-005:** The final newest complete raw-round set MUST be selected once under that allowance. It MUST NOT be enlarged, reduced, or otherwise changed after semantic input is finalized.
- **CMV3-RBS-006:** Removed trajectory MUST be the exact ordered complement of the final raw set within the provider-visible trajectory snapshot. The previous hybrid checkpoint is removed and consolidated, never retained as an independent raw checkpoint.
- **CMV3-RBS-007:** Deterministic state MUST never be weakened to retain more raw trajectory.
- **CMV3-RBS-008:** A candidate MUST satisfy all protocol invariants, have fewer input tokens than `snapshot.local_input_tokens`, and be at most 136,000 input tokens before commit.
- **CMV3-RBS-009:** Candidate construction, semantic generation, and validation MUST NOT mutate live history, source residency, generation, or counters.
- **CMV3-RBS-010:** Commit MUST atomically replace `context.messages`, mark only removed source observations as projected, close/open history windows, clear the provider anchor, and increment `context_generation` exactly once.
- **CMV3-RBS-011:** A successful commit MUST emit one rebase event. No second historical replacement is permitted in the same pressure event.
- **CMV3-RBS-012:** Rebase MUST NOT append a checkpoint whose removed prefix consists only of the current checkpoint and no new trajectory.
- **CMV3-RBS-026:** Every trajectory item removed by a successful Full Rebase MUST be represented by at least one of: semantic handoff, authoritative deterministic state where structurally applicable, or retained raw history. An emergency rebase satisfies structural coverage by recording exact removed history window/ordinal ranges in authoritative recovery state.
- **CMV3-RBS-027:** Every non-emergency removed trajectory item, including any round excluded to satisfy the 136K ceiling, MUST be included in the finalized semantic input. Post-summary raw-tail eviction is forbidden.
- **CMV3-RBS-028:** A successful semantic handoff and deterministic state MUST be serialized as one shared-budget hybrid checkpoint at the head of `[hybrid checkpoint, ...final raw rounds]`.
- **CMV3-RBS-029:** Ceiling enforcement MUST finish before semantic input is finalized. Final validation MUST reject rather than mutate an over-ceiling candidate.
- **CMV3-RBS-030:** The final serialized hybrid checkpoint MUST be measured as one value and MUST satisfy the 12,288-token shared hard bound; component maxima do not waive final measurement.

The 136,000-token post-rebase ceiling is input-only. It includes the system prompt, serialized tool schemas, hybrid checkpoint, and retained raw messages. It excludes the future main-agent output reservation and safety margin. A subsequent main-agent request still MUST satisfy its independently derived `hard_input_limit`.

The production algorithm is deterministic:

```text
snapshot = immutable_context_snapshot()
deterministic = build_authoritative_state(snapshot, max_tokens=4096)

static_input_tokens = estimate_input_tokens(
    system=system,
    messages=[],
    tools=tools,
)
wrapper_tokens = estimate_exact_checkpoint_wrapper_without_payloads()

semantic_actual_max = min(
    8192,
    12288 - deterministic.actual_tokens - wrapper_tokens,
)
require semantic_actual_max >= mandatory_semantic_skeleton_tokens

raw_capacity_by_ceiling = (
    136000
    - static_input_tokens
    - deterministic.actual_tokens
    - wrapper_tokens
    - semantic_actual_max
)
effective_raw_budget = max(0, min(64000, raw_capacity_by_ceiling))

final_raw = select_newest_complete_rounds(
    snapshot.provider_visible_trajectory,
    budget=effective_raw_budget,
)
removed = ordered_complement(snapshot.provider_visible_trajectory, final_raw)

semantic_input = build_semantic_input(
    previous_semantic_handoff=snapshot.previous_semantic_handoff,
    all_removed_trajectory=removed,
    authoritative_runtime_state=deterministic,
)
semantic_request_input_tokens = estimate_input_tokens(
    system=semantic_system,
    messages=semantic_input.messages,
    tools=[],
)
semantic_input_limit = (
    context_window_tokens
    - semantic_actual_max
    - safety_margin_tokens
)
require semantic_request_input_tokens <= semantic_input_limit
semantic = generate_semantic_handoff(
    semantic_input,
    max_tokens=semantic_actual_max,
)

checkpoint = serialize_checkpoint_v3(deterministic, semantic)
candidate = [checkpoint_as_synthetic_user_message, *final_raw]
candidate_input_tokens = estimate_input_tokens(
    system=system,
    messages=candidate,
    tools=tools,
)

require estimate_tokens(checkpoint) <= 12288
require candidate_input_tokens <= 136000
require candidate_input_tokens < snapshot.local_input_tokens
require protocol_rounds_are_complete(candidate)
atomic_commit_once(candidate)
```

`static_input_tokens`, `semantic_request_input_tokens`, `candidate_input_tokens`, and `snapshot.local_input_tokens` exclude output reservations and safety margin. Within provider-window safety accounting, the main-agent and semantic output caps are applied exactly once, only when deriving their respective safe input limits. `semantic_actual_max` separately reserves possible checkpoint payload space while selecting `final_raw`; that is checkpoint construction budgeting, not measured request input.

No final raw round is removed after `semantic_input` is built. An over-budget newest round is part of `removed`, is included in semantic input, and remains exactly recoverable through its audit item/window identifiers.

---

## 16. Semantic Handoff Generation

- **CMV3-SEM-001:** Semantic handoff generation occurs only during Full Rebase.
- **CMV3-SEM-002:** It MUST use one dedicated provider call whose `max_tokens` is the shared-budget `semantic_actual_max`, never a fixed unconditional 8,192.
- **CMV3-SEM-003:** The compaction call is a context-maintenance provider call, not an agent decision turn. It MUST be costed and traced separately and MUST NOT consume the agent's `max_turns` budget.
- **CMV3-SEM-004:** The compaction input MUST preserve and label the trust class of each input item according to the hierarchy below. External evidence MUST be delimited as data and MUST NOT be executed as instructions.
- **CMV3-SEM-005:** The prompt MUST request a coding-task continuation handoff, not a generic conversation summary.
- **CMV3-SEM-006:** The handoff MUST preserve user goals, constraints, corrections, decisions and rationale, findings, exact identifiers, paths, commands, errors, numeric values, failed approaches, verification state, unresolved questions, and next actions.
- **CMV3-SEM-007:** The handoff MUST contain the six mandatory headings defined below exactly once and in the specified order. A later user correction is current truth; the superseded claim may appear only in `REJECTED_OR_OBSOLETE`.
- **CMV3-SEM-008:** The handoff MUST NOT invent completed work or promote hypotheses and failed attempts to confirmed findings.
- **CMV3-SEM-009:** The previous semantic handoff and newly removed trajectory MUST be consolidated into one new handoff on every generation. Repeated epochs MUST not accumulate checkpoint layers.
- **CMV3-SEM-010:** Invalid semantic output as defined below is a compaction failure and MUST NOT be truncated, repaired by another model call, or silently committed under normal pressure.
- **CMV3-SEM-017:** Semantic-compaction usage MUST be costed separately and MUST NOT replace the main-agent usage record used as the next request's provider context anchor.
- **CMV3-SEM-018:** The semantic call MUST use the same resolved model, provider client, base URL, authentication, and transport configuration as the current main agent. V3 MUST NOT add a summary model, compaction model, or second provider configuration.
- **CMV3-SEM-019:** The semantic call MUST send `tools=[]` and MUST NOT expose repository or lifecycle tools.
- **CMV3-SEM-020:** The installed hybrid checkpoint MUST be one synthetic `role="user"` message using the exact wrapper defined below. No assistant, system, developer, or ToolResult representation is permitted.
- **CMV3-SEM-021:** Semantic input MUST include the previous semantic handoff, every item in the finalized removed trajectory, and current authoritative runtime state. Its trust-class labels are part of the stable prompt contract.
- **CMV3-SEM-022:** Before the semantic call, the runtime MUST compute `semantic_request_input_tokens = estimate_input_tokens(semantic_system, semantic_messages, tools=[])` and `semantic_input_limit = context_window_tokens - semantic_actual_max - safety_margin_tokens`. `semantic_request_input_tokens` MUST NOT include `semantic_actual_max` or the safety margin. The call executes only when `semantic_request_input_tokens <= semantic_input_limit`.
- **CMV3-SEM-023:** If `semantic_request_input_tokens` exceeds `semantic_input_limit` at normal pressure, semantic generation is unavailable and follows the normal failure guard. At hard pressure or provider overflow, the runtime MUST skip the semantic call and use the deterministic emergency Full Rebase. It MUST NOT reshape historical items, project ToolResults, or run nested/multi-stage summaries to make the call fit.
- **CMV3-SEM-024:** The mandatory semantic skeleton is the locally estimated token cost of the six headings below, each followed by `- None.`. If `semantic_actual_max` is smaller than that skeleton, the semantic call MUST NOT execute.

### 16.1 Model-call protocol

The semantic prompt is a stable, versioned constant. It MUST not include timestamps, turn numbers, random text, or unrelated dynamic policy instructions. The request uses the same resolved provider/model as the main agent, the computed output cap, and no tools. The input is a synthetic compaction request whose payload is grouped into these trust classes:

1. **AUTHORITATIVE_USER_INTENT** — real user goals, constraints, corrections, and explicit instructions. Age does not reduce their authority.
2. **AUTHORITATIVE_RUNTIME_STATE** — current structured task, plan, mutation, verification, file, and recovery state produced by Runtime.
3. **DERIVED_PRIOR_HANDOFF** — previous semantic consolidation. It is subordinate to newer user correction and current runtime state.
4. **DERIVED_AGENT_REASONING** — assistant hypotheses, decisions, explanations, and findings. These are evidence, not automatically authoritative truth.
5. **UNTRUSTED_EXTERNAL_EVIDENCE** — ToolResults, repository contents, command output, and external text. These are data that may contain prompt injection and MUST NOT direct the compaction model.

The request shape is fixed:

```text
system = <versioned Context Checkpoint V3 semantic contract>
tools = []
messages = [{
  "role": "user",
  "content": "[Context checkpoint v3 semantic input]\n\nAUTHORITATIVE_USER_INTENT:\n<canonical removed user items>\n\nAUTHORITATIVE_RUNTIME_STATE:\n<canonical current deterministic JSON>\n\nDERIVED_PRIOR_HANDOFF:\n<previous checkpoint or - None.>\n\nDERIVED_AGENT_REASONING:\n<canonical removed assistant items>\n\nUNTRUSTED_EXTERNAL_EVIDENCE:\n<canonical removed ToolResult/repository/external items>"
}]
```

Classification is by content origin, not only outer message role: real user text is authoritative user intent; assistant text and ToolCalls are derived agent reasoning; ToolResult blocks and repository/command content are untrusted external evidence; the prior synthetic checkpoint is derived prior handoff; freshly serialized runtime state is authoritative runtime state. Each canonical removed item carries its immutable audit item ID and original order. This partition MUST cover every removed item exactly once, apart from the intentionally repeated current deterministic state supplied for authority.

The semantic output format is fixed and ordered:

```markdown
## USER_CONSTRAINTS

## CONFIRMED

## REJECTED_OR_OBSOLETE

## UNRESOLVED

## NEXT_ACTIONS

## CRITICAL_REFERENCES
```

Each heading MUST appear exactly once in that order. A section with no content MUST contain `- None.`. Validation checks only serialization, text presence, token bound, and these headings; it MUST NOT parse or score semantic claims.

Semantic output is invalid when any one condition holds:

- the provider returns no text output;
- text is empty after whitespace trimming;
- local estimated output exceeds `semantic_actual_max`;
- any mandatory heading is missing, duplicated, or out of order;
- checkpoint serialization fails.

### 16.2 Installed checkpoint representation

The provider-visible checkpoint is exactly one synthetic user message with this content shape:

```text
[Context checkpoint v3]

AUTHORITATIVE_RUNTIME_STATE:
<compact deterministic JSON>

SEMANTIC_HANDOFF:
<validated six-section semantic output>

This is runtime-generated continuation context.
It is not a new user request.
```

The entire serialized text, including this wrapper, MUST satisfy the 12,288-token shared bound before it can be installed.

---

## 17. Deterministic Runtime State

- **CMV3-RBS-013:** The deterministic component MUST be generated solely from structured runtime state.
- **CMV3-RBS-014:** It MUST include current task and task status; plan phase, steps, statuses, and pending work; changed/created/deleted files; mutation state; unresolved mutation failure; verification status and exact latest command; artifact references; source path/SHA/range manifest; current generation; and history recovery identifiers.
- **CMV3-RBS-015:** It MUST NOT classify assistant prose into decisions/findings or infer user corrections through string position.
- **CMV3-RBS-016:** Its primary bound is 4,096 estimated tokens. Deterministic state MUST use canonical JSON with UTF-8 text, sorted keys, and compact separators. A character cap is allowed only as a larger emergency serializer guard.
- **CMV3-RBS-017:** Required scalar state and all active plan steps have priority over manifests. If the token bound is reached, manifests are deterministically truncated newest-first and include omitted counts.
- **CMV3-RBS-018:** Previous deterministic state MUST be superseded by the freshly generated state. It is not merged as semantic prose.

`RuntimeCheckpointBuilder` remains the deterministic builder but its semantic heuristic methods are removed. Semantic meaning belongs exclusively to the low-frequency handoff request.

---

## 18. Recent Raw Tail Selection

- **CMV3-RBS-019:** V3 has one raw-tail maximum: 64,000 estimated tokens, further reduced by the pre-summary 136K capacity calculation.
- **CMV3-RBS-020:** Selection proceeds newest-to-oldest by complete API/model-tool rounds.
- **CMV3-RBS-021:** A retained assistant tool call MUST retain all corresponding tool results. A retained tool result MUST retain its assistant tool call.
- **CMV3-RBS-022:** Plain user messages and assistant final/narrative messages are complete singleton protocol groups when they have no tool relationship.
- **CMV3-RBS-023:** No group may be split to hit the effective raw budget. If the newest complete group alone exceeds that budget, the final raw set is empty and the entire group belongs to removed trajectory and semantic input.
- **CMV3-RBS-024:** Ceiling enforcement occurs only through the pre-summary allowance calculation. No retained group may be evicted after semantic input is finalized.
- **CMV3-RBS-025:** Existing `_group_messages_by_api_round` behavior may be reused only after tests prove pairing completeness for parallel tool calls, errors, and continuation responses.

There are no `recent_target`, `recent_max`, minimum-round, or R0-R4 production profiles.

---

## 19. History Window Model

- **CMV3-HIS-001:** `conversation_messages` remains the append-only source of truth; V3 MUST NOT copy all history into a second memory database.
- **CMV3-HIS-002:** The initial window is generation 0. Each successful rebase closes the current window at the current audit ordinal and opens generation `N+1`.
- **CMV3-HIS-003:** A window ID MUST be stable and deterministic within the run, using `run_id` plus generation.
- **CMV3-HIS-004:** Each audit item MUST have a stable ID derived from `run_id` plus its immutable append ordinal.
- **CMV3-HIS-005:** Window metadata consists only of ID, generation, start ordinal, end ordinal, item count, and current/closed state.
- **CMV3-HIS-006:** Explicit reset or rollback opens a new window only when it actually replaces provider-visible history.
- **CMV3-HIS-007:** A task boundary does not create a history window by itself.

History windows are a bounded index over the existing audit, not a new semantic-memory subsystem.

---

## 20. History Recovery Tools

V3 MUST expose four read-only model tools backed directly by the current run's audit:

### 20.1 `history_list_windows`

- Input: optional `limit`, default 20, range 1-100.
- Output: newest-first bounded window metadata.

### 20.2 `history_list_items`

- Input: `window_id`, optional `after_item_id`, `limit` up to 20, `max_chars_per_item` up to 2,000.
- Output: chronological item IDs, roles/types, and bounded canonical previews.

### 20.3 `history_search_contents`

- Input: case-sensitive literal `query` up to 1,000 characters, optional `window_id`, `limit` up to 20.
- Output: matching item IDs, window IDs, and bounded snippets. It MUST NOT interpret regex or semantic similarity.

### 20.4 `history_read_item`

- Input: `item_id`, `offset_chars` at least 0, `limit_chars` up to 20,000.
- Output: canonical redacted content, exact returned character range, `next_offset`, and `complete`.

- **CMV3-HIS-008:** Each History tool result MUST be bounded to 10,000 estimated tokens before normal admission.
- **CMV3-HIS-009:** History tools MUST use standard ToolResult admission and standard permission/read-only capability handling.
- **CMV3-HIS-010:** A recovery result MUST append at the current tail through the ordinary tool-result path.
- **CMV3-HIS-011:** Recovery MUST NOT insert content at its historical location, edit a checkpoint, alter old audit items, or increment generation.
- **CMV3-HIS-012:** Missing or malformed IDs MUST return deterministic non-terminal tool errors.
- **CMV3-HIS-013:** History content MUST be the already-redacted canonical audit representation; recovery MUST not expose secret material that was removed before admission.
- **CMV3-HIS-014:** Production tool schema names are exactly `history_list_windows`, `history_list_items`, `history_search_contents`, and `history_read_item`. Bootstrap registration, read-only capabilities, prompts, traces, and tests MUST use those underscore names; dotted aliases are forbidden.

The implementation MUST use a small view/index over `AgentContext.conversation_messages`. It MUST NOT add a vector store, semantic index, remote dependency, or independent History Manager.

---

## 21. Artifact Recovery

- **CMV3-REC-001:** `ArtifactStore` remains the source of truth for complete redacted non-source ToolResult evidence that was persisted during admission.
- **CMV3-REC-002:** `read_artifact` remains the bounded recovery path and its result appends normally.
- **CMV3-REC-003:** Rebase MUST preserve referenced artifact IDs in deterministic state or retained raw history.
- **CMV3-REC-004:** Removing Candidate A removes context-projection artifact creation; admission-created artifacts remain unchanged.
- **CMV3-REC-005:** Artifact count is not a success target. Missing artifacts are errors only when an existing referenced artifact cannot be read.

---

## 22. Source Recovery

- **CMV3-REC-006:** Workspace file plus current SHA and line range remains the source recovery mechanism.
- **CMV3-REC-007:** Full rebase MUST mark only source observations removed from Hot Context as projected.
- **CMV3-REC-008:** Range-aware residency and rehydration MUST remain exact: a projected requested range may be read again even when another range from the same file remains resident.
- **CMV3-REC-009:** SHA change MUST invalidate old source coverage/residency under the existing semantics.
- **CMV3-REC-010:** Source recovery MUST remain bounded by read pagination and character limits and MUST append the recovered slice at the current tail.
- **CMV3-REC-011:** V3 MUST NOT persist every source page as an artifact or introduce historical source snapshots.

If a projected old-SHA source version is later edited, it is no longer reconstructible as current workspace source. A bounded page already present in audit history remains readable as a historical conversation item and MUST retain its historical SHA label; V3 does not create a separate source-version archive or treat that audit evidence as current source.

---

## 23. Provider Overflow Recovery

- **CMV3-REC-012:** A provider context-overflow response MUST bypass all non-full strategies and invoke one forced Full Rebase.
- **CMV3-REC-013:** The provider request MAY be retried once after a successful forced rebase. A second overflow terminates through the existing safe failure path.
- **CMV3-REC-014:** A forced rebase still MUST preserve protocol pairing, deterministic state, and audit history.
- **CMV3-REC-015:** Overflow recovery count remains bounded by the existing value `1`; no new recovery state machine is introduced.

---

## 24. Prompt Prefix / Cache Invariants

- **CMV3-INV-CACHE-001:** Within one epoch, the prior provider-visible message sequence MUST be the exact prefix of the next request's message sequence, followed only by new tail items.
- **CMV3-INV-CACHE-002:** Admission shaping is not a prefix break because it occurs before first visibility.
- **CMV3-INV-CACHE-003:** The only allowed history prefix breaks are successful Full Rebase, forced Full Rebase, and existing explicit reset/rollback operations that replace history.
- **CMV3-INV-CACHE-004:** Required plan-phase system/tool capability changes remain allowed and are diagnosed separately from history mutation.
- **CMV3-INV-CACHE-005:** History, artifact, and source recovery MUST append and therefore MUST preserve the current epoch prefix.
- **CMV3-INV-CACHE-006:** System prompt construction, tool ordering, and schema serialization MUST remain deterministic within a stable lifecycle phase.
- **CMV3-INV-CACHE-007:** Prefix diagnostics MUST continue recording system hash, tools hash, previous-message-prefix preservation, and context generation without logging full prompts.

V3 optimizes prefix stability but does not promise provider cache hits. A stable prefix with zero cache-read usage is reported as provider-side behavior unknown, not a ContextManager fault.

---

## 25. `context_generation` Semantics

- **CMV3-INV-013:** Generation starts at zero.
- **CMV3-INV-014:** Normal user, assistant, ToolCall, ToolResult, recovery, task, and runtime-tail appends do not change generation.
- **CMV3-INV-015:** Admission shaping does not change generation.
- **CMV3-INV-016:** Each successful Full Rebase increments generation exactly once.
- **CMV3-INV-017:** Failed or rejected candidates do not change generation.
- **CMV3-INV-018:** Generation change invalidates the provider usage anchor and makes `previous_messages_preserved=false` for the first request after rebase.

Generation represents provider-visible history epochs. It is not a task, plan, source, or artifact version.

---

## 26. Failure Semantics

### 26.1 Normal pressure

- **CMV3-SEM-011:** If semantic preflight, transport, output validation, shared-budget construction, or final candidate validation fails while usage remains below the hard input limit, live history MUST remain unchanged, generation MUST remain unchanged, and one failure event MUST be logged.
- **CMV3-SEM-012:** The failed normal automatic attempt MUST set `last_auto_compaction_failed_generation` to the current generation. The current main-agent request MAY proceed with the original context while it remains below the 251,904 default hard limit.
- **CMV3-SEM-025:** While `last_auto_compaction_failed_generation == context_generation`, later normal automatic pressure checks MUST perform no semantic call and no historical mutation. The original failure event records that suppression is active; repeated guarded checks MUST NOT emit duplicate failure events.

### 26.2 Hard pressure or provider overflow

- **CMV3-SEM-013:** Hard pressure and provider overflow MUST ignore `last_auto_compaction_failed_generation`. If semantic request preflight fits, they attempt one semantic call; if preflight does not fit, they skip that call and immediately build the deterministic emergency Full Rebase.
- **CMV3-SEM-026:** If a hard-pressure semantic call, output validation, shared-checkpoint construction, or semantic candidate validation fails, the implementation MUST build a deterministic emergency checkpoint and retain newest complete raw rounds under the same 136K ceiling.
- **CMV3-SEM-014:** The emergency checkpoint MUST preserve authoritative state and recovery identifiers and MUST not run semantic string heuristics.
- **CMV3-SEM-015:** Emergency fallback is the sole committed rebase for that pressure event. Its authoritative recovery manifest MUST cover every removed audit window/ordinal range so all omitted trajectory remains addressable through History tools.
- **CMV3-SEM-016:** If even deterministic state plus static prefix cannot fit provider safety, the runtime MUST terminate safely with a context-overflow reason and MUST not commit a malformed history.
- **CMV3-SEM-028:** Emergency construction MUST reserve the full 4,096-token deterministic maximum before selecting a final complete raw tail under the 64K/136K input-token limits, then build authoritative state with exact removed window/ordinal ranges inside that reserve. The fixed `SEMANTIC_HANDOFF` payload is `Unavailable after hard-pressure compaction failure; recover removed trajectory using the authoritative History ranges.` It uses the same synthetic-user wrapper and is not validated as model semantic output.
- **CMV3-SEM-029:** Deterministic emergency raw selection MUST use the exact calculation below. Static prefix, the full deterministic reserve, exact wrapper cost, and fixed-payload cost MUST all reduce capacity before complete raw rounds are selected. No alternative emergency profile or post-selection tail eviction is permitted.

The deterministic emergency algorithm is:

```text
EMERGENCY_DETERMINISTIC_RESERVE = 4096
fixed_semantic_payload = (
    "Unavailable after hard-pressure compaction failure; recover removed "
    "trajectory using the authoritative History ranges."
)

snapshot = immutable_context_snapshot()
static_input_tokens = estimate_input_tokens(
    system=system,
    messages=[],
    tools=tools,
)
emergency_wrapper_tokens = estimate_exact_checkpoint_wrapper_without_payloads()
fixed_payload_tokens = estimate_tokens(fixed_semantic_payload)

emergency_raw_capacity = (
    136000
    - static_input_tokens
    - EMERGENCY_DETERMINISTIC_RESERVE
    - emergency_wrapper_tokens
    - fixed_payload_tokens
)
effective_emergency_raw_budget = max(
    0,
    min(64000, emergency_raw_capacity),
)

final_raw = select_newest_complete_rounds(
    snapshot.provider_visible_trajectory,
    budget=effective_emergency_raw_budget,
)
removed = ordered_complement(snapshot.provider_visible_trajectory, final_raw)
removed_history_ranges = exact_window_and_ordinal_ranges(removed)

deterministic = build_authoritative_state(
    snapshot,
    required_removed_history_ranges=removed_history_ranges,
    max_tokens=EMERGENCY_DETERMINISTIC_RESERVE,
)
require deterministic.actual_tokens <= EMERGENCY_DETERMINISTIC_RESERVE
require deterministic.covers_exactly(removed_history_ranges)

checkpoint = serialize_checkpoint_v3(
    deterministic,
    semantic_handoff=fixed_semantic_payload,
)
emergency_candidate = [checkpoint_as_synthetic_user_message, *final_raw]
emergency_candidate_input_tokens = estimate_input_tokens(
    system=system,
    messages=emergency_candidate,
    tools=tools,
)

require estimate_tokens(checkpoint) <= 12288
require emergency_candidate_input_tokens <= 136000
require emergency_candidate_input_tokens < snapshot.local_input_tokens
require protocol_rounds_are_complete(emergency_candidate)
atomic_commit_once(emergency_candidate)
```

The reserve is intentionally used for raw-tail selection before the actual deterministic payload is built. Unused deterministic reserve does not enlarge `final_raw`. If exact recovery ranges or the final candidate cannot satisfy these bounds, emergency construction fails safely and MUST NOT commit malformed history. This path has no semantic call, alternate raw budget, retry, projection, nested summary, or second fallback.

There is no Candidate A fallback, projection fallback, repeated compact loop, failure count, timer, backoff, or configurable circuit breaker. The single optional integer marker implements one normal semantic attempt per generation; generation advancement after any successful Full Rebase restores automatic eligibility by inequality.

### 26.3 Explicit/manual compaction below hard pressure

- **CMV3-SEM-027:** Explicit/manual compaction MUST bypass the generation guard and attempt semantic generation when preflight fits. If preflight or semantic generation fails below hard pressure, it MUST perform no commit, leave generation and `last_auto_compaction_failed_generation` unchanged, and return the bounded failure through the existing explicit operation path.

---

## 27. Configuration Migration

| Old field or constant | V3 status | V3 field/value | Migration |
|---|---|---|---|
| `context_window_tokens=None` | KEEP field, change default | `context_window_tokens=272000` | Explicit smaller provider window continues to take precedence. |
| `context_target_tokens=272000` | REMOVE | none | Capacity is expressed by `context_window_tokens`; there is no economic target. |
| `context_soft_limit_ratio=0.8` | REMOVE | `context_auto_compact_ratio=0.90` | One pressure ratio only. |
| `compact_threshold_chars` | REMOVE | none | Token measurement is authoritative with a known default window. |
| `context_eager_projection_tokens` | ALREADY REMOVED | none | Must not return. |
| `context_recent_target_tokens` | REMOVE | `context_recent_raw_tokens=64000` | Single raw-tail policy. |
| `context_recent_max_tokens` | REMOVE | `context_recent_raw_tokens=64000` | Single raw-tail policy. |
| `context_min_recent_rounds` | REMOVE | complete-round selection | Protocol completeness replaces a numeric minimum. |
| `context_checkpoint_max_chars=6000` | REMOVE | token budgets below | Character count is not policy. |
| none | NEW | `semantic_checkpoint_max_tokens=8192` | Upper bound; each call uses the smaller shared-budget `semantic_actual_max`. |
| none | NEW | `deterministic_checkpoint_max_tokens=4096` | Deterministic component limit. |
| none | DERIVED | combined checkpoint `12288` | Hard final serialized bound including wrapper; do not add a redundant field. |
| `context_task_boundary_tokens=12000` | REMOVE | none | Automatic task-boundary replacement is deleted. |
| none | NEW | `context_post_rebase_ceiling_tokens=136000` | Provider-visible input-context candidate acceptance ceiling. |
| `max_context_compaction_failures=3` | REMOVE | none | No circuit-breaker policy. |
| `AgentContext.context_compaction_failures` | REMOVE | `last_auto_compaction_failed_generation` | One optional runtime generation marker; no count or config. |
| `max_context_recovery_attempts=1` | KEEP | `1` | One provider-overflow retry. |
| `context_safety_margin_tokens=4096` | KEEP | `4096` | Hard safety reserve. |
| `max_tool_round_tokens=12000` | KEEP | `12000` | Admission hard budget. |
| `max_tool_result_chars=18000` | KEEP | `18000` | Existing bounded ToolResult serialization. |
| `ModelClient.DEFAULT_MAX_TOKENS=16000` | KEEP | `16000` | CMV3 does not change main-agent generation; semantic calls pass computed per-call limits up to 8192. |
| `POST_REBASE_CEILING_RATIO=0.65` | REMOVE | fixed `136000` input tokens | One comprehensible ceiling. |
| `MINIMUM_REBASE_GAIN_RATIO=0.50` | REMOVE | strict decrease + 136K ceiling | No competing acceptance heuristic. |

- **CMV3-CFG-006:** Removed fields MUST be deleted from `RunConfig`, validation, environment wiring, tests, and reports in the same production migration.
- **CMV3-CFG-007:** V3 MUST NOT retain aliases, compatibility modes, or hidden legacy defaults for removed policy fields.
- **CMV3-CFG-011:** `last_auto_compaction_failed_generation` is runtime state initialized to `None`; it MUST NOT be exposed as a `RunConfig` field or accompanied by retry-count, time, backoff, or provider-specific configuration.

---

## 28. Code Removal Inventory

Production implementation removes these current symbols or responsibilities:

| File | Removal |
|---|---|
| `src/runtime/context/manager.py` | Candidate A imports, constants, build/commit methods, `microcompacted`, task-boundary method, recent target/max/min selection, compaction circuit breaker |
| `src/runtime/context/projection.py` | `ToolResultRebaseCandidate`, `build_consumed_rebase_candidate`, historical projection-only helpers; retain admission shaping and source stub helpers used by admission/rebase |
| `src/runtime/context/checkpoint.py` | prose classifiers, assistant narrative decision/finding duplication, old checkpoint semantic merge heuristics, character-first policy |
| `src/agent/loop.py` | automatic task-boundary compaction call and Candidate A trace fields |
| `src/runtime/config.py` | fields marked REMOVE in the migration table |
| `src/runtime/session.py` | Remove `context_compaction_failures`; replace it only with the unconfigured `last_auto_compaction_failed_generation` marker required by `CMV3-TRG-015..016` |
| `src/runtime/observability/*` | `context_tool_results_projected`, `microcompacted`, context-projection-artifact aggregates, and duplicate fields that exist solely for Candidate A |

Tests whose purpose is Candidate A, R0-R4 calibration, task-boundary compaction, or the failure circuit breaker MUST be deleted or replaced by V3 requirements. Admission projection tests remain.

---

## 29. Required Code Changes by File

| File | Required V3 change | Requirement mapping |
|---|---|---|
| `src/runtime/config.py` | Apply the migration table and frozen defaults. | `CMV3-CFG-*` |
| `src/runtime/context/budget.py` | Implement single trigger/hard-limit semantics and preserve normalized provider anchoring. | `CMV3-TRG-*` |
| `src/runtime/context/manager.py` | Replace Candidate A/full branching with one atomic Full Rebase pipeline; derive the final raw set before semantic input, enforce input-only candidate accounting and the shared checkpoint budget, and commit once. | `CMV3-RBS-*`, `CMV3-SEM-022..029` |
| `src/runtime/context/checkpoint.py` | Emit token-bounded authoritative state; implement the exact synthetic-user wrapper, trust-labelled semantic prompt, fixed headings, and simple validator. | `CMV3-SEM-*`, `CMV3-RBS-013..018` |
| `src/runtime/context/projection.py` | Retain first-visibility admission; remove historical Candidate A construction. | `CMV3-ADM-*` |
| `src/runtime/session.py` | Maintain stable audit item/window boundaries, atomic generation, and the one-value normal semantic failure guard. | `CMV3-HIS-*`, `CMV3-INV-013..018`, `CMV3-TRG-015..016` |
| `src/runtime/session_factory.py` | Initialize generation-0 history window and 272K default capacity. | `CMV3-CFG-001`, `CMV3-HIS-002` |
| `src/agent/model_client.py` | Keep the 16K main default; accept a per-call semantic output cap on the same resolved client/model; preserve raw usage and isolate semantic usage from the main anchor. | `CMV3-CFG-002..003`, `CMV3-SEM-002`, `CMV3-SEM-017..019` |
| `src/agent/loop.py` | Remove automatic task-boundary compaction; invoke one prepare/rebase; use forced Full Rebase for one overflow retry. | `CMV3-TRG-*`, `CMV3-REC-012..015` |
| `src/tools/history.py` | Implement exactly `history_list_windows`, `history_list_items`, `history_search_contents`, and `history_read_item` as bounded read-only audit views without a manager/database. | `CMV3-HIS-008..014` |
| `src/runtime/bootstrap.py` | Register the four underscore-named History tools. | `CMV3-HIS-008..014` |
| `src/runtime/plan/capabilities.py` | Add the four underscore-named History tools to read-only inspection capabilities without changing lifecycle semantics. | `CMV3-HIS-009`, `CMV3-HIS-014` |
| `src/runtime/observability/*` | Remove Candidate A reporting and retain minimal rebase/admission/prefix evidence. | Section 30 |

The History tool module is a bounded Tool API adapter, not a Context Manager, store, or memory subsystem.

---

## 30. Observability

V3 observability is intentionally small.

- **CMV3-INV-019:** Every model request MUST retain raw provider token usage and existing prefix fingerprints.
- **CMV3-INV-020:** Admission MUST emit the existing bounded `tool_result_budget` event only when it changes the new batch.
- **CMV3-INV-021:** One committed Full Rebase MUST emit one event containing reason (`auto`, `hard`, `provider_overflow`, or `explicit`), generation before/after, `local_input_tokens` before/after, final raw input tokens/rounds, deterministic/wrapper/semantic/final-checkpoint tokens, and emergency-fallback boolean.
- **CMV3-INV-022:** A semantic failure MUST emit one failure event without copying prompts or history content. A normal failure event MUST include `generation` and `auto_suppressed_for_generation`; later guarded automatic checks in that generation MUST not repeat the event.
- **CMV3-INV-023:** History recovery is observed through ordinary tool-call/result traces; no parallel recovery metrics framework is added.
- **CMV3-INV-024:** Reports MUST distinguish admission shaping from Full Rebase and MUST not call admission `compaction`.
- **CMV3-INV-025:** Semantic request preflight failure MUST be distinguishable from provider-call failure, malformed output, shared-checkpoint overflow, and final-candidate rejection using one bounded reason field, not separate metric families.
- **CMV3-INV-026:** Hard/overflow/explicit bypass of the normal generation guard MUST be visible in the single rebase/failure event; no retry counters, timers, or backoff metrics are added.

The following are removed as redundant production aggregates: historical ToolResult projection events/counts, microcompaction status, and context-projection artifact counts. Existing artifact totals, source read/rehydration metrics, provider usage, context generation, full rebase count, and prefix diagnostics remain.

Benchmark reports may calculate additional values from trace data; they do not require persistent runtime fields.

---

## 31. Test Plan

### 31.1 Deterministic unit and integration tests

These tests use a fake semantic provider with exact scripted text. They prove Runtime contracts, not LLM semantic quality.

1. **CMV3-TST-001 — Append-only below trigger.** Build repeated calls below 244,800. Assert every prior provider-visible message is byte-for-byte the prefix of the next request and generation remains zero.
2. **CMV3-TST-002 — Admission hard bound.** Create a result batch over 12,000 tokens. Assert shaping occurs before both Hot Context and audit append, the unbounded form never appears, and generation is unchanged.
3. **CMV3-TST-003 — One full rebase.** Reach 244,800. Assert exactly one Full Rebase event, no historical projection path, and one history assignment.
4. **CMV3-TST-004 — Generation once.** Assert a successful rebase changes generation from N to N+1 exactly once; preflight and admission do not change it.
5. **CMV3-TST-005 — Complete final rounds.** Use parallel tool calls, success/error results, and continuations. Assert no orphan call/result and no group splitting.
6. **CMV3-TST-006 — Final raw allowance.** Assert the final raw set is selected newest-first under `min(64000, raw_capacity_by_ceiling)` before semantic input is built.
7. **CMV3-TST-007 — Removed-trajectory coverage.** Record the finalized semantic input, then build the candidate. Assert every non-retained item is in semantic input, no retained round changes afterward, and the candidate is at most 136,000.
8. **CMV3-TST-008 — User-correction input contract.** Provide A followed by a real user correction to B. Assert both are present under `AUTHORITATIVE_USER_INTENT`, later ordering is preserved, and a scripted valid handoff is installed without Runtime reclassification.
9. **CMV3-TST-009 — Failed-hypothesis input contract.** Provide an assistant failed hypothesis and tool evidence. Assert they are labelled `DERIVED_AGENT_REASONING` and `UNTRUSTED_EXTERNAL_EVIDENCE`, never authoritative input classes.
10. **CMV3-TST-010 — Conversation recovery.** Rebase away an old item, find it with `history_search_contents`, read it with `history_read_item`, and assert the result appends only at the current tail.
11. **CMV3-TST-011 — Artifact recovery.** Persist old bash/grep evidence during admission, rebase, and recover the complete redacted content through `read_artifact`.
12. **CMV3-TST-012 — Source recovery.** Rebase away a source observation and recover the exact path/SHA/range through `read_file`; retain range-aware residency and SHA invalidation.
13. **CMV3-TST-013 — Recovery immutability.** Snapshot all old audit/provider messages, call all four underscore-named History tools, and assert old items are unchanged.
14. **CMV3-TST-014 — Provider overflow.** Return provider overflow once. Assert one forced Full Rebase, one retry, no historical projection, and safe termination on a second overflow.
15. **CMV3-TST-015 — Normal semantic failure guard.** Fail one normal semantic attempt in generation N. Assert no commit, marker N, and zero further semantic calls under later normal pressure in N.
16. **CMV3-TST-016 — Hard bypass and emergency.** With marker N active, reach hard pressure and fail semantic generation. Assert the guard is bypassed, one deterministic emergency rebase uses the exact `CMV3-SEM-029` budget before final-tail selection, installs the fixed unavailable payload plus exact removed History ranges, commits generation N+1, preserves complete rounds, and stays at most 136,000 input tokens.
17. **CMV3-TST-017 — Prefix diagnostics.** Assert `previous_messages_preserved=true` throughout an epoch and false only on the first request after rebase/reset.
18. **CMV3-TST-018 — Provider accounting.** Cover exclusive cache accounting, inclusive/duplicate cache accounting, and no-cache usage without false pressure. Assert normalized provider pressure remains an input-token quantity and the default hard input limit is 251,904.
19. **CMV3-TST-019 — Repeated checkpoint consolidation contract.** Run three rebases with scripted valid handoffs. Assert one checkpoint remains, every previous handoff is supplied to the next semantic input, and fresh deterministic state supersedes old deterministic state.
20. **CMV3-TST-020 — Shared checkpoint budget.** Vary deterministic size and wrapper size. Assert `semantic_actual_max` follows the frozen formula, is passed directly to the provider, and final serialized checkpoint including wrapper is at most 12,288 without post-generation truncation.
21. **CMV3-TST-021 — History names and limits.** Assert schemas/registry/capabilities expose exactly `history_list_windows`, `history_list_items`, `history_search_contents`, and `history_read_item`; verify all window/item/query/read/result limits and deterministic errors.
22. **CMV3-TST-022 — No task-boundary rewrite.** Start a second task below pressure. Assert task text appends, generation is unchanged, and prior history remains the exact prefix.
23. **CMV3-TST-023 — Config contract.** Assert removed fields are absent, main-agent default output remains 16,000, and no summary-model/provider or failure-count config exists.
24. **CMV3-TST-024 — Candidate rejection.** Reject any candidate whose input-token estimate is not strictly smaller, exceeds the 136,000 input-token ceiling, exceeds the 12,288 checkpoint bound, or changes finalized raw rounds; assert no generation/window/source mutation.
25. **CMV3-TST-025 — Compaction usage isolation.** Complete a semantic handoff call and assert its usage is reported as compaction cost but does not overwrite the matching main-agent provider anchor.
26. **CMV3-TST-026 — Semantic protocol.** Assert the same resolved provider/model is used with `tools=[]`, computed `max_tokens`, and one installed synthetic user checkpoint with the exact frozen wrapper.
27. **CMV3-TST-027 — Simple output validator.** Cover missing text, blank text, over-budget text, every missing/duplicate/out-of-order heading, and serialization failure; assert no content repair or semantic parsing occurs.
28. **CMV3-TST-028 — Semantic input preflight.** Make `semantic_request_input_tokens` exceed `semantic_input_limit`. Assert the measured input excludes `semantic_actual_max` and safety margin; normal pressure performs no call/no commit and activates the generation guard; hard/overflow skips the call and commits one deterministic emergency rebase.
29. **CMV3-TST-029 — Explicit/manual guard bypass.** Activate the normal failure marker, request explicit compaction below hard pressure, and assert one semantic attempt occurs; on scripted failure, no emergency commit occurs and both generation and marker remain unchanged.
32. **CMV3-TST-032 — Main input accounting.** Build a main request whose system, tools, and messages total 236,000 input tokens with a 16,000 output cap. Assert `local_input_tokens == 236000`, not 252,000, and no automatic pressure occurs because 236,000 is below 244,800.
33. **CMV3-TST-033 — Auto input boundary.** Assert `local_input_tokens=244799` does not trigger automatic rebase and `local_input_tokens=244800` does.
34. **CMV3-TST-034 — Hard input boundary.** Assert `local_input_tokens=251903` is not hard pressure and `local_input_tokens=251904` is hard pressure.
35. **CMV3-TST-035 — Main output override.** Set `actual_main_request_max_output=8000`. Assert `hard_input_limit == 272000 - 8000 - 4096` while identical system, tools, and messages produce unchanged `local_input_tokens`.
36. **CMV3-TST-036 — Semantic preflight accounting.** Assert `semantic_request_input_tokens` contains only the semantic system prompt, messages, and empty tool schema cost. `semantic_actual_max` and safety margin reduce `semantic_input_limit` exactly once and are not added to measured input.
37. **CMV3-TST-037 — Post-rebase input ceiling and savings.** Assert the 136,000 ceiling measures system, tools, checkpoint, and retained raw messages only. Assert savings equal input-before minus input-after and exclude output caps, safety margin, and provider usage.
38. **CMV3-TST-038 — Emergency raw budget.** Vary static prefix, exact emergency wrapper, and fixed-payload token costs. Assert the full 4,096 deterministic reserve and all three costs reduce capacity before newest complete rounds are selected; no round is split or evicted afterward, exact removed History ranges fit deterministic state, and an invalid or over-ceiling candidate is not committed.

### 31.2 Provider semantic quality evaluation

These are real-provider benchmark/evaluation requirements, not deterministic pytest claims:

30. **CMV3-TST-030 — Correction quality evaluation.** In an A-then-user-correction-to-B trajectory, verify that the generated handoff places B under `CONFIRMED`, A under `REJECTED_OR_OBSOLETE`, and preserves the applicable user constraint.
31. **CMV3-TST-031 — Failed-hypothesis quality evaluation.** In a trajectory with a disproven agent hypothesis and a later confirmed cause, verify that the disproven hypothesis is absent from `CONFIRMED` and present under `REJECTED_OR_OBSOLETE` when continuity requires it.

A provider evaluation records the resolved model/provider, prompt version, inputs, output headings, pass/fail judgment, and raw usage without adding production policy fields. When no real provider is available, the result is reported as `not run`; deterministic implementation tests MUST NOT be presented as semantic-quality proof.

The final implementation gate is:

```bash
pytest
ruff check .
ruff format --check .
```

Targeted tests run first during development; the full suite runs once at the final integration gate and before merge.

---

## 32. Acceptance Criteria

V3 is accepted only when:

1. deterministic requirements `CMV3-TST-001` through `CMV3-TST-029` and `CMV3-TST-032` through `CMV3-TST-038` pass;
2. the complete repository test/lint/format gate passes;
3. no Candidate A, task-boundary auto-compaction, semantic heuristic classifier, or removed config field remains in production references;
4. normal epochs demonstrate exact append-only prefix preservation;
5. every auto/hard pressure event commits at most one Full Rebase;
6. successful rebases satisfy strict `local_input_tokens` decrease, final 12,288 checkpoint maximum including wrapper, complete-round retention, pre-summary removed-trajectory coverage, and the 136,000 input-token post-rebase ceiling;
7. scripted repeated-rebase tests prove previous handoff input consolidation and current authoritative runtime state; no unit test is described as proof of model semantic quality;
8. history, artifacts, and sources remain recoverable through distinct bounded paths;
9. provider overflow is bounded to one forced rebase and one retry;
10. one failed normal semantic attempt suppresses only later normal attempts in the same generation, while hard/overflow/explicit paths bypass the guard;
11. context pressure uses input-only accounting; the main-agent output default remains 16,000 and is applied exactly once to derive the default 251,904 hard input limit; the auto trigger remains 244,800;
12. semantic calls use the same provider/model, no tools, computed shared-budget output limit, fixed trust labels/headings, and the exact synthetic-user checkpoint wrapper;
13. no provider-specific compaction branch, memory subsystem, dynamic eviction policy, or compatibility mode is introduced.
14. semantic output reservation and safety margin are applied exactly once to derive `semantic_input_limit` and are excluded from `semantic_request_input_tokens`;
15. post-rebase candidate measurement includes only provider-visible input context, and savings equal local input before minus local input after without provider usage, output reservations, or safety margin;
16. deterministic emergency raw selection uses the frozen `CMV3-SEM-029` formula, selects complete rounds before candidate construction, and commits no malformed or over-ceiling history.

Provider evaluations `CMV3-TST-030` and `CMV3-TST-031` are required evidence before semantic-quality claims or production-default enablement. When a provider is unavailable, deterministic review can complete, but the result MUST be reported as `provider semantic evaluation not run` and MUST NOT be described as semantically validated. Benchmark output validates defaults after implementation; it does not block this Spec's frozen status and cannot silently change constants.

---

## 33. Migration Sequence

1. Add V3 contract tests and remove CMV2 Candidate A/R0-R4/task-boundary tests that assert deleted behavior.
2. Preserve and revalidate admission shaping, ArtifactStore, source rehydration, provider usage normalization, and prefix diagnostics.
3. Add stable audit item/window indexing and the four frozen underscore-named read-only History tools over `conversation_messages`.
4. Split checkpoint work into deterministic structured state and the fixed same-provider/no-tools semantic protocol, including shared-budget calculation and validator.
5. Implement pre-summary final complete-round selection, exact removed-trajectory input, shared 12,288 checkpoint validation, input-only 136K candidate validation, deterministic emergency budgeting, and one atomic Full Rebase commit.
6. Add only `last_auto_compaction_failed_generation`; wire normal pressure, hard-pressure bypass, provider overflow, and semantic preflight to the single rebase path; remove automatic task-boundary rewriting.
7. Delete Candidate A symbols, removed config, semantic string heuristics, old failure count/circuit breaker, and redundant observability while retaining the 16K main output default.
8. Run deterministic V3 tests, then the full test/lint/format gate.
9. Run the two provider semantic evaluations and fixed-task benchmark; record unavailable provider evaluation as `not run` without changing frozen defaults.

Intermediate commits that leave both CMV2 and V3 production policies active are not mergeable. No compatibility flag is permitted.

---

## 34. Explicitly Deferred Work

The following remain outside V3:

- model-writable persistent Notes;
- semantic or vector history search;
- source-version archives;
- assistant ToolCall compression;
- provider-native compaction APIs;
- BodyAfterPrefix token-budget scope;
- dynamic retention budgets;
- cross-run history recovery;
- context-policy tuning based on model/provider identity.

Each requires a new specification revision with separate evidence.

---

## 35. Requirement Index

| Family | Scope | IDs |
|---|---|---|
| Core invariants | Capacity, epoch, cache, generation, observability | `CMV3-INV-001` through `CMV3-INV-026`; `CMV3-INV-CACHE-001` through `007` |
| Admission | First-visibility ToolResult shaping | `CMV3-ADM-001` through `009` |
| Trigger/measurement | Local/provider accounting and pressure | `CMV3-TRG-001` through `016` |
| Full rebase | Atomic checkpoint/tail construction | `CMV3-RBS-001` through `030` |
| Semantic handoff | Dedicated summary and failure semantics | `CMV3-SEM-001` through `029` |
| History | Windows and bounded read-only recovery | `CMV3-HIS-001` through `014` |
| Recovery | Artifact, source, and provider overflow | `CMV3-REC-001` through `015` |
| Configuration | Frozen constants and field migration | `CMV3-CFG-001` through `011` |
| Tests and provider evaluations | Deterministic contracts and separately labelled semantic quality evidence | `CMV3-TST-001` through `038` |

The production implementation is complete only when every applicable row in Section 29 cites its requirement IDs and every behavior in this index has passing evidence.
