import { useMemo, useRef, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  BookmarkSimple,
  Bug,
  Check,
  CheckCircle,
  Clock,
  Code,
  FileCode,
  FileText,
  FolderOpen,
  Funnel,
  GitBranch,
  Graph,
  Heartbeat,
  Info,
  Lightning,
  LinkSimple,
  ListMagnifyingGlass,
  LockSimple,
  MagnifyingGlass,
  Minus,
  Pulse,
  ShieldCheck,
  Sparkle,
  Stack,
  Target,
  TerminalWindow,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { buildImportedRun, laneLabels, lanes, sampleRun } from "./data";
import { TraceView } from "./TraceView";

const number = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

const categoryMeta = {
  tool_results: { label: "Tool Results", color: "#146ee8" },
  user_messages: { label: "User Messages", color: "#149eaa" },
  tool_schemas: { label: "Tool Schemas", color: "#f26b21" },
  assistant_tool_calls: { label: "Assistant Tool Calls", color: "#efbd2e" },
  system_prompt: { label: "System Prompt", color: "#8b5bd7" },
  compacted_history: { label: "Compacted History", color: "#9ca3af" },
};

const laneIcons = {
  model: Sparkle,
  plan: FileText,
  permission: ShieldCheck,
  tool: TerminalWindow,
  verification: CheckCircle,
  context: Stack,
};

function formatTime(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return String(value);
  }
}

function fileState(name, available) {
  return (
    <span className={`file-state ${available ? "loaded" : "optional"}`}>
      {name}
      {available ? <Check size={13} weight="bold" /> : <span>optional</span>}
    </span>
  );
}

function StatusDot({ status = "success" }) {
  return <span className={`status-dot ${status}`} aria-hidden="true" />;
}

function Metric({ label, value, tone, suffix }) {
  return (
    <div className="metric">
      <span>{label}</span>
      <strong className={tone || ""}>{value}</strong>
      {suffix && <small>{suffix}</small>}
    </div>
  );
}

function AppHeader({ view, onView, run, onOpen, loaded }) {
  return (
    <header className="app-header">
      <div className="brand-lockup">
        <div className="brand-mark"><Pulse size={19} weight="bold" /></div>
        <div>
          <strong>Agent Trace Studio</strong>
          <span>local runtime observatory</span>
        </div>
      </div>
      <nav className="view-switch" aria-label="Primary views">
        <button className={view === "economics" ? "active" : ""} onClick={() => onView("economics")}>
          <Graph size={17} /> Economics
        </button>
        <button className={view === "lifecycle" ? "active" : ""} onClick={() => onView("lifecycle")}>
          <GitBranch size={17} /> Lifecycle
        </button>
        <button className={view === "trace" ? "active" : ""} onClick={() => onView("trace")}>
          <ListMagnifyingGlass size={17} /> Trace
        </button>
      </nav>
      <div className="header-actions">
        {fileState("trace.jsonl", loaded.trace)}
        {fileState("cost.json", loaded.cost)}
        <span className="local-label"><LockSimple size={14} /> Local only</span>
        <button className="primary-button" onClick={onOpen} title="Choose trace.jsonl and optional cost.json">
          <FolderOpen size={17} /> Open run
        </button>
      </div>
    </header>
  );
}

function RunSummary({ run }) {
  const input = run.turns.reduce((sum, turn) => sum + turn.input, 0);
  const cache = run.turns.reduce((sum, turn) => sum + turn.cacheRead, 0);
  const output = run.turns.reduce((sum, turn) => sum + turn.output, 0);
  return (
    <section className="run-summary">
      <div className="run-picker">
        <span>Run</span>
        <div className="run-value" title={run.id}>
          <span>{run.id}</span><CheckCircle size={14} weight="fill" />
        </div>
      </div>
      <Metric label="Status" value={<><StatusDot /> {run.status}</>} />
      <Metric label="Model" value={run.model} />
      <Metric label="Duration" value={run.duration} />
      <Metric label="Model calls" value={run.turns.length} />
      <Metric label="Input tokens" value={number.format(input)} tone="blue" />
      <Metric label="Cache-read" value={number.format(cache)} tone="teal" />
      <Metric label="Output tokens" value={number.format(output)} tone="plum" />
      <Metric label="Tool failures" value={run.toolFailures} tone={run.toolFailures ? "orange" : ""} />
      <Metric label="Full rebases" value={run.context.rebases} />
    </section>
  );
}

