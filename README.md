# Local Coding Agent Harness

[English](README.md) | [中文](README.zh-CN.md)

Local Coding Agent Harness is an auditable local runtime for coding agents that
work on real repositories through explicit, policy-controlled tools. The model
chooses its repository strategy; the runtime enforces filesystem, permission,
plan, verification, protocol, and context-capacity invariants.

Key capabilities:

- snapshot-bound, exact repository reads and edits
- structured Direct / Plan execution with explicit approval state
- layered permission checks and optional OS-level Bash sandboxing
- Context Manager V3 with bounded ToolResult admission and recoverable rebasing
- MCP V2 discovery through a stable search/call gateway surface
- authoritative verification tracking and bounded failure recovery
- complete trace, report, diff, artifact, and provider-usage output
- an isolated, deterministic Agent evaluation benchmark

## Architecture

The codebase keeps model orchestration, tool semantics, security policy,
context management, and observability as separate runtime responsibilities.

Core directories:

- `src/agent/`: agent loop, prompts, model client, and message conversion
- `src/runtime/`: session state, execution, recovery, and runtime composition
- `src/runtime/plan/`: plan policy, lifecycle controller, gate, and audit snapshots
- `src/runtime/context/`: context budgets, checkpoints, compaction, and projection
- `src/runtime/security/`: access policy, permission gate, risk analysis, and sandbox
- `src/runtime/hooks/`: lifecycle, policy, and tracking hooks
- `src/runtime/observability/`: traces, reports, artifacts, diffs, and cost tracking
- `src/tools/`: explicit tool implementations and registry
- `src/cli/`: Typer commands, interactive mode, and trace replay
- `tests/unit/` and `tests/integration/`: tests organized by source domain

See [`docs/architecture.md`](docs/architecture.md) for the dependency map and
the main execution paths. Context Manager V3 and MCP V2 are governed by their
frozen specifications in [`docs/spec.md`](docs/spec.md) and
[`docs/mcp-client-spec.md`](docs/mcp-client-spec.md).

## Install

```bash
pip install -e ".[dev]"
```

Create `.env` from `.env.example`:

```bash
ANTHROPIC_API_KEY=
MODEL_ID=
MODEL_CONTEXT_WINDOW_TOKENS=
ANTHROPIC_BASE_URL=
```

The adapter loads only the harness-root `.env` by default; it does not search
the active `WORKDIR`. Set `LCAH_ENV_FILE` to an explicit path when running an
installed package without that file.

Set `MODEL_CONTEXT_WINDOW_TOKENS` to the provider's real model window. The
runtime combines that hard-cap safety with its finite operating target; the
character threshold is used only when both token limits are disabled.

The model adapter uses the Anthropic Messages API shape, including top-level
`system`, `messages`, `tools`, assistant `tool_use` blocks, and user
`tool_result` blocks. `ANTHROPIC_BASE_URL` can point at an Anthropic-compatible
provider.

## CLI

Installed console scripts:

```bash
agent
lcah
```

Fallback without installing scripts:

```bash
python -m cli.app
```

Interactive mode uses the current terminal directory as `WORKDIR`:

```bash
agent --permission accept_edits
agent --sandbox
```

One-shot mode:

```bash
agent run "Fix the failing tests" --permission accept_edits
agent run "Inspect this project and summarize the structure" --permission read_only
```

Read artifacts:

```bash
agent report <run_id>
agent replay <run_id>
```

Permission modes:

- `read_only`: allow reads/searches only; writes are gated.
- `accept_edits`: allow normal file edits and safe commands; risky commands are
  still gated.
- `manual_approval`: ask before edits and command execution.

## MCP V2 Client

MCP is disabled unless a host-owned configuration is passed explicitly:

```bash
agent --mcp-config /absolute/path/to/mcp.json
agent run "Use the configured service" --mcp-config /absolute/path/to/mcp.json
```

The configuration supports stdio and bare Streamable HTTP:

