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

## Development

```bash
pip install -e ".[dev]"
python -m cli.main run --repo examples/demo_repo --task "Fix the failing tests"
```

The model client is currently a seam for later implementation. The structure is
ready for wiring OpenAI-compatible model calls, controlled tools, trace logging,
and repair loops.

