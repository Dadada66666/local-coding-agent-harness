const now = "2026-08-27T10:24:35.998Z";

const sampleTurns = [
  {
    turn: 1,
    phase: "Planning",
    input: 3842,
    cacheRead: 2380,
    cacheWrite: 488,
    output: 624,
    epoch: "A",
    systemHash: "a1c9d3f2",
    toolsHash: "98fe61a0",
    contextGeneration: 0,
    categories: { tool_results: 914, user_messages: 1228, tool_schemas: 716, system_prompt: 706, assistant_tool_calls: 278 },
  },
  {
    turn: 2,
    phase: "Planning",
    input: 5972,
    cacheRead: 4110,
    cacheWrite: 612,
    output: 910,
    epoch: "A",
    systemHash: "a1c9d3f2",
    toolsHash: "98fe61a0",
    contextGeneration: 0,
    categories: { tool_results: 1960, user_messages: 1244, tool_schemas: 716, system_prompt: 706, assistant_tool_calls: 612 },
  },
  {
    turn: 3,
    phase: "Executing",
    input: 7318,
    cacheRead: 4958,
    cacheWrite: 804,
    output: 1216,
    epoch: "B",
    systemHash: "7e92b8a1",
    toolsHash: "b923ce11",
    contextGeneration: 0,
    categories: { tool_results: 2642, user_messages: 1288, tool_schemas: 1296, system_prompt: 732, assistant_tool_calls: 668 },
  },
  {
    turn: 4,
    phase: "Executing",
    input: 10486,
    cacheRead: 6720,
    cacheWrite: 1238,
    output: 1862,
    epoch: "B",
    systemHash: "7e92b8a1",
    toolsHash: "b923ce11",
    contextGeneration: 0,
    categories: { tool_results: 4356, user_messages: 2124, tool_schemas: 1812, system_prompt: 1096, assistant_tool_calls: 1098 },
  },
  {
    turn: 5,
    phase: "Executing",
    input: 7212,
    cacheRead: 5024,
    cacheWrite: 728,
    output: 1428,
    epoch: "C",
    systemHash: "b4f0c6e7",
    toolsHash: "b923ce11",
    contextGeneration: 1,
    categories: { tool_results: 2828, user_messages: 1368, tool_schemas: 1296, system_prompt: 732, assistant_tool_calls: 988 },
  },
  {
    turn: 6,
    phase: "Completed",
    input: 6857,
    cacheRead: 4408,
    cacheWrite: 902,
    output: 778,
    epoch: "C",
    systemHash: "b4f0c6e7",
    toolsHash: "b923ce11",
    contextGeneration: 1,
    categories: { tool_results: 2784, user_messages: 1298, tool_schemas: 1296, system_prompt: 716, assistant_tool_calls: 763 },
  },
];

