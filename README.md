# Local Coding Agent Harness

Local Coding Agent Harness is a local runtime for controlled coding agents.

V1 focuses on a single main agent loop that can understand a task, inspect a
local repository, edit files through guarded tools, run tests, repair failures,
and emit run artifacts:

- `trace.jsonl`
- `report.md`
- `diff.patch`
- `cost.json`
- large tool outputs under `artifacts/`

## V1 Scope

Implemented boundaries are organized around these responsibilities:

- `agent/`: agent loop, context, prompts, model client interface, message types
- `tools/`: tool definitions and registry
- `runtime/`: execution, permissions, hooks, context management, tracing, cost,
  diffs, recovery, reports
- `cli/`: Typer-based command entrypoint
- `examples/`: local demo repository for future end-to-end runs

V1 intentionally avoids sub-agents, MCP, background scheduling, worktree
isolation, external plugin systems, YAML hook configuration, and LangGraph
adapters.

## Configuration

Create `.env` from `.env.example`:

```bash
ANTHROPIC_API_KEY=...
MODEL_ID=deepseek-v4-pro
ANTHROPIC_BASE_URL=https://ai.yxkl.cloud
```

The model adapter uses the Anthropic Messages API shape:

- top-level `system`
- `messages=[...]`
- `tools=[...]`
- assistant `tool_use` blocks
- user `tool_result` blocks

## Development

```bash
pip install -e ".[dev]"
python -m cli.main
```

The current terminal directory is used as WORKDIR. Choose a permission mode at startup, then type tasks at the `agent:` prompt. Enter `q` or `exit` to stop. Reports are written to:

```bash
<repo>/.agent/runs/<run_id>/report.md
```

You can read a previous report with:

```bash
python -m cli.main report <run_id>
```

## Sandbox Runtime

Bash commands can be wrapped with Anthropic Sandbox Runtime (`srt`) for an
extra local execution boundary:

```bash
npm install -g @anthropic-ai/sandbox-runtime
srt --version
```

Run the harness with sandbox wrapping enabled:

```bash
python -m cli.main run --sandbox
```

Useful options:

- `--sandbox-fail-if-unavailable`: stop startup if `srt` cannot actually run.
- `--sandbox-settings <path>`: use a custom `srt-settings.json`.
- `--sandbox-auto-allow/--no-sandbox-auto-allow`: control whether unknown bash
  commands may be auto-allowed when a strong sandbox is available.

The runtime does not trust package presence alone. On startup it resolves the
real `srt` executable and runs a small probe command. On Linux/macOS the probe
and wrapped BashTool commands use `--settings <srt-settings.json>`. On Windows
they intentionally run without `--settings` to avoid current srt-win ACL stamp
instability. If the probe fails, sandbox metadata is still written to
trace/report, but BashTool falls back to normal execution and permission checks
stay active.

On Windows, complete the one-time SRT provisioning step before expecting `srt`
to run commands:

```bash
srt windows-install
```

This step may require UAC/admin rights. Windows support in SRT is still treated
as a weak boundary by this project, so unknown bash commands are not
auto-approved on Windows even when `srt` is installed. Destructive commands,
network commands, and file writes through shell remain controlled by
`PermissionGate`.
