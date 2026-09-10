import assert from "node:assert/strict";
import test from "node:test";

import { buildImportedRun } from "../src/data.js";

const jsonl = (rows) => rows.map((row) => JSON.stringify(row)).join("\n");

test("actual runtime fields produce failed outcome, verification, and tool status", () => {
  const run = buildImportedRun(jsonl([
    { type: "task_transition", before: "idle", after: "running", ts_iso: "2026-08-27T00:00:00Z" },
    { type: "model_call_start", context_auto_compact_trigger: 123456, context_hard_input_limit: null, ts_iso: "2026-08-27T00:00:01Z" },
    { type: "tool_result", tool: "bash", ok: false, error: "boom", ts_iso: "2026-08-27T00:00:02Z" },
    { type: "test_result", ok: false, command: "pytest", ts_iso: "2026-08-27T00:00:03Z" },
    { type: "task_transition", before: "running", after: "failed", ts_iso: "2026-08-27T00:00:04Z" },
  ]));

  assert.equal(run.status, "Failed");
  assert.equal(run.runtimeSuccess, false);
  assert.equal(run.verification, "Failed");
  assert.equal(run.toolFailures, 1);
  assert.equal(run.events.find((event) => event.type === "tool_result").status, "failure");
  assert.equal(run.events.find((event) => event.type === "tool_result").summary, "boom");
  assert.equal(run.events.find((event) => event.type === "test_result").summary, "Failed: pytest.");
  assert.equal(run.context.autoTrigger, 123456);
  assert.equal(run.context.hardLimit, null);
  assert.equal(run.lifecycle.at(-1).label, "Failed");
  assert.equal(run.lifecycle.at(-1).ts, "2026-08-27T00:00:04Z");
});

test("non-terminal and unknown verification evidence are never reported as success", () => {
  const run = buildImportedRun(jsonl([
    { type: "task_transition", before: "idle", after: "running", ts_iso: "2026-08-27T00:00:00Z" },
    { type: "test_result", command: "pytest", ts_iso: "2026-08-27T00:00:01Z" },
  ]));

  assert.equal(run.status, "Running");
  assert.equal(run.runtimeSuccess, false);
  assert.equal(run.verification, "Not recorded");
});

test("lifecycle phases come from serialized plan transitions", () => {
  const run = buildImportedRun(jsonl([
    { type: "task_transition", before: "idle", after: "running", ts_iso: "2026-08-27T00:00:00Z" },
    { type: "plan_transition", before: { phase: "inactive" }, after: { phase: "planning" }, ts_iso: "2026-08-27T00:00:01Z" },
    { type: "plan_transition", before: { phase: "planning" }, after: { phase: "awaiting_approval" }, ts_iso: "2026-08-27T00:00:02Z" },
    { type: "plan_transition", before: { phase: "awaiting_approval" }, after: { phase: "executing" }, ts_iso: "2026-08-27T00:00:03Z" },
    { type: "plan_transition", before: { phase: "executing" }, after: { phase: "completed" }, ts_iso: "2026-08-27T00:00:04Z" },
    { type: "task_transition", before: "running", after: "completed", ts_iso: "2026-08-27T00:00:05Z" },
  ]));

  assert.deepEqual(run.lifecycle.map((phase) => phase.label), [
    "Inactive",
    "Planning",
    "Awaiting Approval",
    "Executing",
    "Completed",
  ]);
  assert.equal(run.lifecycle[3].ts, "2026-08-27T00:00:03Z");
  assert.equal(run.status, "Completed");
  assert.equal(run.runtimeSuccess, true);
});

test("cost context metrics use current serialized field names", () => {
  const trace = jsonl([
    { type: "task_transition", before: "idle", after: "running", ts_iso: "2026-08-27T00:00:00Z" },
    { type: "task_transition", before: "running", after: "completed", ts_iso: "2026-08-27T00:00:01Z" },
  ]);
  const cost = JSON.stringify({ context_management: { full_rebase_events: 2, hard_input_limit_tokens: 250000 } });
  const run = buildImportedRun(trace, cost);

  assert.equal(run.context.rebases, 2);
  assert.equal(run.context.fullCompactions, 2);
  assert.equal(run.context.hardLimit, 250000);
});