function EpochStrip({ turns, selectedTurn, onSelect }) {
  const groups = [];
  turns.forEach((turn) => {
    const last = groups.at(-1);
    if (!last || last.epoch !== turn.epoch) {
      groups.push({ epoch: turn.epoch, hash: turn.systemHash, start: turn.turn, turns: [turn.turn] });
    } else {
      last.turns.push(turn.turn);
    }
  });
  return (
    <div className="epoch-strip" style={{ gridTemplateColumns: `repeat(${Math.max(turns.length, 1)}, 1fr)` }}>
      {groups.map((group) => (
        <button
          key={`${group.epoch}-${group.start}`}
          className={group.turns.includes(selectedTurn) ? "selected" : ""}
          style={{ gridColumn: `${group.start} / span ${group.turns.length}` }}
          onClick={() => onSelect(group.start)}
          title="Open the first turn in this stable prefix epoch"
        >
          <span>Epoch {group.epoch}</span>
          <small>hash: {group.hash}</small>
        </button>
      ))}
    </div>
  );
}

function TokenTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const turn = payload[0]?.payload;
  return (
    <div className="chart-tooltip">
      <strong>Turn {label} · {turn.phase}</strong>
      <span>Input {number.format(turn.input)}</span>
      <span>Cache-read {number.format(turn.cacheRead)}</span>
      <span>Uncached {number.format(Math.max(turn.input - turn.cacheRead, 0))}</span>
      <span>Output {number.format(turn.output)}</span>
    </div>
  );
}