const sampleEvents = [
  { id: "evt-01", ts: "2026-08-27T10:15:42.123Z", elapsed: 0, lane: "context", type: "task_transition", title: "Task created", summary: "Validation task entered the runtime.", status: "neutral", turn: 0 },
  { id: "evt-02", ts: "2026-08-27T10:15:45.210Z", elapsed: 3.1, lane: "plan", type: "plan_transition", title: "Plan draft", summary: "Three execution milestones were drafted.", status: "active", turn: 1, next: ["evt-03"] },
  { id: "evt-03", ts: "2026-08-27T10:15:47.881Z", elapsed: 5.8, lane: "permission", type: "plan_awaiting_approval", title: "Auto approval", summary: "Plan v12 approved by auto_policy.", status: "warning", turn: 1, causedBy: "evt-02", next: ["evt-04"] },
  { id: "evt-04", ts: "2026-08-27T10:15:48.302Z", elapsed: 6.2, lane: "model", type: "model_call_start", title: "Model call 1", summary: "Execution started with approved plan context.", status: "active", turn: 1, causedBy: "evt-03", next: ["evt-05"] },
  { id: "evt-05", ts: "2026-08-27T10:15:49.112Z", elapsed: 7, lane: "tool", type: "tool_use", title: "read_file", summary: "Read service.py lines 1–184.", status: "success", turn: 1, causedBy: "evt-04", next: ["evt-06"] },
  { id: "evt-06", ts: "2026-08-27T10:15:53.001Z", elapsed: 10.9, lane: "model", type: "model_call_end", title: "Model call 2", summary: "Root cause narrowed to normalized payload handling.", status: "active", turn: 2, causedBy: "evt-05", next: ["evt-07"] },
  { id: "evt-07", ts: "2026-08-27T10:16:05.443Z", elapsed: 23.3, lane: "tool", type: "tool_use", title: "edit_file", summary: "Applied exact replacement in models.py.", status: "success", turn: 2, causedBy: "evt-06", next: ["evt-08"] },
  { id: "evt-08", ts: "2026-08-27T10:16:08.214Z", elapsed: 26.1, lane: "context", type: "tool_result_budget", title: "Result admitted", summary: "4,812 tokens admitted under the 12K hard bound.", status: "neutral", turn: 2, causedBy: "evt-07", next: ["evt-09"] },
  { id: "evt-09", ts: "2026-08-27T10:16:16.789Z", elapsed: 34.7, lane: "tool", type: "tool_use", title: "pytest -q", summary: "Authoritative foreground verification.", status: "active", turn: 3, causedBy: "evt-08", next: ["evt-10"] },
  { id: "evt-10", ts: "2026-08-27T10:16:17.123Z", elapsed: 35, lane: "verification", type: "test_result", title: "Verification failed", summary: "3 tests failed; 25 passed.", status: "failure", turn: 3, command: "pytest -q", details: "3 tests failed.\nSee the related tool call for redacted output.", causedBy: "evt-09", next: ["evt-11"] },
  { id: "evt-11", ts: "2026-08-27T10:16:37.456Z", elapsed: 55.3, lane: "plan", type: "plan_transition", title: "Recovery injected", summary: "Bounded recovery requested from authoritative failure evidence.", status: "warning", turn: 4, causedBy: "evt-10", next: ["evt-12"] },
  { id: "evt-12", ts: "2026-08-27T10:16:41.006Z", elapsed: 58.9, lane: "tool", type: "tool_use", title: "edit_file", summary: "Corrected parser default propagation.", status: "success", turn: 4, causedBy: "evt-11", next: ["evt-13"] },
  { id: "evt-13", ts: "2026-08-27T10:16:59.004Z", elapsed: 76.9, lane: "model", type: "model_call_end", title: "Model call 5", summary: "Repair evidence consumed; verification prepared.", status: "active", turn: 4, causedBy: "evt-12", next: ["evt-14"] },
  { id: "evt-14", ts: "2026-08-27T10:17:15.982Z", elapsed: 93.9, lane: "tool", type: "tool_use", title: "pytest -q", summary: "Full suite rerun after bounded repair.", status: "active", turn: 5, causedBy: "evt-13", next: ["evt-15"] },
  { id: "evt-15", ts: "2026-08-27T10:17:57.142Z", elapsed: 135, lane: "verification", type: "test_result", title: "Verification passed", summary: "All 28 tests passed.", status: "success", turn: 5, command: "pytest -q", details: "28 passed in 0.42s", causedBy: "evt-14", next: ["evt-16"] },
  { id: "evt-16", ts: "2026-08-27T10:18:21.087Z", elapsed: 159, lane: "model", type: "final_response", title: "Final response", summary: "Outcome and verification facts reported.", status: "success", turn: 6, causedBy: "evt-15", next: ["evt-17"] },
  { id: "evt-17", ts: now, elapsed: 533.9, lane: "context", type: "task_transition", title: "Task completed", summary: "Runtime finished cleanly.", status: "success", turn: 6, causedBy: "evt-16" },
];