```json
{
  "mcpServers": {
    "local": {
      "type": "stdio",
      "command": "python",
      "args": ["/absolute/path/to/server.py"]
    },
    "remote": {
      "type": "http",
      "url": "http://127.0.0.1:8765/mcp"
    }
  }
}
```

After an `AgentContext` exists, the session connects to configured servers,
discovers their tools once, and builds one immutable catalog. Remote schemas do
not expand the Provider `tools[]` array. Instead, the model sees a bounded,
stable gateway surface:

- `mcp_tool_search` performs deterministic local search over catalog metadata.
- `mcp_tool_call` resolves one canonical tool ID and invokes its existing MCP
  binding through the normal execution pipeline.

Planning and Auto-undecided phases expose local metadata search; direct and
approved execution expose both gateways. Individual remote schemas remain in
the runtime catalog, so search and calls do not rewrite the base Provider tool
surface. Remote calls continue through the existing Plan Gate, Permission Gate,
post-tool hooks, ToolResult admission, and Context Manager. MCP servers remain
authoritative for remote argument validation.

## Plan Mode

Plan mode is an optional runtime capability with three policies:

- `off`: preserve the original loop. Plan prompts, schemas, gates, and
  `plan.json` are omitted. This is the default for backward compatibility.
- `auto`: the model may inspect with read-only tools, then must call
  `select_execution_mode` before Bash or repository mutations. A direct choice
  continues normally; a plan choice stays read-only until a structured plan is
  submitted.
- `required`: planning starts immediately and remains read-only.

Execution-path policy and approval policy are independent. Approval defaults to
`manual`, so a submitted plan pauses at `awaiting_approval` under both `auto`
and `required`. Use `--plan-approval auto` only when submitted plans should be
authorized without user input.

Configure a run with:

```bash
agent --plan-mode auto
agent --plan-mode required
agent --plan-mode off
agent --plan       # alias for --plan-mode required
agent --no-plan    # alias for --plan-mode off
agent --plan-mode auto --plan-approval manual
agent --plan-mode auto --plan-approval auto
```

Conflicting plan options fail instead of silently overriding each other.
Interactive mode also recognizes:

```text
/plan-mode auto|required|off
/plan-approval manual|auto
/plan
/approve
/revise <feedback>
/cancel-plan
/plan-status
```

`/approve` and `/revise` resume the same task and preserve its model-call,
mutation, verification, recovery, and context budgets. They do not create a new
task. At an approval prompt, `1`, `2`, and `3` map to approve, revise, and cancel;
option `2` requests revision feedback. Exact replies `同意`, `同意执行`, `批准`,
`批准执行`, `approve`, and `approved` use a deterministic approval path and do
not spend a model call interpreting the authorization. Matching trims only outer
whitespace and lowercases ASCII, so negative or conditional text is never
substring-matched. All other ordinary text remains a continuation of the same
task and is interpreted by the dynamically visible `resolve_plan_response` tool.

Auto mode does not use a keyword or prompt-length heuristic: the model's direct
or plan choice remains a structured, traced tool call based on the task and
inspected repository.

The Plan Gate and Permission Gate have separate responsibilities. Before mode
selection, while planning, and while required approval is pending, the Plan
Gate blocks Bash and repository mutation tools before permission evaluation.
During direct or authorized execution it yields control to the existing
Permission Gate; plan state never grants filesystem or command permission.

Tool visibility is state-aware. Planning exposes only inspection tools and the
phase-specific plan contract; pending approval with fresh input exposes only
`resolve_plan_response`. `ToolRegistry.resolve()` supplies both schema visibility
and executor callability, while the Plan Gate remains a defense-in-depth runtime
boundary for forged or stale calls.

Planning remains read-only until the model explicitly submits or cancels the
plan. Legal repository inspection does not become unavailable because of a
phase-local call counter or draft age. The global `max_turns` limit remains the
task safety cap, while call-budget reporting is observational. Plan step count
remains model-selected, and every `replace_plan` call must state
`submit=true` or `submit=false` explicitly.

Planning input contains only step IDs and descriptions. Step status is runtime
execution evidence, not model-authored history. During replanning, a completed
step is preserved only when its ID and description match a step previously
completed through the authorized execution controller.