function TokenChart({ turns, selectedTurn, onSelect }) {
  const data = turns.map((turn) => ({ ...turn, uncached: Math.max(turn.input - turn.cacheRead, 0) }));
  return (
    <div className="token-plot" aria-label="Turn-by-turn token usage chart">
      <EpochStrip turns={turns} selectedTurn={selectedTurn} onSelect={onSelect} />
      <ResponsiveContainer width="100%" height={300}>
        <BarChart data={data} margin={{ top: 12, right: 12, left: 0, bottom: 0 }} barCategoryGap="32%">
          <CartesianGrid stroke="#e8e9ec" vertical={false} strokeDasharray="2 3" />
          <XAxis dataKey="turn" axisLine={false} tickLine={false} tick={{ fill: "#606773", fontSize: 12 }} />
          <YAxis axisLine={false} tickLine={false} width={42} tickFormatter={(value) => compact.format(value)} tick={{ fill: "#7b818b", fontSize: 11 }} />
          <Tooltip content={<TokenTooltip />} cursor={{ fill: "rgba(20,110,232,.055)" }} />
          <Bar dataKey="cacheRead" stackId="tokens" fill="#149eaa" radius={[0, 0, 0, 0]} onClick={(dataPoint) => onSelect(dataPoint.turn)}>
            {data.map((item) => <Cell key={`cache-${item.turn}`} opacity={item.turn === selectedTurn ? 1 : 0.78} />)}
          </Bar>
          <Bar dataKey="uncached" stackId="tokens" fill="#146ee8" onClick={(dataPoint) => onSelect(dataPoint.turn)}>
            {data.map((item) => <Cell key={`input-${item.turn}`} opacity={item.turn === selectedTurn ? 1 : 0.78} />)}
          </Bar>
          <Bar dataKey="output" stackId="tokens" fill="#8b5bd7" radius={[4, 4, 0, 0]} onClick={(dataPoint) => onSelect(dataPoint.turn)}>
            {data.map((item) => <Cell key={`output-${item.turn}`} opacity={item.turn === selectedTurn ? 1 : 0.78} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}

function Legend() {
  return (
    <div className="legend-row">
      <span><i className="legend-swatch teal" /> Cache-read</span>
      <span><i className="legend-swatch blue" /> Uncached input</span>
      <span><i className="legend-swatch plum" /> Output</span>
      <span><i className="legend-line" /> Stable system/tools epoch</span>
    </div>
  );
}

function TurnTable({ turns, selectedTurn, onSelect }) {
  const totals = turns.reduce((acc, turn) => ({
    input: acc.input + turn.input,
    cache: acc.cache + turn.cacheRead,
    uncached: acc.uncached + Math.max(turn.input - turn.cacheRead, 0),
    output: acc.output + turn.output,
  }), { input: 0, cache: 0, uncached: 0, output: 0 });
  return (
    <div className="turn-table-wrap">
      <table className="turn-table">
        <thead>
          <tr><th>Turn</th><th>Phase</th><th>Input tokens</th><th>Cache-read</th><th>Uncached</th><th>Output</th><th>Prefix</th></tr>
        </thead>
        <tbody>
          {turns.map((turn) => (
            <tr key={turn.turn} className={turn.turn === selectedTurn ? "selected" : ""} onClick={() => onSelect(turn.turn)} tabIndex={0} onKeyDown={(event) => event.key === "Enter" && onSelect(turn.turn)}>
              <td><ArrowRight size={13} className="row-arrow" /> {turn.turn}</td>
              <td>{turn.phase}</td>
              <td className="blue">{number.format(turn.input)}</td>
              <td className="teal">{number.format(turn.cacheRead)}</td>
              <td className="orange">{number.format(Math.max(turn.input - turn.cacheRead, 0))}</td>
              <td className="plum">{number.format(turn.output)}</td>
              <td><span className="epoch-pill">Epoch {turn.epoch}</span></td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr><td colSpan="2">Total</td><td className="blue">{number.format(totals.input)}</td><td className="teal">{number.format(totals.cache)}</td><td className="orange">{number.format(totals.uncached)}</td><td className="plum">{number.format(totals.output)}</td><td>—</td></tr>
        </tfoot>
      </table>
    </div>
  );
}

function TurnInspector({ turn }) {
  const categories = Object.entries(turn?.categories || {})
    .map(([key, value]) => ({ key, value: Number(value || 0), ...(categoryMeta[key] || { label: key.replaceAll("_", " "), color: "#9ca3af" }) }))
    .sort((a, b) => b.value - a.value);
  const total = categories.reduce((sum, category) => sum + category.value, 0) || turn?.input || 1;
  return (
    <aside className="economics-inspector">
      <div className="section-heading accent-blue">
        <div><small>Selected spike</small><h2>Turn {turn?.turn}</h2></div>
        <span className="token-badge">{number.format(turn?.input || 0)} input</span>
      </div>
      <div className="why-card">
        <h3>Why this turn was expensive</h3>
        <p>Local category estimates explain which serialized inputs dominated this request.</p>
        <div className="category-list">
          {categories.map((category) => (
            <div className="category-row" key={category.key}>
              <span className="category-dot" style={{ background: category.color }} />
              <span>{category.label}</span>
              <strong>{number.format(category.value)}</strong>
              <small>{((category.value / total) * 100).toFixed(1)}%</small>
            </div>
          ))}
        </div>
        <div className="info-note"><Info size={17} /> Estimates are computed locally from trace metadata and token counts.</div>
      </div>
    </aside>
  );
}

function PressureChart({ run, selectedTurn }) {
  const data = run.turns.map((turn) => ({ turn: turn.turn, input: turn.input }));
  const peak = Math.max(...data.map((item) => item.input), 0);
  const localCeiling = Math.max(Math.ceil(peak * 1.25 / 1000) * 1000, 12000);
  return (
    <section className="pressure-section">
      <div className="section-heading compact accent-teal">
        <div><small>Context pressure</small><h2>Input tokens by turn</h2></div>
        <div className="peak-stat"><span>Peak input</span><strong>{number.format(peak)}</strong></div>
      </div>
      <div className="pressure-chart">
        <ResponsiveContainer width="100%" height={170}>
          <LineChart data={data} margin={{ top: 10, right: 12, left: -10, bottom: 0 }}>
            <CartesianGrid stroke="#eaebed" vertical={false} strokeDasharray="2 3" />
            <XAxis dataKey="turn" axisLine={false} tickLine={false} tick={{ fontSize: 11, fill: "#747b85" }} />
            <YAxis domain={[0, localCeiling]} axisLine={false} tickLine={false} tickFormatter={(value) => compact.format(value)} tick={{ fontSize: 10, fill: "#747b85" }} />
            <ReferenceLine y={localCeiling * 0.8} stroke="#146ee8" strokeDasharray="5 4" />
            <ReferenceLine y={localCeiling} stroke="#e65044" strokeDasharray="2 3" />
            <Line type="monotone" dataKey="input" stroke="#146ee8" strokeWidth={2.4} dot={{ r: 3, fill: "#146ee8" }} activeDot={{ r: 5 }} />
          </LineChart>
        </ResponsiveContainer>
        <div className="pressure-legend">
          <span><i className="pressure-line auto" /> Observed input</span>
          <span><i className="pressure-line trigger" /> Relative chart guide</span>
          <span className="real-limits">Runtime contract: trigger {compact.format(run.context.autoTrigger)} · hard {compact.format(run.context.hardLimit)}</span>
        </div>
      </div>
      <div className="success-note"><CheckCircle size={17} weight="fill" /> {run.context.rebases ? `${run.context.rebases} full rebase event(s) recorded.` : "No full rebase occurred in this run."}</div>
    </section>
  );
}

function SourceEfficiency({ source }) {
  const cells = [
    { label: "Read calls", value: source.readCalls, icon: FileText, tone: "blue" },
    { label: "Unique lines", value: source.uniqueLines, icon: Code, tone: "teal" },
    { label: "Duplicate lines", value: source.duplicateLines, icon: FileCode, tone: "orange" },
    { label: "Rehydrated lines", value: source.rehydratedLines, icon: Lightning, tone: "plum" },
  ];
  return (
    <section className="source-section">
      <div className="section-heading compact accent-plum"><div><small>Source efficiency</small><h2>Repository reading</h2></div></div>
      <div className="source-grid">
        {cells.map(({ label, value, icon: Icon, tone }) => (
          <div key={label}><Icon size={22} className={tone} /><span>{label}</span><strong className={tone}>{number.format(value)}</strong></div>
        ))}
      </div>
    </section>
  );
}

function EconomicsView({ run, selectedTurn, onTurn, onTrajectory }) {
  const turn = run.turns.find((item) => item.turn === selectedTurn) || run.turns[0];
  return (
    <div className="economics-page">
      <RunSummary run={run} />
      <main className="economics-main">
        <section className="economics-workspace">
          <div className="chart-heading">
            <div><span className="eyebrow">Token economics</span><h1>Turn-by-turn usage</h1></div>
            <Legend />
          </div>
          {run.turns.length ? <TokenChart turns={run.turns} selectedTurn={selectedTurn} onSelect={onTurn} /> : <EmptyState />}
          <div className="table-title"><span>Per-turn summary</span><small>Select a row to explain the request</small></div>
          <TurnTable turns={run.turns} selectedTurn={selectedTurn} onSelect={onTurn} />
        </section>
        <div className="economics-side">
          <TurnInspector turn={turn} />
          <PressureChart run={run} selectedTurn={selectedTurn} />
          <SourceEfficiency source={run.source} />
        </div>
      </main>
      <footer className="economics-footer">
        <button className="trajectory-button" onClick={() => onTrajectory(selectedTurn)}><GitBranch size={18} /> Open lifecycle at Turn {selectedTurn}</button>
        <span>Investigate causal events behind the selected request.</span>
        <div className="source-path"><small>Trace loaded from</small><strong>{run.sourcePath}</strong></div>
        <div className="source-path"><small>Imported</small><strong>{run.importedAt}</strong></div>
      </footer>
    </div>
  );
}

function EmptyState() {
  return <div className="empty-state"><ListMagnifyingGlass size={28} /><strong>No model usage found</strong><span>Open a matching cost.json to populate token economics.</span></div>;
}

function LifecycleMap({ run }) {
  const phases = [
    ["Inactive", run.startedAt, "done"],
    ["Planning", run.events.find((event) => event.lane === "plan")?.ts, "done"],
    ["Awaiting approval", run.events.find((event) => event.lane === "permission")?.ts, "warning"],
    ["Executing", run.events.find((event) => event.lane === "tool")?.ts, "active"],
    ["Completed", run.finishedAt, "done"],
  ];
  return (
    <div className="lifecycle-map">
      <h3>Lifecycle map</h3>
      {phases.map(([label, ts, state], index) => (
        <div className={`phase-node ${state}`} key={label}>
          <i /><span>{label}</span><time>{formatTime(ts)}</time>{index < phases.length - 1 && <b />}
        </div>
      ))}
      <div className="phase-details">
        <span><i /> Tool execution <strong>00:03:12</strong></span>
        <span><i /> Verification <strong>00:01:27</strong></span>
        <span><i /> Recovery <strong>00:01:18</strong></span>
      </div>
    </div>
  );
}

function LeftRail({ run, query, setQuery, mode, setMode, bookmarks, onSelectEvent }) {
  const bookmarkedEvents = bookmarks.map((id) => run.events.find((event) => event.id === id)).filter(Boolean);
  return (
    <aside className="left-rail">
      <div className="rail-icons">
        <button className={mode === "runs" ? "active" : ""} title="Runs" onClick={() => setMode("runs")}><FileText size={20} /></button>
        <button title="Search" onClick={() => { setMode("runs"); window.setTimeout(() => document.querySelector("#run-search")?.focus(), 0); }}><MagnifyingGlass size={20} /></button>
        <button className={mode === "bookmarks" ? "active" : ""} title="Bookmarks" onClick={() => setMode("bookmarks")}><BookmarkSimple size={20} /></button>
      </div>
      <div className="rail-content">
        <div className="rail-tabs"><button className={mode === "runs" ? "active" : ""} onClick={() => setMode("runs")}>Runs</button><button className={mode === "bookmarks" ? "active" : ""} onClick={() => setMode("bookmarks")}>Bookmarks</button></div>
        {mode === "runs" ? <>
          <label className="run-search"><MagnifyingGlass size={15} /><input id="run-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search current run…" /></label>
          {run.id.toLowerCase().includes(query.toLowerCase()) && (
            <div className="run-row selected"><span><strong>{run.id.slice(0, 22)}</strong><small>runtime {run.duration} · {run.turns.length} calls</small></span><StatusDot /></div>
          )}
          <div className="task-group"><span>Tasks (1)</span><div><Target size={14} /> {run.taskId}</div></div>
          <LifecycleMap run={run} />
          <div className="rail-outcome">
            <div><span>Outcome</span><strong>{run.runtimeSuccess ? "Runtime Success" : "Runtime Failed"}</strong></div>
            <div><span>Verification</span><strong>{run.verification}</strong></div>
            <div><span>Model calls</span><strong>{run.turns.length}</strong></div>
            <div><span>Total input</span><strong>{number.format(run.turns.reduce((sum, turn) => sum + turn.input, 0))}</strong></div>
          </div>
        </> : <div className="bookmarks-panel">
          <span>Saved events ({bookmarkedEvents.length})</span>
          {bookmarkedEvents.length ? bookmarkedEvents.map((event) => (
            <button key={event.id} onClick={() => onSelectEvent(event.id)}>
              <strong>{event.title}</strong><small>{laneLabels[event.lane]} · {formatTime(event.ts)}</small>
            </button>
          )) : <div className="bookmarks-empty"><BookmarkSimple size={24} /><strong>No bookmarks yet</strong><small>Add one from the Event inspector.</small></div>}
        </div>}
      </div>
    </aside>
  );
}

function OutcomeStrip({ run }) {
  const input = run.turns.reduce((sum, turn) => sum + turn.input, 0);
  return (
    <div className="outcome-strip">
      <div><span>Runtime</span><strong className={run.runtimeSuccess ? "success" : "failure"}>{run.runtimeSuccess ? "Success" : "Failed"}</strong><CheckCircle size={28} weight="duotone" /></div>
      <div><span>Verification</span><strong className={run.verification === "Passed" ? "success" : "warning"}>{run.verification}</strong><ShieldCheck size={28} weight="duotone" /></div>
      <div><span>Model calls</span><strong>{run.turns.length} calls</strong><Heartbeat size={25} /></div>
      <div><span>Total input</span><strong>{number.format(input)}</strong><Stack size={25} /></div>
    </div>
  );
}

function FilterBar({ filters, onToggle, links, setLinks, failuresOnly, setFailuresOnly }) {
  return (
    <div className="timeline-toolbar">
      <label className="switch-label">Causal links <button className={`switch ${links ? "on" : ""}`} onClick={() => setLinks(!links)} aria-pressed={links}><i /></button></label>
      <label className="switch-label">Failures only <button className={`switch danger ${failuresOnly ? "on" : ""}`} onClick={() => setFailuresOnly(!failuresOnly)} aria-pressed={failuresOnly}><i /></button></label>
      <span className="toolbar-divider" />
      <span className="filters-label"><Funnel size={15} /> Filters</span>
      {lanes.map((lane) => {
        const Icon = laneIcons[lane];
        return <button key={lane} className={`filter-chip ${lane} ${filters[lane] ? "active" : ""}`} onClick={() => onToggle(lane)}><Icon size={14} /> {laneLabels[lane]}</button>;
      })}
      <button className="reset-filter" onClick={() => onToggle("reset")}>Reset</button>
    </div>
  );
}

function Timeline({ events, selectedId, onSelect, links, zoom }) {
  const maxElapsed = Math.max(...events.map((event) => event.elapsed || 0), 1);
  const width = Math.max(820 * zoom, 760);
  const leftOffset = 150;
  const rightInset = 70;
  const placed = [];
  lanes.forEach((lane) => {
    let previousX = leftOffset - 72;
    events
      .filter((event) => event.lane === lane)
      .sort((a, b) => (a.elapsed || 0) - (b.elapsed || 0))
      .forEach((event) => {
        const proportionalX = leftOffset + ((event.elapsed || 0) / maxElapsed) * (width - leftOffset - rightInset);
        const x = Math.min(Math.max(proportionalX, previousX + 72), width - rightInset);
        placed.push({ ...event, x });
        previousX = x;
      });
  });
  const placedById = Object.fromEntries(placed.map((event) => [event.id, event]));
  const causalLinks = placed.flatMap((event) =>
    (event.next || []).map((nextId) => {
      const target = placedById[nextId];
      if (!target) return null;
      const sourceY = 90 + lanes.indexOf(event.lane) * 96;
      const targetY = 90 + lanes.indexOf(target.lane) * 96;
      return { id: `${event.id}-${nextId}`, sourceX: event.x, targetX: target.x, sourceY, targetY };
    }).filter(Boolean),
  );
  return (
    <div className="timeline-scroll">
      <div className="timeline-canvas" style={{ width }}>
        <div className="time-ruler">
          {[0, .2, .4, .6, .8, 1].map((ratio) => (
            <span key={ratio} style={{ left: leftOffset + ratio * (width - leftOffset - rightInset) }}>{formatTime(new Date(new Date(events[0]?.ts || Date.now()).getTime() + maxElapsed * ratio * 1000))}</span>
          ))}
        </div>
        {links && <div className="causal-layer" aria-hidden="true">
          {causalLinks.map((link) => {
            const downward = link.targetY >= link.sourceY;
            return (
              <i
                key={link.id}
                className={downward ? "causal-link down" : "causal-link up"}
                style={{
                  left: link.sourceX,
                  top: Math.min(link.sourceY, link.targetY),
                  width: Math.max(link.targetX - link.sourceX, 1),
                  height: Math.max(Math.abs(link.targetY - link.sourceY), 1),
                }}
              />
            );
          })}
        </div>}
        {lanes.map((lane, laneIndex) => {
          const Icon = laneIcons[lane];
          return (
            <div className={`timeline-lane ${lane}`} key={lane} style={{ top: 48 + laneIndex * 96 }}>
              <div className="lane-label"><Icon size={20} weight="duotone" /><span>{laneLabels[lane]}</span></div>
              <div className="lane-track" />
              {placed.filter((event) => event.lane === lane).map((event) => (
                <button
                  key={event.id}
                  className={`event-node ${event.status} ${selectedId === event.id ? "selected" : ""}`}
                  style={{ left: event.x }}
                  onClick={() => onSelect(event.id)}
                  title={`${event.type} · ${formatTime(event.ts)}`}
                >
                  <span>{event.title}</span>
                  <small>{formatTime(event.ts)}</small>
                </button>
              ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function TimelineMinimap({ events, selectedId, onSelect, zoom, setZoom }) {
  const maxElapsed = Math.max(...events.map((event) => event.elapsed || 0), 1);
  return (
    <div className="minimap-row">
      <div className="minimap-title"><small>Timeline minimap</small><strong>Total duration {formatDuration(maxElapsed)}</strong></div>
      <div className="minimap-track">
        {events.map((event) => <button key={event.id} className={`${event.lane} ${event.status} ${selectedId === event.id ? "selected" : ""}`} style={{ left: `${((event.elapsed || 0) / maxElapsed) * 100}%` }} onClick={() => onSelect(event.id)} title={event.title} />)}
        <i className="viewport-window" style={{ width: `${Math.min(100 / zoom, 100)}%` }} />
      </div>
      <div className="zoom-control">
        <span>Zoom</span>
        <button onClick={() => setZoom(Math.max(1, zoom - .25))} title="Zoom out"><Minus size={15} /></button>
        <strong>{Math.round(zoom * 100)}%</strong>
        <button onClick={() => setZoom(Math.min(2, zoom + .25))} title="Zoom in"><MagnifyingGlass size={15} weight="bold" /></button>
        <button className="fit-button" onClick={() => setZoom(1)}>Fit to view</button>
      </div>
    </div>
  );
}

function formatDuration(seconds) {
  const minutes = Math.floor(seconds / 60).toString().padStart(2, "0");
  const secs = Math.floor(seconds % 60).toString().padStart(2, "0");
  return `00:${minutes}:${secs}`;
}

function EventInspector({ event, events, bookmarked, onBookmark, onSelect, onClose }) {
  if (!event) return <aside className="event-inspector empty"><Graph size={28} /><strong>Select an event</strong><span>Inspect timing, causal neighbors and redacted details.</span></aside>;
  const causedBy = events.find((item) => item.id === event.causedBy);
  const next = events.find((item) => event.next?.includes(item.id));
  const Icon = laneIcons[event.lane];
  return (
    <aside className="event-inspector">
      <div className="inspector-title"><span>Event inspector</span><button onClick={onClose} title="Clear selection"><X size={17} /></button></div>
      <div className={`event-status ${event.status}`}>{event.status === "failure" ? <WarningCircle size={15} /> : <Icon size={15} />} {event.status}</div>
      <dl className="event-facts">
        <div><dt>Event ID</dt><dd className="mono">{event.id}</dd></div>
        <div><dt>Time</dt><dd>{formatTime(event.ts)}</dd></div>
        <div><dt>Lane</dt><dd><Icon size={15} /> {laneLabels[event.lane]}</dd></div>
        <div><dt>Type</dt><dd>{event.type}</dd></div>
        <div><dt>Summary</dt><dd>{event.summary}</dd></div>
        {event.command && <div><dt>Command</dt><dd className="code-value">{event.command}</dd></div>}
        {event.details && <div><dt>Details (redacted)</dt><dd className="detail-value">{typeof event.details === "string" ? event.details : JSON.stringify(event.details, null, 2)}</dd></div>}
      </dl>
      {(next || causedBy) && <div className="relation-list">
        {next && <button onClick={() => onSelect(next.id)}><small>Next effect</small><strong>{next.title}</strong><span>{next.id}</span></button>}
        {causedBy && <button onClick={() => onSelect(causedBy.id)}><small>Caused by</small><strong>{causedBy.title}</strong><span>{causedBy.id}</span></button>}
      </div>}
      <div className="tag-row"><span>{event.lane}</span><span>{event.status}</span><span>turn {event.turn || "—"}</span></div>
      <button className={`bookmark-button ${bookmarked ? "active" : ""}`} onClick={() => onBookmark(event.id)}><BookmarkSimple size={17} weight={bookmarked ? "fill" : "regular"} /> {bookmarked ? "Bookmarked" : "Add bookmark"}</button>
    </aside>
  );
}

function LifecycleView({ run, selectedEventId, onSelectEvent, onBack }) {
  const [filters, setFilters] = useState(Object.fromEntries(lanes.map((lane) => [lane, true])));
  const [links, setLinks] = useState(true);
  const [failuresOnly, setFailuresOnly] = useState(false);
  const [zoom, setZoom] = useState(1);
  const [query, setQuery] = useState("");
  const [bookmarks, setBookmarks] = useState([]);
  const [railMode, setRailMode] = useState("runs");
  const filtered = run.events.filter((event) => filters[event.lane] && (!failuresOnly || event.status === "failure"));
  const selected = run.events.find((event) => event.id === selectedEventId);
  const toggle = (lane) => {
    if (lane === "reset") {
      setFilters(Object.fromEntries(lanes.map((item) => [item, true])));
      setFailuresOnly(false);
      return;
    }
    setFilters((current) => ({ ...current, [lane]: !current[lane] }));
  };
  const toggleBookmark = (id) => setBookmarks((items) => items.includes(id) ? items.filter((item) => item !== id) : [...items, id]);
  return (
    <div className="lifecycle-page">
      <LeftRail run={run} query={query} setQuery={setQuery} mode={railMode} setMode={setRailMode} bookmarks={bookmarks} onSelectEvent={onSelectEvent} />
      <main className="lifecycle-main">
        <div className="lifecycle-breadcrumb"><button onClick={onBack}><ArrowLeft size={16} /> Economics</button><span>{run.id}</span><span>{formatTime(run.startedAt)}</span><span>{run.duration}</span><span className="read-only"><LockSimple size={13} /> Read-only</span></div>
        <OutcomeStrip run={run} />
        <FilterBar filters={filters} onToggle={toggle} links={links} setLinks={setLinks} failuresOnly={failuresOnly} setFailuresOnly={setFailuresOnly} />
        <div className="timeline-shell">
          {filtered.length ? <Timeline events={filtered} selectedId={selectedEventId} onSelect={onSelectEvent} links={links} zoom={zoom} /> : <div className="timeline-empty"><Funnel size={24} /><strong>No events match the active filters</strong><button onClick={() => toggle("reset")}>Reset filters</button></div>}
          <TimelineMinimap events={run.events} selectedId={selectedEventId} onSelect={onSelectEvent} zoom={zoom} setZoom={setZoom} />
        </div>
      </main>
      <EventInspector event={selected} events={run.events} bookmarked={bookmarks.includes(selectedEventId)} onBookmark={toggleBookmark} onSelect={onSelectEvent} onClose={() => onSelectEvent(null)} />
      <footer className="lifecycle-status"><span>{run.events.length} events · {bookmarks.length} bookmark{bookmarks.length === 1 ? "" : "s"}</span><span>All data is local and read-only</span><span>Loaded from {run.sourcePath}</span></footer>
    </div>
  );
}

export function App() {
  const [view, setView] = useState("economics");
  const [run, setRun] = useState(sampleRun);
  const [selectedTurn, setSelectedTurn] = useState(4);
  const [selectedEventId, setSelectedEventId] = useState("evt-10");
  const [loaded, setLoaded] = useState({ trace: false, cost: false });
  const [notice, setNotice] = useState(null);
  const fileRef = useRef(null);

  const nearestEventForTurn = useMemo(() => (turn) => {
    const candidates = run.events.filter((event) => event.turn === turn);
    return candidates.find((event) => event.status === "failure") || candidates[0] || run.events[0];
  }, [run]);

  const openTrajectory = (turn) => {
    const event = nearestEventForTurn(turn);
    setSelectedEventId(event?.id || null);
    setView("lifecycle");
  };

  const importFiles = async (files) => {
    const list = [...files];
    const traceFile = list.find((file) => file.name === "trace.jsonl" || file.name.endsWith(".jsonl"));
    const costFile = list.find((file) => file.name === "cost.json" || (file.name.endsWith(".json") && file.name.toLowerCase().includes("cost")));
    if (!traceFile) {
      setNotice({ tone: "error", text: "请选择 trace.jsonl；cost.json 可选。" });
      return;
    }
    try {
      const [traceText, costText] = await Promise.all([traceFile.text(), costFile ? costFile.text() : Promise.resolve(null)]);
      const imported = buildImportedRun(traceText, costText, { trace: traceFile.webkitRelativePath || traceFile.name, cost: costFile?.name });
      setRun(imported);
      setSelectedTurn(imported.turns[0]?.turn || 1);
      setSelectedEventId(imported.events[0]?.id || null);
      setLoaded({ trace: true, cost: Boolean(costFile) });
      setNotice({ tone: "success", text: `${imported.id} 已在浏览器本地载入。` });
      window.setTimeout(() => setNotice(null), 3600);
    } catch (error) {
      setNotice({ tone: "error", text: error.message });
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  return (
    <div className={`app-shell ${view}`}>
      <input ref={fileRef} className="visually-hidden" type="file" accept=".json,.jsonl,application/json" multiple onChange={(event) => importFiles(event.target.files)} />
      <AppHeader view={view} onView={setView} run={run} onOpen={() => fileRef.current?.click()} loaded={loaded} />
      {view === "economics" ? (
        <EconomicsView run={run} selectedTurn={selectedTurn} onTurn={setSelectedTurn} onTrajectory={openTrajectory} />
      ) : view === "lifecycle" ? (
        <LifecycleView run={run} selectedEventId={selectedEventId} onSelectEvent={setSelectedEventId} onBack={() => setView("economics")} />
      ) : (
        <TraceView
          run={run}
          selectedEventId={selectedEventId}
          onSelectEvent={setSelectedEventId}
          onLifecycle={(eventId) => { setSelectedEventId(eventId); setView("lifecycle"); }}
        />
      )}
      {notice && <div className={`toast ${notice.tone}`}>{notice.tone === "success" ? <CheckCircle size={18} /> : <WarningCircle size={18} />}<span>{notice.text}</span><button onClick={() => setNotice(null)}><X size={15} /></button></div>}
    </div>
  );
}