export const sampleRun = {
  id: "20260827-052605-c65c5249",
  taskId: "task_failed_verification_recovery",
  task: "Repair the normalization regression and verify the complete suite.",
  status: "Completed",
  runtimeSuccess: true,
  verification: "Passed",
  model: "gpt-5.6-terra",
  startedAt: "2026-08-27T10:15:42.123Z",
  finishedAt: now,
  duration: "00:08:54",
  importedAt: "2026-08-27 10:25:02",
  sourcePath: "/runs/20260827-052605-c65c5249/",
  turns: sampleTurns,
  events: sampleEvents,
  source: { readCalls: 13, uniqueLines: 1936, duplicateLines: 85, rehydratedLines: 79 },
  context: { autoTrigger: 244800, hardLimit: 251904, fullCompactions: 0, rebases: 0 },
  artifacts: 0,
  toolFailures: 1,
  repairs: 1,
};

const laneByType = (type = "") => {
  if (type.startsWith("model_") || type === "final_response") return "model";
  if (type.startsWith("plan_")) return "plan";
  if (type.startsWith("permission_") || type === "operation_classified") return "permission";
  if (type === "test_result" || type === "verification_ignored") return "verification";
  if (type.startsWith("tool_") || type.startsWith("mcp_")) return "tool";
  return "context";
};

const titleFromType = (type = "event") =>
  type
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");

const first = (...values) => values.find((value) => value !== undefined && value !== null && value !== "");

export function parseTraceJsonl(text) {
  const rows = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        throw new Error(`trace.jsonl 第 ${index + 1} 行不是有效 JSON：${error.message}`);
      }
    });

  return rows.map((row, index) => {
    const type = first(row.type, row.event_type, "event");
    const status =
      type.includes("failed") || type.includes("error") || row.success === false
        ? "failure"
        : type === "test_result" && first(row.result, row.status) === "failed"
          ? "failure"
          : type.includes("approval") || type.includes("recovery")
            ? "warning"
            : type.includes("completed") || row.success === true
              ? "success"
              : "neutral";
    return {
      ...row,
      id: first(row.event_id, row.id, `event-${index + 1}`),
      ts: first(row.ts_iso, row.timestamp, row.ts ? new Date(row.ts * 1000).toISOString() : null, new Date().toISOString()),
      elapsed: Number(first(row.elapsed_ms && row.elapsed_ms / 1000, row.elapsed, index)),
      lane: laneByType(type),
      type,
      title: first(row.title, row.tool_name, row.command, titleFromType(type)),
      summary: first(row.summary, row.message, row.reason, row.error, row.decision_reason, "Runtime event"),
      status,
      turn: Number(first(row.turn_id, row.turn, 0)),
      command: first(row.command, row.metadata?.command),
      details: first(row.details, row.content, row.output, row.error),
    };
  });
}

function normalizeBreakdown(turn) {
  const breakdown = turn.input_breakdown || {};
  return Object.fromEntries(
    Object.entries(breakdown).map(([key, value]) => [
      key,
      Number(value?.estimated_tokens ?? value?.tokens ?? value ?? 0),
    ]),
  );
}

export function parseCostJson(text) {
  let cost;
  try {
    cost = JSON.parse(String(text || "{}"));
  } catch (error) {
    throw new Error(`cost.json 不是有效 JSON：${error.message}`);
  }
  const rawTurns = cost.token_breakdown?.turns || cost.turns || [];
  let previousKey = null;
  let epochIndex = -1;
  const turns = rawTurns.map((turn, index) => {
    const prefix = turn.request_prefix || {};
    const key = `${prefix.system_hash || "unknown"}:${prefix.tools_hash || "unknown"}:${prefix.context_generation || 0}`;
    if (key !== previousKey) {
      epochIndex += 1;
      previousKey = key;
    }
    return {
      turn: Number(turn.turn_id ?? index + 1),
      phase: prefix.plan_phase || "Inactive",
      input: Number(turn.input_tokens || 0),
      cacheRead: Number(turn.cache_read_input_tokens || 0),
      cacheWrite: Number(turn.cache_creation_input_tokens || 0),
      output: Number(turn.output_tokens || 0),
      epoch: String.fromCharCode(65 + Math.min(epochIndex, 25)),
      systemHash: String(prefix.system_hash || "unknown").slice(0, 8),
      toolsHash: String(prefix.tools_hash || "unknown").slice(0, 8),
      contextGeneration: Number(prefix.context_generation || 0),
      messagesPreserved: prefix.previous_messages_preserved,
      categories: normalizeBreakdown(turn),
    };
  });
  return { cost, turns };
}

