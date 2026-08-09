# Architecture

Local Coding Agent Harness is a small composition-oriented runtime. Its package
layout keeps orchestration, policy, tools, and output concerns separate without
adding a framework or dynamic plugin layer.

## Dependency Direction

```text
cli -> agent -> runtime
               ^
tools ---------+
```

- `cli` translates command-line arguments into `RunConfig` and starts the agent.
- `agent` owns model interaction, Anthropic message shape, and the agent loop.
- `runtime` owns session state, execution policy, hooks, context management, and
  observability.
- `tools` depend only on low-level runtime operation and security types. They do
  not import the agent.
- `runtime.bootstrap` is the composition root for concrete tools, hooks, the
  executor, context manager, and recovery policy.
- `runtime.plan` owns the optional plan state machine, lifecycle transitions,
  pre-permission side-effect gate, and atomic audit snapshot. It does not own
  filesystem permission decisions.
- `runtime.task` owns task lifecycle independently from loop-invocation
  control. A plan transition listener synchronizes only cross-domain
  invariants, without making the plan controller depend on the agent or CLI.

Runtime subpackages do not import `cli` or `agent`. `session_factory` receives
the already-rendered system prompt and initial messages from the agent layer,
which keeps runtime assembly independent from prompt construction.

## Main Paths

### Agent Loop

`src/agent/loop.py` sends the full Anthropic-compatible request, processes
assistant text and `tool_use` blocks, returns matching `tool_result` blocks, and
handles bounded recovery and stop-reason completion rules.

### Runtime Composition

`src/runtime/bootstrap.py` registers tools and hooks in explicit order.
`src/runtime/session_factory.py` creates the run directory and composes the
session's security and observability services.

### Tool Registration And Execution

`src/runtime/bootstrap.py::build_tool_registry` registers every concrete tool.
`src/runtime/executor.py` validates and executes one tool call through the hook
pipeline. Tool implementations remain one file per tool under `src/tools/`.

### Permission Decisions

`src/runtime/security/risk_classifier.py` classifies Bash effects.
`src/runtime/security/permission_gate.py` combines the operation, access policy,
permission mode, session rules, sandbox status, and approval response. The data
objects shared by these modules live in `permission_models.py`.

### Plan Decisions

`src/runtime/plan/models.py` separates user policy (`off`, `auto`, `required`),
the model-selected execution path (`undecided`, `direct`, `plan`), and plan
lifecycle phase. `controller.py` is the only writer of `PlanState`; tools and
CLI commands are thin adapters around its checked transitions.

Plan decision policy (`off`, `auto`, `required`) and approval policy (`manual`,
`auto`) are separate axes. Approval defaults to manual. A small capability
projection in `capabilities.py` is shared by tool visibility and the Plan Gate,
while the controller remains the authority for state transitions.

`ToolRegistry.schemas(context)` filters plan tools for the current state.
`select_execution_mode` is visible only in undecided auto mode, while
`update_plan` is visible while planning or executing. `resolve_plan_response`
is visible only while approval is pending and a fresh real-user continuation
exists. Existing tools keep their default availability.

The pre-tool path is deliberately ordered as:

```text
validation -> trace -> Plan Gate -> Permission Gate -> Tool.call -> post hooks
```

Validation remains tool-owned. The Plan Gate only decides whether the current
plan phase permits side effects; when it yields, the existing Permission Gate
still evaluates paths, command risk, user rules, and sandbox state. A blocked
plan call returns a structured `ToolResult`, records trace metadata, and is not
counted as a mutation, verification attempt, repair failure, or deterministic
invalid-call loop.

| State | Plan Gate behavior |
| --- | --- |
| `off` | Disabled; original tool flow is unchanged. |
| `auto + undecided` | Allows repository reads and mode selection; blocks Bash and mutations. |
| `planning` | Allows repository reads and plan updates; blocks Bash and mutations. |
| `awaiting_approval` | Allows bounded reads and blocks side effects; fresh user input enables only the response resolver. |
| `direct` | Yields to the existing Permission Gate. |
| `executing` | Yields to Permission Gate; plan state never auto-allows the call. |

`src/runtime/plan/store.py` atomically replaces `plan.json` after each active
state transition. In-memory `context.plan_state` remains authoritative during
the run. The JSON file is a bounded, redacted audit snapshot, not an arbitrary
crash-point or full conversation recovery format.

### Task Lifecycle

`TaskStatus` distinguishes `idle`, `running`, `waiting_user`, `completed`,
`failed`, and `cancelled`. Agent-loop `finished` is deliberately narrower: it
ends one invocation and may be true while the task remains `waiting_user`.
Interactive ordinary text continues a waiting task; only terminal tasks may be
archived before `start_task` creates a new task. Runtime notices use
`resume_runtime` and cannot masquerade as user authorization.

### Context Management

`src/runtime/context/manager.py` delegates token measurement, runtime checkpoint
construction, and tool-result projection to the neighboring context modules.
Consumed observations are projected before full pressure once the eager token
threshold is reached, while recent API rounds remain intact. Read-file
projections retain source path and line provenance. Full compaction and bounded
overflow recovery remain the final layers.

### Traces And Reports

`src/runtime/hooks/` records lifecycle, policy, mutation, verification, and tool
events. Writers and stores under `src/runtime/observability/` generate
`trace.jsonl`, `readable_trace.md`, `report.md`, `diff.patch`, `cost.json`, and
large-output artifacts in `<WORKDIR>/.agent/runs/<run_id>/`.

## Package Layout

```text
src/
|-- agent/
|-- cli/
|-- runtime/
|   |-- context/
|   |-- hooks/
|   |-- observability/
|   |-- plan/
|   `-- security/
`-- tools/
```

The `src` directory is a source root, not a Python package. Installed imports
remain `agent.*`, `runtime.*`, `tools.*`, and `cli.*`.