Before that narrow approval-resolution call, consumed source observations are
projected to bounded source stubs. A response containing any non-resolver tool is
rejected as a whole and receives one automatic resolver-only retry; repeated
failure pauses instead of looping.

Plan tool visibility follows the same capability projection:

- `auto + undecided`: `select_execution_mode`
- `plan + planning/executing`: `update_plan`
- `awaiting_approval + fresh user response`: `resolve_plan_response`
- `off`, direct execution, completed plans, and cancelled plans: no plan tools

Each active plan writes an atomic audit snapshot to
`<WORKDIR>/.agent/runs/<run_id>/plan.json`. It records decision and approval
policies, task ID/status, the model's selection reason, plan version,
authorization source, phase, and step progress.
It excludes environment data and is redacted before persistence. `plan.json` is
a plan decision and execution-state audit snapshot, not a complete session
recovery mechanism.

## Tools

Tools are registered through `ToolRegistry` and executed through a shared
`ToolExecutor`. Each tool owns validation, operation classification, and tool
semantics; runtime concerns such as permission checks, trace logging, large
output handling, and verification tracking are handled by hooks.

Available tools:

- `list_dir`: list visible files/directories, skipping runtime/cache directories
  such as `.agent`, `.git`, `.venv`, `node_modules`, and `__pycache__`.
- `grep`: search UTF-8 repository text with match limits and truncation
  metadata.
- `read_file`: read UTF-8 text with line numbers and record a file snapshot.
  Pages include total lines, returned range, `next_offset`, and `has_more`.
  Task-local, SHA-bound interval coverage identifies overlap and complete scans.
  Broad rereads of an unchanged fully scanned source return a small notice and
  direct the model to grep or a narrow line range. A source mutation invalidates
  coverage automatically. Non-UTF-8 files return a normal tool failure instead
  of an unhandled decode exception.
- `read_artifact`: retrieve a bounded slice of a large tool result through an
  opaque ID scoped to the current run; it does not accept filesystem paths.
- `write_file`: write a complete UTF-8 file. Missing files are created exclusively;
  existing files require a complete, current snapshot and are replaced atomically.
  Successful writes refresh the file snapshot.
- `edit_file`: replace exact text in a previously known file snapshot. It
  supports one replacement with `old_text` / `new_text`, or multiple
  replacements with `edits`. Repeated matches remain ambiguous by default;
  `occurrence` targets a specific match and `replace_all` explicitly replaces
  every match. Batch edits are atomic.
- `delete_file`: delete one snapshotted file. Current-task files can be cleaned
  up automatically in `accept_edits`; deleting a pre-existing file requires
  approval. Directories and symlinks are not supported.
- `bash`: run verification or inspection commands from `WORKDIR`. Commands can
  carry `purpose="verify"` so verification results are reflected in report
  success. Verification commands run fail-fast and cannot include explicit
  file mutations; `exit_expectation="nonzero"` marks an intentionally non-zero
  overall status. Shell patching is routed to the structured file tools.
- `view_diff`: show git diff when `WORKDIR` is a git repository; non-git
  directories return a clean "diff unavailable" result.
- `select_execution_mode`: dynamically visible in undecided auto mode; records
  the model's direct-or-plan choice and reason.
- `update_plan`: dynamically visible during plan lifecycle work. Planning
  replacements require explicit submit intent and cannot set execution status;
  execution actions update runtime-owned step progress. The tool cannot approve
  a manual plan.
- `resolve_plan_response`: visible only while a plan awaits approval and a fresh
  user continuation exists; records approve, revise, or cancel structurally.

File tools are constrained by `AgentContext.safe_path()`, so reads and writes
cannot escape `WORKDIR`; protected-path checks also apply to deletions.

## Runtime Behavior

Important runtime properties:

- File edits require a known snapshot from `read_file`, `write_file`, or a
  successful prior `edit_file`.
- Successful edits refresh the snapshot, so multiple edits to the same file do
  not require unnecessary rereads.
