# Local Coding Agent Harness

[English](README.md) | [中文](README.zh-CN.md)

Local Coding Agent Harness is a local runtime for coding agents that work on a
real repository through controlled tools instead of direct filesystem access.

The runtime is intentionally small, but it covers the core loop expected from a
coding agent:

- understand a user task
- inspect the working directory
- read and edit files through guarded tools
- run verification commands
- retry after failed verification
- write trace, report, diff, and cost artifacts

## Current Scope

This project focuses on a single local agent loop. It does not implement
sub-agents, MCP, background jobs, plugin discovery, worktree isolation, or
LangGraph adapters.

Core directories:

- `agent/`: loop, context state, prompts, model client, message conversion
- `tools/`: tool implementations and registry
- `runtime/`: permissions, hooks, sandbox integration, tracing, artifacts,
  context compaction, recovery, reports, cost tracking
- `cli/`: Typer CLI
- `tests/`: unit and runtime behavior tests
- `examples/`: demo repository fixtures

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

Set `MODEL_CONTEXT_WINDOW_TOKENS` when the provider does not expose the model's
window size. This enables token-budget compaction; without it, the runtime keeps
the compatible character-threshold fallback.

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
python -m cli.main
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
  Non-UTF-8 files return a normal tool failure instead of an unhandled decode
  exception.
- `read_artifact`: retrieve a bounded slice of a large tool result through an
  opaque ID scoped to the current run; it does not accept filesystem paths.
- `write_file`: write a new UTF-8 file. Existing files fail as tool semantics,
  not as permission denials; use `edit_file` for existing files. Successful
  writes update the file snapshot.
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
- `max_turns` limits model calls per task, while trace turn IDs remain unique
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
- Context pressure includes the system prompt, tool schemas, messages, reserved
  output, and a safety margin. Provider usage anchors the local estimator when
  available. Capacity pressure and the default 32K economic context target use
  the lower limit; a character threshold remains the compatibility fallback.
- Context reduction is layered: aggregate tool-result budgets persist large
  observations first, consumed old observations become retrieval references,
  and full compaction writes a bounded runtime checkpoint while preserving
  complete recent API rounds. The append-only conversation audit is unchanged.
- Interactive task boundaries compact completed history above a token threshold
  into a deterministic checkpoint before the next task starts. The current
  prompt and the append-only audit remain intact.
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

When the model runs a command to validate behavior, it should set:

```json
{"purpose": "verify"}
```

The runtime records verification results from:

- known test commands such as `pytest`, `unittest`, and `npm test`
- any `bash` command with `purpose="verify"`

Read-only discovery commands such as `find`, `git status`, `git diff`, `ls`,
`rg`, and `grep` are not treated as verification even if labeled `verify`.
Commands that explicitly mutate files are also excluded from verification;
perform the edit with a structured file tool, then verify it in a separate
`bash` call.

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
