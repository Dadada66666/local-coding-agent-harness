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
python -m cli.main run "Fix the failing tests" --repo examples/demo_repo --permission accept_edits
```

Reports are written to:

```bash
<repo>/.agent/runs/<run_id>/report.md
```

You can read a previous report with:

```bash
python -m cli.main report <run_id> --repo examples/demo_repo
```