- No-op edits return success without marking the file changed.
- Interactive sessions separate whole-run state from current-task state. A
  previous prompt's failed verification or changed files cannot poison the next
  prompt's success inference.
- Task lifecycle is explicit (`idle`, `running`, `waiting_user`, `completed`,
  `failed`, `cancelled`). `finished` only stops the current loop invocation;
  waiting tasks are neither archived nor reset.
- `max_turns` limits model calls per task (40 by default), while trace turn IDs remain unique
  across an interactive run.
- Model `stop_reason` is recorded and validated. Truncated, refused, or
  protocol-invalid responses cannot be reported as successful completion;
  providers that omit `stop_reason` retain content-based compatibility.
- Parallel tool calls return one user message containing all matching
  `tool_result` blocks. Calls skipped after a terminal denial receive explicit
  cancelled results so the message history remains valid.
- Successful verification is tied to the current mutation version. A later
  file change makes that evidence stale until another verification runs.
- Shell-based file deletion returns a non-terminal routing failure so the
  model can retry with `delete_file`; recursive and broad destructive commands
  remain terminal denials.
- Shell risk analysis is quote-aware and records composite effects. A network
  command that also creates or writes paths presents the host and filesystem
  targets in one approval scope instead of hiding the write behind `network`.
- Directory listing and recursive search filter protected paths after canonical path
  resolution, including aliases that resolve to protected files.
- Context pressure uses input-only accounting for the system prompt, tool
  schemas, and provider-visible messages. Output reservation and the safety
  margin are applied once when deriving the applicable hard input limit.
- Every normal Context Epoch is append-only. New ToolResult batches are shaped
  before first model visibility and must satisfy the aggregate round budget.
- Real context pressure triggers one atomic Full Rebase built from authoritative
  runtime state, a structured semantic handoff, and the newest complete raw
  rounds. Normal lifecycle transitions do not rewrite historical context.
- Removed evidence stays recoverable through distinct Source, Artifact, and
  History paths. Source recovery remains path/SHA/range-bound, while history
  recovery appends requested evidence to the current tail.
- Provider context overflow gets one bounded force-compaction retry. Repeated
  overflow or compaction failures stop cleanly instead of looping indefinitely.
- Context measurements and savings notes are trace/report observability only;
  they are not appended to model messages.
- Unknown tools and validation failures are traced as normal tool results, which
  keeps debugging artifacts complete.
- Recovery prompts avoid duplicating large failed test output already present in
  the preceding tool result.

## Artifacts

Each run writes under:

```bash
<WORKDIR>/.agent/runs/<run_id>/
```

Artifacts:

- `trace.jsonl`: structured runtime events
- `readable_trace.md`: developer-friendly conversation/tool chain
- `report.md`: task/session cost, changed files, verification level, recovered
  failures, sandbox, and artifacts
- `diff.patch`: git diff, or a clean non-git placeholder
- `cost.json`: model usage plus estimated per-turn token breakdown
- `plan.json`: conditional plan decision and execution-state audit snapshot
- `artifacts/`: complete large tool outputs, recoverable in-run by opaque ID

`cost.json` breaks model input/output into categories such as system prompt,
tool schemas, user messages, assistant tool calls, tool results, compacted
history, assistant text, and tool calls. The breakdown is local estimation for
optimization; provider usage remains the billing source of truth.
Cache creation/read usage and estimated context-management savings are recorded
separately when the provider reports them.
The top-level totals remain session-wide; `current_task` and completed task
records expose task-local usage without resetting the run audit.

## Verification

The Bash `purpose` states whether a command is authoritative task verification:

```json
{"purpose": "verify"}
```

The supported intent values are:

- `verify`: authoritative evidence for the current task result
- `probe`: environment, setup, or availability diagnosis
- `run`: ordinary command execution

`result_scope="command"` means the returned exit status describes the foreground
command; `result_scope="launcher"` describes only a submitted launcher and is
never authoritative verification. Known test commands without an explicit
purpose retain legacy verification behavior, while explicit `run` and `probe`
suppress that inference. Read-only discovery, setup probes, and commands that
explicitly mutate files do not overwrite authoritative verification state.

