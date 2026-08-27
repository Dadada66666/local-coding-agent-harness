# Repository Skills Specification

## 1. Document Metadata

| Field | Value |
| --- | --- |
| Title | Repository Skills Specification |
| Version | 1.0 |
| Status | FROZEN FOR IMPLEMENTATION |
| Local repository HEAD | `d1f6610aee1d53a951f0f9ac9593ffde6f2eb8f9` |
| Local prerequisite working-tree patch | `7e86e0956881aab54c34a52fd3e8d7ef28e7ac29` |
| Prerequisite changed files | `src/agent/prompts.py`, `tests/unit/agent/test_prompts.py` |
| Codex reference HEAD | `ed42068c45c1b0ab92eaf495c2880c63ca06fa09` |
| Format reference | [Agent Skills specification](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx) |
| Codex product reference | [OpenAI Codex Skills documentation](https://developers.openai.com/codex/skills) |

The local prerequisite patch is the Git object hash of the binary diff for the two listed
uncommitted files, calculated before this specification was added. Those user-owned changes are
part of the reviewed implementation baseline and MUST be preserved.

This document is the implementation contract for the first Skills feature in this Harness. It
does not supersede:

- `docs/spec.md` for Context Manager V3;
- `docs/runtime-economics-spec.md` for lifecycle and accounting behavior;
- `docs/mcp-client-spec.md` for MCP;
- `docs/verification-execution-metadata-spec.md` for Bash verification metadata.

If implementation requires a Context Manager change, a new Permission architecture, a new Plan
transition, or a second Skills execution path, implementation MUST stop and report:

`SPEC DEVIATION REQUIRED`

## 2. Repository Reality Check

The current Harness has no Skills loader, catalog, selection contract, or Skills-specific tool.
The reusable primitives already present are:

| Current component | Proven responsibility | Skills consequence |
| --- | --- | --- |
| `AgentLoop.create_context()` | Creates `AgentContext` before the first Provider request | Discover repository Skills here, then freeze the catalog for the session |
| `build_system_prompt()` | Rebuilds deterministic phase-aware system text before every request | Render one immutable bounded catalog section through this function |
| `read_file` | Safe WORKDIR read, UTF-8 validation, line/character pagination, SHA coverage, rehydration | Use it to load `SKILL.md` and text references; do not duplicate it |
| `bash` | Runs commands through Plan, Permission, risk classification, environment, and Sandbox | Skill scripts use Bash under unchanged authority |
| `ToolRegistry` / `ToolExecutor` | Own model-visible schemas and the normal tool pipeline | Register no Skills-specific execution or read tool in v1 |
| `PlanCapabilities` / `PlanGate` | Own lifecycle visibility and side-effect authorization | Skill use inherits the current phase; it creates no capability |
| `PermissionGate` / `AccessPolicy` | Own filesystem and process safety | Skills never pre-approve tools; protect Skill packages from self-modification |
| CMV3 | Bounds ToolResults and recovers projected source observations | Skill reads remain ordinary `read_file` source observations |
| `TraceLogger` | Records bounded runtime events | Add only catalog-load diagnostics; do not create a Skills metrics subsystem |

`ModelClient` exposes an Anthropic-compatible `system`, `messages`, and `tools` request. It has no
structured Provider Skill input and no Provider-native Skills protocol. The v1 design is therefore
Provider-independent and does not add a Provider branch.

## 3. Reference Evidence and Decisions

Codex is an architecture reference, not this Harness's implementation contract.

| Codex / standard component | Observed behavior | Adopt / adapt / reject | Decision for this Harness |
| --- | --- | --- | --- |
| `SKILL.md` package | Required YAML `name` and `description`, followed by Markdown instructions | Adopt | Use the standard package and validation rules |
| Progressive disclosure | Metadata is visible at startup; full instructions load only after selection | Adopt | Put bounded metadata in the system catalog; use `read_file` for the body |
| Explicit and implicit activation | `$name`/explicit selection and description-based matching select Skills | Adapt | Freeze both as model prompt contracts; no new structured Provider input in v1 |
| Host root discovery | Codex supports repo, user, admin, system, plugin, and extra roots | Adapt narrowly | Discover only `<WORKDIR>/.agents/skills` in v1 |
| Catalog rendering budget | Codex bounds catalog metadata and keeps rendering deterministic | Adopt simply | Fixed 8,000-character whole-section bound; no dynamic token policy |
| `skills.read` | Reads environment/orchestrator-owned packages with bounded pagination | Reject for v1 | Existing `read_file` already owns repository file reading and recovery |
| Dynamic Skill selector | Latest Codex contains lexical/routing-card/LRU selector implementations | Reject | No scoring, LRU, semantic selector, cache, or hidden automatic choice |
| Root aliases and source authorities | Codex supports host/executor/orchestrator/custom locators | Reject for v1 | One repository root needs no alias or authority abstraction |
| Skill interfaces, dependencies, and policies | Optional UI metadata, tool dependencies, and invocation policies | Defer | Parse only standard `name` and `description`; optional fields grant nothing |
| Invocation analytics | Codex can record detailed invocation types | Reject as a subsystem | One bounded catalog-load trace event is sufficient |

The relevant frozen Codex references are:

- [`codex-rs/skills/src/parser.rs`](https://github.com/openai/codex/blob/ed42068c45c1b0ab92eaf495c2880c63ca06fa09/codex-rs/skills/src/parser.rs)
  for frontmatter validation;
- [`codex-rs/ext/skills/src/host_roots.rs`](https://github.com/openai/codex/blob/ed42068c45c1b0ab92eaf495c2880c63ca06fa09/codex-rs/ext/skills/src/host_roots.rs)
  for `.agents/skills` discovery;
- [`codex-rs/ext/skills/src/catalog_prompt.rs`](https://github.com/openai/codex/blob/ed42068c45c1b0ab92eaf495c2880c63ca06fa09/codex-rs/ext/skills/src/catalog_prompt.rs)
  for progressive-disclosure instructions;
- [`codex-rs/ext/skills/src/render.rs`](https://github.com/openai/codex/blob/ed42068c45c1b0ab92eaf495c2880c63ca06fa09/codex-rs/ext/skills/src/render.rs)
  for bounded deterministic catalog rendering;
- [`codex-rs/skills/src/selection.rs`](https://github.com/openai/codex/blob/ed42068c45c1b0ab92eaf495c2880c63ca06fa09/codex-rs/skills/src/selection.rs)
  for explicit-versus-implicit selection semantics.

All references are pinned to Codex commit
`ed42068c45c1b0ab92eaf495c2880c63ca06fa09` for this specification review.

## 4. Problem Statement

Today the model cannot know which repository workflows are packaged as Skills. If a repository
contains a `SKILL.md`, Runtime neither discovers its metadata nor gives the model a deterministic
activation contract. Directly placing every Skill body in the system prompt would waste context,
weaken prefix stability, and expose irrelevant instructions. Adding a second file reader or Skill
executor would duplicate already-correct safety and Context behavior.

The required correction is a small orchestration layer:

```text
AgentContext creation
    -> discover and validate repository Skill metadata once
    -> freeze an immutable session catalog
    -> render bounded name/description metadata in system prompt
    -> model selects an applicable Skill
    -> existing read_file loads SKILL.md completely
    -> existing read_file/Bash/file tools use required resources
    -> existing Plan/Permission/Sandbox/CMV3 remain authoritative
```

## 5. Goals

1. Support standard repository-local Agent Skill packages.
2. Make available Skills discoverable without loading every body.
3. Support explicit `$skill-name` and description-based implicit use through a concise prompt
   contract.
4. Load selected Skill instructions and required text references through existing `read_file`.
5. Run Skill scripts only through existing Bash execution policy.
6. Keep model-visible tool schemas unchanged.
7. Keep the catalog deterministic and bounded for Provider prefix stability.
8. Prevent the agent from modifying its own repository Skill instructions.
9. Preserve exact zero-Skills model-visible behavior.

## 6. Non-Goals

Version 1 does not implement:

- user, admin, system, plugin, remote, executor, or orchestrator Skill roots;
- `.codex/skills`, `.claude/skills`, home-directory, or ancestor-directory discovery;
- Skill installation, authoring, enable/disable commands, or hot reload;
- a `read_skill`, `skills.list`, `skills.read`, or Skill execution tool;
- a Skills manager, router, cache, memory system, or new executor;
- dynamic selection, lexical ranking, semantic matching code, embeddings, LRU, TTL, or relevance
  scoring;
- Provider-native Skill inputs or Provider-specific branches;
- automatic injection of full Skill bodies into system or user messages;
- `agents/openai.yaml`, UI metadata, dependency installation, or dependency resolution;
- enforcement of `allowed-tools`, `compatibility`, or arbitrary metadata;
- binary asset handling, template import, or media rendering;
- subagents or cross-agent Skill sharing;
- changes to Context, MCP, Plan lifecycle, PermissionGate, Sandbox, ArtifactStore, or History.

## 7. Terminology

| Term | Definition |
| --- | --- |
| Skill package | One direct child directory of `<WORKDIR>/.agents/skills` containing `SKILL.md` |
| Skill metadata | Validated `name`, normalized `description`, and repository-relative `SKILL.md` path |
| Skill catalog | Immutable, name-sorted tuple of valid metadata for one `AgentContext` session |
| Catalog section | Bounded system-prompt text containing usage rules and every catalog entry |
| Activation | The model decides a Skill applies and reads its complete `SKILL.md` |
| Explicit activation | The user names an available Skill using `$skill-name` or an unambiguous plain name |
| Implicit activation | The task clearly matches an available Skill's description |
| Resource | A file referenced from `SKILL.md`, resolved relative to the Skill package |
| Skill instruction | Repository-provided workflow guidance; never a permission or security authority |

## 8. Architecture Invariants

- **SKL-ARC-001:** Skills MUST remain an instruction-loading feature, not a tool plugin,
  execution, permission, Context, or memory subsystem.
- **SKL-ARC-002:** Runtime MUST discover metadata once per `AgentContext` and MUST NOT rescan it
  between model calls or interactive tasks in that context.
- **SKL-ARC-003:** The session catalog MUST be immutable after discovery completes.
- **SKL-ARC-004:** Full Skill bodies MUST NOT be placed in the base system catalog.
- **SKL-ARC-005:** No direct per-Skill Provider tool schema MAY be registered.
- **SKL-ARC-006:** Existing `read_file`, Bash, repository mutation tools, ToolExecutor, hooks, Plan,
  Permission, Sandbox, and CMV3 paths MUST be reused without alternate execution paths.
- **SKL-ARC-007:** Skills loading MUST finish before the first Provider request.
- **SKL-ARC-008:** When no valid Skills root exists, system prompt text, model-visible native tool
  schemas, tool ordering, Plan behavior, and lifecycle behavior MUST match the current baseline.

## 9. Package Format

The only discovered layout is:

```text
<WORKDIR>/.agents/skills/
  <skill-name>/
    SKILL.md
    scripts/       # optional
    references/    # optional
    assets/        # optional; no new binary support
```

- **SKL-FMT-001:** `SKILL.md` MUST start with YAML frontmatter delimited by lines whose trimmed
  content is exactly `---`, followed by Markdown instructions.
- **SKL-FMT-002:** Frontmatter MUST contain string fields `name` and `description`.
- **SKL-FMT-003:** `name` MUST be 1-64 ASCII characters, contain only lowercase letters, digits,
  and single hyphens, and MUST NOT start, end, or contain consecutive hyphens.
- **SKL-FMT-004:** `name` MUST exactly equal its package directory name.
- **SKL-FMT-005:** `description` MUST be non-empty after whitespace normalization and MUST contain
  at most 1,024 characters.
- **SKL-FMT-006:** Metadata parsing MUST use `yaml.safe_load` from an explicit `PyYAML>=6.0`
  dependency. Invalid YAML MUST NOT be repaired, coerced, or retried.
- **SKL-FMT-007:** Unknown optional frontmatter fields MAY be parsed by YAML but MUST be ignored.
  In particular, `allowed-tools` MUST NOT pre-authorize any tool.
- **SKL-FMT-008:** Metadata discovery MUST read at most 16,384 bytes from each `SKILL.md`. The
  closing frontmatter delimiter MUST occur within that prefix; otherwise the package is invalid.
- **SKL-FMT-009:** Package directories and their `SKILL.md` entrypoints MUST be real paths, not
  symbolic links.

## 10. Discovery and Session Lifecycle

- **SKL-DIS-001:** The sole v1 discovery root MUST be
  `<WORKDIR>/.agents/skills`.
- **SKL-DIS-002:** Runtime MUST inspect only direct child directories of the discovery root and
  only a direct child file named exactly `SKILL.md` within each package.
- **SKL-DIS-003:** An absent discovery root MUST produce an empty catalog without an error.
- **SKL-DIS-004:** A present discovery path that is not a directory, cannot be enumerated, or
  resolves outside WORKDIR MUST fail Skills startup before the first Provider request.
- **SKL-DIS-005:** Child entries MUST be considered in normalized name order. The final catalog
  MUST be sorted by validated Skill name, then relative entrypoint path.
- **SKL-DIS-006:** Invalid packages MUST be excluded individually. Their bounded relative path and
  validation reason MUST be retained as load issues; valid siblings MUST remain available.
- **SKL-DIS-007:** A catalog MAY contain at most 64 valid Skills. More than 64 valid packages MUST
  fail Skills startup before the first Provider request rather than silently omit a Skill.
- **SKL-DIS-008:** Skill discovery MUST occur after a valid `AgentContext` and trace exist, but
  before MCP startup and before the first Provider request.
- **SKL-DIS-009:** One-shot runs discover once and interactive sessions discover once. Starting a
  later interactive task MUST reuse the same catalog.
- **SKL-DIS-010:** Filesystem changes under `.agents/skills` during a session MUST take effect only
  in a new `AgentContext`; no watcher or refresh command is permitted.

The implementation MUST be a small set of pure functions and frozen data classes in
`runtime.skills`. It MUST NOT introduce `SkillManager`, `SkillRuntime`, `SkillCache`, or another
lifecycle owner.

## 11. Catalog Rendering and Progressive Disclosure

The system catalog MUST contain:

1. one concise definition of Skills;
2. the fixed root `.agents/skills`;
3. every valid Skill name and a bounded description;
4. activation, resource-resolution, safety, and context-hygiene rules.

The renderer MUST NOT include full bodies, raw YAML, arbitrary metadata, absolute paths, file
contents, or directory listings.

- **SKL-CAT-001:** The entire rendered Skills section, including fixed instructions and entries,
  MUST contain at most 8,000 Unicode characters.
- **SKL-CAT-002:** Every catalog Skill name MUST remain visible. Budget enforcement MAY shorten
  descriptions but MUST NOT omit an entry.
- **SKL-CAT-003:** The renderer MUST first compute the fixed instructions and name-only entry cost,
  then divide the remaining description-character allowance equally across entries. Any remainder
  MUST be assigned in final catalog order. Descriptions MUST be truncated at character boundaries
  with `...` when at least three characters are available for the suffix.
- **SKL-CAT-004:** If fixed instructions plus all name-only entries cannot fit 8,000 characters,
  Skills startup MUST fail before the first Provider request. No secondary catalog profile is
  allowed.
- **SKL-CAT-005:** Rendering MUST be byte-for-byte deterministic for the same immutable catalog.
- **SKL-CAT-006:** The catalog MUST use repository-relative paths only. The fixed path rule is
  `.agents/skills/<name>/SKILL.md`; repeating absolute paths per entry is forbidden.
- **SKL-CAT-007:** A zero-entry catalog MUST render an empty string, preserving the current system
  prompt exactly.
- **SKL-CAT-008:** Catalog rendering MUST NOT depend on turn number, Plan progress, remaining model
  calls, previous Skill reads, or task keywords.

## 12. Selection and Activation Contract

The model, not Runtime, chooses whether a valid Skill applies. Runtime enforces only discovery,
format, bounds, path, and existing safety invariants.

- **SKL-ACT-001:** The catalog prompt MUST instruct the model to activate a Skill when the user
  explicitly names it with `$skill-name` or an unambiguous plain name.
- **SKL-ACT-002:** The catalog prompt MUST instruct the model to activate a Skill when the task
  clearly matches its description.
- **SKL-ACT-003:** After choosing a Skill, the model MUST read its complete `SKILL.md` with
  `read_file` before taking task actions governed by that Skill.
- **SKL-ACT-004:** If `read_file` paginates, the model MUST continue from returned `next_offset`
  until the entrypoint is complete; it MUST NOT infer `offset + limit`.
- **SKL-ACT-005:** Multiple applicable Skills MUST be limited to the smallest set that covers the
  task, and the model MUST state their order in one concise line.
- **SKL-ACT-006:** Skill use is task-scoped guidance. A later interactive task MUST match or name
  the Skill again; Runtime MUST NOT maintain an active-Skill memory.
- **SKL-ACT-007:** A missing or invalid explicitly named Skill MUST be reported briefly, after which
  the model MAY continue using the best available non-Skill workflow.
- **SKL-ACT-008:** Runtime MUST NOT parse task keywords, automatically select Skills, inject Skill
  bodies, count Skill searches, or suppress repeated reads.
- **SKL-ACT-009:** Deterministic tests MUST validate catalog and prompt contracts. Real-Provider
  evaluations, not pytest, MUST evaluate whether semantic implicit matching is effective.

This v1 choice intentionally accepts one normal `read_file` tool round for activation. It avoids a
new Provider protocol, a synthetic high-authority message, duplicate file-access code, and special
Context retention.

## 13. Resource and Script Use

- **SKL-RES-001:** Relative paths mentioned by `SKILL.md` MUST be resolved from the package
  directory `.agents/skills/<name>`.
- **SKL-RES-002:** Only resources required by the chosen workflow MUST be read. Runtime MUST NOT
  preload `scripts/`, `references/`, `assets/`, or the whole package tree.
- **SKL-RES-003:** UTF-8 text resources MUST be read through existing `read_file`, preserving its
  pagination, character budget, SHA tracking, protected-path checks, and rehydration behavior.
- **SKL-RES-004:** Repository discovery outside the Skill package MUST continue to use normal
  repository tools; a Skill reference grants no additional filesystem scope.
- **SKL-RES-005:** Skill scripts MUST run only through existing Bash calls. Their command,
  environment, network, permission, verification purpose, result scope, and Sandbox behavior MUST
  be evaluated exactly as for any other Bash call.
- **SKL-RES-006:** Runtime MUST NOT automatically execute, install dependencies for, patch, or
  repair a Skill script.
- **SKL-RES-007:** Existing tools MAY use text assets they already support. Binary Skill assets are
  outside v1 and MUST NOT cause a new file or media subsystem.

## 14. Trust and Security Contract

- **SKL-SEC-001:** Skill metadata and instructions are repository-provided workflow guidance. They
  are subordinate to the system prompt, current user request, Plan lifecycle, PermissionGate,
  AccessPolicy, and Sandbox.
- **SKL-SEC-002:** A Skill MUST NOT grant tool availability, permission approval, filesystem access,
  network access, credentials, environment variables, or verification authority.
- **SKL-SEC-003:** `allowed-tools`, annotations, or prose claiming pre-approval MUST have no Runtime
  effect.
- **SKL-SEC-004:** `.agents/skills` MUST be added to `AccessPolicy.protected_write_prefixes`, using
  the existing denial path for structured file tools and Bash mutations.
- **SKL-SEC-005:** `.agents/skills` MUST remain readable. It MUST NOT be added to protected read
  prefixes.
- **SKL-SEC-006:** Loader diagnostics and trace events MUST NOT contain Skill bodies or unrelated
  repository content.
- **SKL-SEC-007:** No full process environment, secret, MCP credential, or user-home path may enter
  the catalog prompt.
- **SKL-SEC-008:** Following a Skill MUST never weaken validation-only task scope, Plan approval,
  exact edit semantics, Bash purpose/result-scope semantics, or existing security decisions.

## 15. Plan and Lifecycle Compatibility

- **SKL-PLAN-001:** No `PlanCapabilities`, `PlanGate`, `PlanController`, Plan state, or transition
  change is authorized.
- **SKL-PLAN-002:** Planning and Auto-undecided may load Skills because existing `read_file` is
  already read-only and visible there. Mutations and Bash remain governed by the current phase.
- **SKL-PLAN-003:** Completed and awaiting-approval behavior MUST remain unchanged. Catalog presence
  MUST NOT create tool availability in either phase.
- **SKL-PLAN-004:** A Skill workflow that materially expands an approved Plan MUST use the existing
  replanning contract; Skill text is not approval.

## 16. Context and Prefix-Cache Compatibility

- **SKL-CTX-001:** No production file under `src/runtime/context/**` may change.
- **SKL-CTX-002:** Skill entrypoints and text resources read by `read_file` MUST remain ordinary
  source ToolResults subject to CMV3 admission, source projection, and exact rehydration.
- **SKL-CTX-003:** Runtime MUST NOT add Skill pinning, retention, memory, artifact, projection, TTL,
  or special checkpoint semantics.
- **SKL-CTX-004:** The immutable catalog section MUST remain identical within one stable Plan phase.
- **SKL-CTX-005:** Skill activation MUST NOT add, remove, reorder, or rewrite Provider tool schemas.
- **SKL-CTX-006:** Changing the repository Skill catalog in a new session is an intentional system
  prefix change. Reading a selected body affects only the normal message tail.
- **SKL-CTX-007:** Context savings and Provider usage accounting MUST remain governed exclusively by
  CMV3 and the runtime-economics specification.

## 17. Failure Semantics

- **SKL-ERR-001:** Missing discovery root is the only silent empty-catalog case.
- **SKL-ERR-002:** Invalid individual packages are excluded with bounded load issues and do not
  invalidate valid siblings.
- **SKL-ERR-003:** Root-level I/O/containment failures, more than 64 valid Skills, or an impossible
  8,000-character name-only catalog MUST fail context startup before the first Provider request.
- **SKL-ERR-004:** A `read_file` failure while loading a selected Skill remains a normal ToolResult;
  no automatic retry, alternate root, body injection, or fallback reader is permitted.
- **SKL-ERR-005:** A Skill-requested command or mutation denied by Plan, Permission, AccessPolicy, or
  Sandbox remains denied; Runtime MUST NOT reinterpret it as a Skills error.

## 18. Observability

Exactly one new event family is required:

```json
{
  "type": "skills_catalog_loaded",
  "available_count": 3,
  "invalid_count": 1,
  "catalog_chars": 1240,
  "root": ".agents/skills",
  "issues": [
    {"path": ".agents/skills/bad/SKILL.md", "reason": "invalid YAML"}
  ]
}
```

- **SKL-OBS-001:** The event MUST be emitted once per `AgentContext` after discovery and before the
  first Provider request, including an empty catalog.
- **SKL-OBS-002:** `issues` MUST contain at most 20 entries; paths are repository-relative and each
  reason is at most 200 characters. If more issues exist, the event MUST include
  `issues_omitted` with the exact omitted count.
- **SKL-OBS-003:** Existing `read_file`, Bash, permission, cost, Context, and tool traces remain the
  source of truth for later Skill actions. No per-Skill metrics or report section is authorized.

## 19. Configuration and Dependencies

- **SKL-CFG-001:** No `RunConfig` field and no CLI flag may be added. Repository Skills are enabled
  by the presence of `<WORKDIR>/.agents/skills`.
- **SKL-CFG-002:** `pyproject.toml` MUST add the direct runtime dependency `PyYAML>=6.0`.
- **SKL-CFG-003:** All numeric bounds in this document are internal constants, not user-tunable
  policy.
- **SKL-CFG-004:** No legacy Skill location or compatibility alias may be added.

## 20. Required Production Changes by File

No production file outside this table is authorized.

| File | Function / class | Requirement mapping | Exact change |
| --- | --- | --- | --- |
| `src/runtime/skills.py` (new) | frozen data classes and pure load/render functions | SKL-ARC-002..004, SKL-FMT-*, SKL-DIS-*, SKL-CAT-*, SKL-ERR-* | Discover, validate, sort, bound, and render the one repository catalog; define errors/issues; no manager class |
| `src/runtime/session.py` | `AgentContext` | SKL-ARC-002..003, SKL-DIS-009..010 | Add one catalog field with an immutable empty default; add no lifecycle methods or active-Skill state |
| `src/agent/loop.py` | `create_context()`, per-request prompt construction | SKL-ARC-007, SKL-DIS-008..010, SKL-OBS-*, SKL-CTX-004 | Load and attach the catalog once after context creation, trace it, and pass its pre-rendered section to the prompt before MCP startup/first request |
| `src/agent/prompts.py` | `build_system_prompt()` | SKL-CAT-*, SKL-ACT-*, SKL-SEC-001..003, SKL-CTX-004..006 | Accept an already-rendered immutable Skills section and append it between base behavior and phase-specific Plan instructions; add no selection logic |
| `src/runtime/security/access_policy.py` | `AccessPolicy.protected_write_prefixes` | SKL-SEC-004..005 | Add `.agents/skills`; do not alter protected reads or PermissionGate |
| `pyproject.toml` | runtime dependencies | SKL-FMT-006, SKL-CFG-002 | Add `PyYAML>=6.0` |
| `README.md` | Skills usage section | SKL-FMT-*, SKL-DIS-001..003, SKL-ACT-*, SKL-SEC-* | Document repository layout, progressive disclosure, trigger syntax, and unchanged safety authority |

The implementation MUST NOT modify:

- `src/runtime/bootstrap.py`, `src/tools/registry.py`, or add a Skill tool;
- `src/runtime/plan/**`;
- `src/runtime/security/permission_gate.py` or Sandbox;
- `src/runtime/context/**`;
- `src/runtime/mcp/**` or `src/tools/mcp_tool.py`;
- `src/agent/model_client.py`;
- Artifact, History, verification, recovery, or report writers.

## 21. Required Tests

Only tests that prove frozen behavior are authorized.

| Test file | Required coverage |
| --- | --- |
| `tests/unit/runtime/test_skills.py` (new) | format validation, safe YAML, name rules, directory-name equality, direct-child discovery, symlink rejection, deterministic sorting, invalid sibling isolation, 64-Skill limit, metadata byte limit, catalog bound, fair deterministic description truncation, zero catalog |
| `tests/unit/agent/test_prompts.py` | zero-Skills exact prompt preservation; bounded catalog and activation/safety semantic fragments; unchanged Plan phase fragments |
| `tests/unit/agent/test_loop.py` | catalog loads after `AgentContext` exists and before MCP/Provider; one load per interactive context; trace event; stable same-phase system/tools payload |
| `tests/unit/runtime/security/test_access_policy.py` | structured/Bash write references under `.agents/skills` remain protected; reads remain permitted |
| `tests/integration/test_interactive_session.py` | later tasks reuse the immutable catalog and do not rescan changed disk metadata |
| `tests/integration/test_package_surface.py` | new `runtime.skills` module remains importable from the installed source layout |

Deterministic regressions:

- **SKL-TST-001:** An absent `.agents/skills` root produces an empty immutable catalog and the
  exact current system prompt and Provider tools serialization.
- **SKL-TST-002:** A valid package is discovered from the direct-child layout and appears once with
  normalized metadata and repository-relative identity.
- **SKL-TST-003:** Missing frontmatter, malformed YAML, missing fields, invalid name, mismatched
  directory, oversized description, oversized metadata prefix, and symlinked entrypoints are each
  excluded with a bounded issue.
- **SKL-TST-004:** A malformed package does not hide a valid sibling.
- **SKL-TST-005:** Sixty-five valid packages fail before the first Provider request.
- **SKL-TST-006:** All names remain visible and the complete catalog section is at most 8,000
  characters for the maximum valid catalog.
- **SKL-TST-007:** The same catalog renders byte-for-byte identically across repeated calls and
  stable Plan phases.
- **SKL-TST-008:** Prompt semantics cover explicit `$name`, unambiguous plain naming,
  description matching, complete `SKILL.md` read, returned `next_offset`, minimal Skill set,
  relative resources, and unchanged safety authority.
- **SKL-TST-009:** `AgentContext` and trace exist before discovery; discovery completes before MCP
  startup and before the first Provider call.
- **SKL-TST-010:** One interactive context discovers once; changing disk metadata does not alter its
  catalog, prompt, or tools hash until a new context is created.
- **SKL-TST-011:** With Skills enabled, a fake model can call existing `read_file` for
  `.agents/skills/demo/SKILL.md`, follow pagination, and receive the body through the normal
  ToolExecutor and CMV3 admission path.
- **SKL-TST-012:** Skill discovery and activation add no Provider tool schema; tools serialization
  remains byte-for-byte unchanged within the same lifecycle phase.
- **SKL-TST-013:** Existing Planning, Auto-undecided, Direct, Awaiting Approval, Executing,
  Completed, and Cancelled visibility tests pass unchanged.
- **SKL-TST-014:** `.agents/skills/**` writes are denied through existing AccessPolicy behavior;
  `read_file` remains permitted.
- **SKL-TST-015:** A Skill-requested Bash command still reaches existing Plan, Permission, risk,
  verification metadata, and Sandbox behavior without a Skills bypass.
- **SKL-TST-016:** Existing CMV3 source projection and rehydration tests pass unchanged for a
  `SKILL.md` path.

Provider evaluation, kept separate from deterministic pytest:

1. `$demo-skill` causes the model to read the complete entrypoint before governed actions.
2. A task clearly matching the description activates the Skill without the user naming it.
3. An unrelated task does not load the Skill body.
4. A multi-Skill task selects the smallest sufficient set.

These are semantic behavior evaluations. They MUST NOT be represented as deterministic unit-test
proof of model quality.

## 22. Acceptance Criteria

Implementation is accepted only when all statements are true:

1. Standard valid repository packages under `.agents/skills` are discovered before Provider use.
2. Metadata, not bodies, is present in the bounded system catalog.
3. Every valid Skill name remains visible and the section is at most 8,000 characters.
4. Catalog and prompt rendering are deterministic for a session.
5. Selected entrypoints and references use existing `read_file` and its exact cursor contract.
6. Skill scripts use existing Bash and do not gain permission or Sandbox authority.
7. No Skill-specific Provider tool schema or execution path exists.
8. No `RunConfig` or CLI field exists for Skills v1.
9. No Context, MCP, Plan, PermissionGate, Sandbox, Artifact, History, verification, or recovery
   production behavior changes.
10. `.agents/skills` is write-protected but readable.
11. Zero-Skills system prompt and Provider tools are byte-for-byte baseline-compatible.
12. Stable-phase system and tools hashes remain stable after Skill reads.
13. Invalid packages are observable without suppressing valid siblings.
14. The catalog is reused across interactive tasks without a watcher or cache subsystem.
15. New production modules consist only of `src/runtime/skills.py`.
16. `pytest`, `ruff check .`, and `ruff format --check .` pass and are reported truthfully.
17. Every production diff maps to at least one `SKL-*` requirement and no unmapped production diff
   remains.

## 23. Implementation Sequence

Implementation MUST proceed in this order:

1. Preserve and record the frozen local HEAD plus prerequisite patch.
2. Add `PyYAML` and implement strict format/parser unit tests.
3. Implement immutable metadata data classes and direct-child discovery.
4. Implement deterministic bounded catalog rendering and its maximum-size tests.
5. Add the catalog field to `AgentContext`.
6. Wire one-time discovery after context creation and before MCP/Provider startup.
7. Add the catalog and activation contract to `build_system_prompt()`.
8. Protect `.agents/skills` writes through the existing AccessPolicy list.
9. Add lifecycle, stable-prefix, existing-`read_file`, and interactive reuse tests.
10. Update README usage documentation.
11. Run focused Skills, prompt, loop, Plan, access-policy, read-file, and CMV3 tests.
12. Run full `pytest`.
13. Run `ruff check .`.
14. Run `ruff format --check .`.
15. Audit `git diff --stat`, `git diff`, and `git diff --check`.
16. Produce a Requirement -> File -> Function -> Test mapping and remove every unmapped production
    change before completion.

## 24. Explicitly Deferred Work

- user/global/admin/system Skill scopes;
- plugins and remote Skill providers;
- additional discovery roots or configuration;
- structured Skill selection input and automatic full-body injection;
- Skill list/read tools;
- enable/disable state;
- hot reload and filesystem watchers;
- catalog search, ranking, semantic selection, and caches;
- dependency resolution and `agents/openai.yaml`;
- `allowed-tools` enforcement;
- Skill authoring and installation;
- binary assets and media pipelines;
- invocation analytics and dedicated report sections;
- subagent Skill propagation.

Deferred work MUST NOT leave interfaces, flags, placeholder fields, or unused abstractions in the
v1 implementation.

## 25. Requirement Index

| Family | Scope |
| --- | --- |
| `SKL-ARC-*` | minimal architecture and reuse invariants |
| `SKL-FMT-*` | package and YAML contract |
| `SKL-DIS-*` | repository discovery and session snapshot |
| `SKL-CAT-*` | bounded deterministic catalog |
| `SKL-ACT-*` | model selection and progressive activation |
| `SKL-RES-*` | references, scripts, and assets |
| `SKL-SEC-*` | trust, write protection, and unchanged authorities |
| `SKL-PLAN-*` | Plan lifecycle compatibility |
| `SKL-CTX-*` | CMV3 and prefix-cache compatibility |
| `SKL-ERR-*` | exact failure semantics |
| `SKL-OBS-*` | minimal trace contract |
| `SKL-CFG-*` | dependency and no-config contract |
| `SKL-TST-*` | deterministic tests and provider evaluation boundary |

## 26. Frozen Self-Review

1. Metadata discovery and full-body activation are separate: **Yes**.
2. All Skill bodies enter the base prompt: **No**.
3. A new Skill tool or executor is required: **No**.
4. Existing `read_file` provides the required bounded loading and recovery path: **Yes**.
5. Skill scripts bypass Bash, Plan, Permission, or Sandbox: **No**.
6. Skill metadata or `allowed-tools` grants authority: **No**.
7. The Provider tools array changes when a Skill is selected: **No**.
8. Catalog rendering is deterministic and bounded: **Yes**.
9. The implementation dynamically scores or selects Skills: **No**.
10. Invalid packages silently alter valid siblings: **No**.
11. Interactive tasks rescan Skills: **No**.
12. Zero-Skills model-visible behavior changes: **No**.
13. Any Context Manager production change is required: **No**.
14. Any Plan, MCP, PermissionGate, Sandbox, or ModelClient change is required: **No**.
15. New configuration or compatibility aliases are required: **No**.
16. The sole new production module has an implementation and test purpose: **Yes**.
17. Every MUST maps to an authorized change or deterministic test: **Yes**.
18. Any unresolved implementation choice remains: **No**.

`SPEC DEVIATION REQUIRED: No`