export function buildImportedRun(traceText, costText, names = {}) {
  const events = parseTraceJsonl(traceText);
  const parsedCost = costText ? parseCostJson(costText) : { cost: {}, turns: [] };
  const cost = parsedCost.cost;
  const traceRunId = events.find((event) => event.run_id)?.run_id;
  const startedAt = events[0]?.ts || new Date().toISOString();
  const finishedAt = events.at(-1)?.ts || startedAt;
  const durationSeconds = Math.max((new Date(finishedAt) - new Date(startedAt)) / 1000, 0);
  const duration = new Date(durationSeconds * 1000).toISOString().slice(11, 19);
  const turns = parsedCost.turns.length
    ? parsedCost.turns
    : events
        .filter((event) => event.type === "model_usage")
        .map((event, index) => ({
          turn: Number(event.turn_id || index + 1),
          phase: "Unknown",
          input: Number(event.input_tokens || 0),
          cacheRead: Number(event.cache_read_input_tokens || 0),
          cacheWrite: Number(event.cache_creation_input_tokens || 0),
          output: Number(event.output_tokens || 0),
          epoch: "A",
          systemHash: "unknown",
          toolsHash: "unknown",
          contextGeneration: 0,
          categories: {},
        }));
  const verificationEvents = events.filter((event) => event.type === "test_result");
  const lastVerification = verificationEvents.at(-1);
  const statusEvent = [...events].reverse().find((event) => event.type === "task_transition" || event.type === "stop");
  return {
    id: traceRunId || names.trace?.replace(/\.[^.]+$/, "") || "imported-run",
    taskId: first(events.find((event) => event.task_id)?.task_id, "task-imported"),
    task: first(events.find((event) => event.type === "user_prompt")?.content, "Imported agent run"),
    status: statusEvent?.status === "failure" ? "Failed" : "Completed",
    runtimeSuccess: !events.some((event) => event.type === "run_aborted" || event.type === "max_turns_exceeded"),
    verification: lastVerification ? (lastVerification.status === "failure" ? "Failed" : "Passed") : "Not recorded",
    model: first(cost.current_task?.model, cost.model, "Provider model"),
    startedAt,
    finishedAt,
    duration,
    importedAt: new Date().toLocaleString(),
    sourcePath: names.trace || "trace.jsonl",
    turns,
    events,
    source: {
      readCalls: Number(cost.source_read_efficiency?.read_file_calls || 0),
      uniqueLines: Number(cost.source_read_efficiency?.unique_source_lines || 0),
      duplicateLines: Number(cost.source_read_efficiency?.duplicate_source_lines || 0),
      rehydratedLines: Number(cost.source_read_efficiency?.rehydrated_source_lines || 0),
    },
    context: {
      autoTrigger: 244800,
      hardLimit: 251904,
      fullCompactions: Number(cost.context_management?.full_history_compactions || 0),
      rebases: Number(cost.context_management?.full_history_compactions || 0),
    },
    artifacts: Number(cost.artifacts?.created || 0),
    toolFailures: events.filter((event) => event.type === "tool_result" && event.status === "failure").length,
    repairs: events.filter((event) => String(event.title).toLowerCase().includes("recovery")).length,
  };
}

export const lanes = ["model", "plan", "permission", "tool", "verification", "context"];

export const laneLabels = {
  model: "Model",
  plan: "Plan",
  permission: "Permission",
  tool: "Tool",
  verification: "Verification",
  context: "Context",
};