## Sandbox Runtime

Bash commands can be wrapped with Anthropic Sandbox Runtime (`srt`) for an
extra local execution boundary:

```bash
npm install -g @anthropic-ai/sandbox-runtime
srt --version
agent --sandbox
```

Useful options:

- `--sandbox-fail-if-unavailable`: stop startup if `srt` cannot run.
- `--sandbox-settings <path>`: use a custom SRT settings file.
- `--sandbox-auto-allow/--no-sandbox-auto-allow`: control unknown bash
  auto-allow when a strong sandbox is available.
- `--bash-env <name>`: explicitly pass a non-secret environment variable to
  Bash. Repeat the option for multiple names. Provider keys and secret-like
  names are never inherited.

Linux/macOS:

- sandbox settings are applied with `srt --settings <settings_path> ...`
- `.env`, `.agent`, `.mcp.json`, and SSH data are denied at the OS read
  boundary; Git metadata required by `git status` remains internally readable,
  while direct Bash references and all protected writes are still gated
- startup performs an OS-level protected-read canary; only a successful denial
  is treated as a strong boundary

Windows:

- commands are wrapped as `srt <real-shell-argv...>` without `--settings`
- the runtime treats this as a weak boundary
- unknown bash commands are not auto-approved just because `srt` is installed

The sandbox is an execution boundary, not a replacement for `PermissionGate`.
Destructive commands, network commands, protected paths, and shell-based file
writes remain controlled by runtime permission checks.

Bash receives a sanitized environment instead of the harness process
environment. Tool output is redacted before model feedback, trace logging, or
artifact persistence.

## Agent Evaluation Benchmark

The standalone benchmark under `benchmarks/` evaluates the Agent through its
public Python API without becoming a production runtime dependency. Each case
copies a clean fixture into an isolated temporary workspace, rejects unexpected
interactive input, and evaluates the final repository with an external pytest
process.

The evaluator keeps four outcomes distinct:

- `task_correct`: the deterministic external oracle passed.
- `runtime_success`: the runtime reached a successful terminal state.
- `runtime_oracle_agreement`: runtime success and external correctness agree.
- `end_to_end_pass`: correctness, runtime completion, execution integrity, and
  case-specific invariants all passed.

The six current contract cases cover deterministic repair, validation-only
mutation discipline, Required Plan authorization, cross-module diagnosis,
regression preservation, and bounded verification behavior. Test files are
immutable, changed paths are checked independently, and efficiency metrics are
reported without turning token or call counts into correctness gates.

Run all cases sequentially:

```bash
python -m benchmarks.runner
```

Generated results are written to:

```text
benchmarks/results/resume.json
benchmarks/results/resume.md
```

A reference run on commit `a9beb66c03cb9c78eaf266606f9e02d82ab25e38`
with `gpt-5.6-terra` completed all six cases with external-oracle agreement and
zero unauthorized mutations:

| E2E | Task correct | Runtime/oracle agreement | Unauthorized mutations | Model calls | Input tokens | Cache-read tokens |
|---:|---:|---:|---:|---:|---:|---:|
| 6/6 | 6/6 | 6/6 | 0 | 38 | 255,225 | 193,792 |

These cases are deterministic runtime and evaluation contracts. The report
states the task set, model, commit, and raw efficiency observations explicitly
so results are reproducible and are not presented as a general software-
engineering success rate.

## Development

Run lint:

```bash
python -m ruff check . --no-cache
```

Run tests:

```bash
python -m pytest -q -p no:cacheprovider
```

On Windows, if pytest cannot create or clean its default temp/cache directory,
use a repository-local base temp directory:

```powershell
New-Item -ItemType Directory -Force -Path .tmp | Out-Null
$env:GIT_CEILING_DIRECTORIES=(Resolve-Path .tmp).Path
python -m pytest -q -p no:cacheprovider --basetemp=.tmp\pytest-full
```

The test suite covers tool semantics, permission behavior, verification
tracking, trace/report writing, recovery, context compaction, and interactive
state isolation.
