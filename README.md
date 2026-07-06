# Local Coding Agent Harness

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
ANTHROPIC_BASE_URL=
```

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
- `create_file`: create a new UTF-8 file. Existing files fail as tool semantics,
  not as permission denials. Successful creates update the file snapshot.
- `edit_file`: replace exact text in a previously known file snapshot. It
  supports one replacement with `old_text` / `new_text`, or multiple
  replacements with `edits`. Repeated matches remain ambiguous by default;
  `occurrence` targets a specific match and `replace_all` explicitly replaces
  every match. Batch edits are atomic.
- `bash`: run verification or inspection commands from `WORKDIR`. Commands can
  carry `purpose="verify"` so verification results are reflected in report
  success.
- `view_diff`: show git diff when `WORKDIR` is a git repository; non-git
  directories return a clean "diff unavailable" result.

File tools are constrained by `AgentContext.safe_path()`, so reads and writes
cannot escape `WORKDIR`.

## Runtime Behavior

Important runtime properties:

- File edits require a known snapshot from `read_file`, `create_file`, or a
  successful prior `edit_file`.
- Successful edits refresh the snapshot, so multiple edits to the same file do
  not require unnecessary rereads.
- No-op edits return success without marking the file changed.
- Interactive sessions separate whole-run state from current-task state. A
  previous prompt's failed verification or changed files cannot poison the next
  prompt's success inference.
- Context compaction avoids leaving orphan `tool_result` messages without their
  corresponding `tool_use`.
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
- `report.md`: status, changed files, verification, sandbox, cost, artifacts
- `diff.patch`: git diff, or a clean non-git placeholder
- `cost.json`: model usage plus estimated per-turn token breakdown
- `artifacts/`: persisted large tool outputs

`cost.json` breaks model input/output into categories such as system prompt,
tool schemas, user messages, assistant tool calls, tool results, compacted
history, assistant text, and tool calls. The breakdown is local estimation for
optimization; provider usage remains the billing source of truth.

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

Linux/macOS:

- sandbox settings are applied with `srt --settings <settings_path> ...`
- a successful probe is treated as a strong boundary

Windows:

- commands are wrapped as `srt <real-shell-argv...>` without `--settings`
- the runtime treats this as a weak boundary
- unknown bash commands are not auto-approved just because `srt` is installed

The sandbox is an execution boundary, not a replacement for `PermissionGate`.
Destructive commands, network commands, protected paths, and shell-based file
writes remain controlled by runtime permission checks.

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
