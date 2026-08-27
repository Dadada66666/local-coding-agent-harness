import { useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  ArrowSquareOut,
  BracketsCurly,
  CaretDown,
  CaretRight,
  Check,
  CheckCircle,
  Clock,
  Copy,
  FileText,
  Funnel,
  GitBranch,
  MagnifyingGlass,
  Pause,
  Play,
  ShieldCheck,
  SkipBack,
  SkipForward,
  Sparkle,
  Stack,
  TerminalWindow,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import { laneLabels } from "./data";

const laneIcons = {
  model: Sparkle,
  plan: FileText,
  permission: ShieldCheck,
  tool: TerminalWindow,
  verification: CheckCircle,
  context: Stack,
};

const normalizedKeys = new Set([
  "id",
  "event_id",
  "ts",
  "ts_iso",
  "timestamp",
  "elapsed",
  "elapsed_ms",
  "lane",
  "type",
  "event_type",
  "title",
  "summary",
  "message",
  "reason",
  "error",
  "status",
  "success",
  "turn",
  "turn_id",
  "command",
  "details",
  "content",
  "output",
  "causedBy",
  "next",
]);

function formatClock(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleTimeString([], { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "—";
  const safe = Math.max(Number(seconds || 0), 0);
  if (safe < 1) return `${Math.round(safe * 1000)}ms`;
  if (safe < 60) return `${safe.toFixed(safe < 10 ? 2 : 1)}s`;
  return `${Math.floor(safe / 60)}m ${Math.round(safe % 60)}s`;
}

function eventDuration(event, events) {
  const explicit = Number(event.duration_ms ?? event.duration ?? 0);
  if (explicit > 0) return event.duration_ms ? explicit / 1000 : explicit;
  return null;
}

function eventAuthority(event) {
  if (event.type === "test_result") return "Authoritative verification fact";
  if (event.lane === "permission") return "Runtime permission decision";
  if (event.lane === "plan") return "Plan lifecycle state";
  if (event.lane === "tool") return "Tool execution evidence";
  if (event.lane === "context") return "Runtime context event";
  return "Model trajectory event";
}

function eventMatchesFilter(event, filter) {
  if (filter === "failures") return event.status === "failure";
  if (filter === "decisions") return event.lane === "plan" || event.lane === "permission";
  if (filter === "tools") return event.lane === "tool";
  if (filter === "verification") return event.lane === "verification";
  return true;
}

function traceStatus(events) {
  if (events.some((event) => event.status === "failure")) return "failure";
  if (events.some((event) => event.status === "warning")) return "warning";
  if (events.some((event) => event.status === "success")) return "success";
  return "neutral";
}

function TraceOutline({ run, groups, selectedId, onSelect }) {
  const failed = run.events.filter((event) => event.status === "failure").length;
  const recovery = run.events.filter((event) => `${event.type} ${event.title}`.toLowerCase().includes("recovery")).length;
  return (
    <aside className="trace-outline">
      <div className="trace-outline-heading">
        <span>Trace outline</span>
        <small>{run.events.length} events</small>
      </div>
      <div className="trace-health-grid">
        <div><span>Failures</span><strong className={failed ? "failure" : "success"}>{failed}</strong></div>
        <div><span>Recoveries</span><strong className={recovery ? "warning" : "success"}>{recovery}</strong></div>
      </div>
      <div className="trace-outline-label">Turn index</div>
      <div className="trace-turn-index">
        {groups.map((group) => {
          const selected = group.events.some((event) => event.id === selectedId);
          const state = traceStatus(group.events);
          return (
            <button key={group.turn} className={selected ? "selected" : ""} onClick={() => onSelect(group.events[0]?.id)}>
              <span className={`turn-index-state ${state}`} />
              <span><strong>{group.turn ? `Turn ${group.turn}` : "Bootstrap"}</strong><small>{group.phase} · {group.events.length} events</small></span>
              <CaretRight size={13} />
            </button>
          );
        })}
      </div>
      <div className="trace-contract-note">
        <GitBranch size={18} />
        <div><strong>Forensic view</strong><span>Event order, gates, failure evidence and recovery relationships. Token economics and phase timing stay in their dedicated views.</span></div>
      </div>
    </aside>
  );
}

function TraceReplay({ events, cursor, setCursor, playing, setPlaying }) {
  const current = events[cursor];
  const go = (next) => {
    setPlaying(false);
    setCursor(Math.min(Math.max(next, 0), Math.max(events.length - 1, 0)));
  };
  return (
    <div className="trace-replay">
      <div className="trace-filter-label"><span>Replay position</span><strong>{events.length ? cursor + 1 : 0} / {events.length}</strong></div>
      <input
        aria-label="Replay position"
        type="range"
        min="0"
        max={Math.max(events.length - 1, 0)}
        value={cursor}
        onChange={(event) => go(Number(event.target.value))}
      />
      <div className="trace-replay-actions">
        <button onClick={() => go(0)} title="First event"><SkipBack size={16} /></button>
        <button className="trace-play" disabled={!events.length} onClick={() => {
          if (cursor >= events.length - 1) setCursor(0);
          setPlaying((value) => !value);
        }} title={playing ? "Pause replay" : "Play trace replay"}>{playing ? <Pause size={17} weight="fill" /> : <Play size={17} weight="fill" />}</button>
        <button onClick={() => go(cursor + 1)} title="Next event"><SkipForward size={16} /></button>
      </div>
      <div className="trace-now"><span>Now</span><strong>{current?.title || "No event"}</strong><small>{formatClock(current?.ts)}</small></div>
    </div>
  );
}

function TraceEventRow({ event, events, selected, future, onSelect }) {
  const Icon = laneIcons[event.lane] || Stack;
  const duration = eventDuration(event, events);
  const ResultIcon = event.status === "failure" || event.status === "warning" ? WarningCircle : event.status === "success" ? CheckCircle : Clock;
  return (
    <button className={`trace-event-row ${event.lane} ${event.status} ${selected ? "selected" : ""} ${future ? "future" : ""}`} onClick={() => onSelect(event.id)}>
      <span className="trace-event-branch" />
      <span className="trace-event-icon"><Icon size={15} weight="duotone" /></span>
      <span className="trace-event-copy">
        <span><strong>{event.title}</strong><small>{event.type}</small></span>
        <em>{event.summary}</em>
      </span>
      <span className="trace-event-time"><strong>{formatDuration(duration)}</strong><small>+{formatDuration(event.elapsed)}</small></span>
      <span className={`trace-event-result ${event.status}`}><ResultIcon size={16} weight={event.status === "neutral" || event.status === "active" ? "regular" : "fill"} /></span>
    </button>
  );
}

function TraceTree({ groups, events, expanded, setExpanded, selectedId, onSelect, cursor }) {
  const cursorEvent = events[cursor];
  const cursorElapsed = Number(cursorEvent?.elapsed || 0);
  const toggle = (turn) => setExpanded((current) => {
    const next = new Set(current);
    if (next.has(turn)) next.delete(turn);
    else next.add(turn);
    return next;
  });
  return (
    <div className="trace-tree">
      {groups.map((group) => {
        const isOpen = expanded.has(group.turn);
        const state = traceStatus(group.events);
        const duration = Math.max(group.events.at(-1)?.elapsed - group.events[0]?.elapsed, 0);
        return (
          <section className={`trace-turn-group ${state}`} key={group.turn}>
            <button className="trace-turn-heading" onClick={() => toggle(group.turn)} aria-expanded={isOpen}>
              {isOpen ? <CaretDown size={14} /> : <CaretRight size={14} />}
              <span className="trace-turn-number">{group.turn || "B"}</span>
              <span className="trace-turn-title"><strong>{group.turn ? `Model turn ${group.turn}` : "Runtime bootstrap"}</strong><small>{group.phase} · {group.events.length} linked events</small></span>
              <span className="trace-turn-duration"><Clock size={13} /> {formatDuration(duration)}</span>
              <span className={`trace-turn-status ${state}`}>{state}</span>
            </button>
            {isOpen && <div className="trace-turn-events">
              {group.events.map((event) => <TraceEventRow key={event.id} event={event} events={events} selected={selectedId === event.id} future={Number(event.elapsed || 0) > cursorElapsed} onSelect={onSelect} />)}
            </div>}
          </section>
        );
      })}
    </div>
  );
}

function TraceInspector({ event, events, onSelect, onClose, onLifecycle }) {
  const [copied, setCopied] = useState(false);
  if (!event) return <aside className="trace-inspector empty"><BracketsCurly size={26} /><strong>Select a trace event</strong><span>Inspect its gate, evidence, relations and raw fields.</span></aside>;
  const Icon = laneIcons[event.lane] || Stack;
  const parent = events.find((item) => item.id === event.causedBy);
  const effects = events.filter((item) => event.next?.includes(item.id) || item.causedBy === event.id);
  const raw = Object.fromEntries(Object.entries(event).filter(([key]) => !normalizedKeys.has(key)));
  const operational = [
    ["Purpose", event.purpose],
    ["Result scope", event.result_scope],
    ["Decision", event.decision],
    ["Decision reason", event.decision_reason],
    ["Error kind", event.error_kind],
    ["Exit code", event.exit_code],
    ["Timed out", event.timed_out === undefined ? null : event.timed_out ? "Yes" : "No"],
  ].filter(([, value]) => value !== undefined && value !== null && value !== "");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(event, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1400);
    } catch {
      setCopied(false);
    }
  };
  return (
    <aside className="trace-inspector">
      <div className="trace-inspector-title"><span>Event inspector</span><button onClick={onClose} title="Close inspector"><X size={17} /></button></div>
      <div className="trace-inspector-hero">
        <span className={`trace-inspector-icon ${event.lane}`}><Icon size={19} weight="duotone" /></span>
        <div><small>{laneLabels[event.lane] || event.lane}</small><h2>{event.title}</h2><span className={`trace-status-pill ${event.status}`}>{event.status}</span></div>
      </div>
      <div className="trace-fact-grid">
        <div><span>Turn</span><strong>{event.turn || "Bootstrap"}</strong></div>
        <div><span>Elapsed</span><strong>{formatDuration(event.elapsed)}</strong></div>
        <div><span>Duration</span><strong>{formatDuration(eventDuration(event, events))}</strong></div>
        <div><span>Event ID</span><strong className="mono">{event.id}</strong></div>
      </div>
      <section className="trace-inspector-section">
        <span className="trace-inspector-label">Runtime meaning</span>
        <strong>{eventAuthority(event)}</strong>
        <p>{event.summary}</p>
      </section>
      {event.command && <section className="trace-inspector-section"><span className="trace-inspector-label">Command</span><pre>{event.command}</pre></section>}
      {operational.length > 0 && <section className="trace-inspector-section">
        <span className="trace-inspector-label">Execution contract</span>
        <dl className="trace-operation-facts">{operational.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{String(value)}</dd></div>)}</dl>
      </section>}
      {event.details && <section className="trace-inspector-section"><span className="trace-inspector-label">Evidence excerpt</span><pre>{typeof event.details === "string" ? event.details : JSON.stringify(event.details, null, 2)}</pre></section>}
      {(parent || effects.length) && <section className="trace-inspector-section">
        <span className="trace-inspector-label">Causal neighborhood</span>
        <div className="trace-relations">
          {parent && <button onClick={() => onSelect(parent.id)}><span>Caused by</span><strong>{parent.title}</strong><CaretRight size={13} /></button>}
          {effects.map((effect) => <button key={effect.id} onClick={() => onSelect(effect.id)}><span>Next effect</span><strong>{effect.title}</strong><CaretRight size={13} /></button>)}
        </div>
      </section>}
      <details className="trace-raw-fields">
        <summary><BracketsCurly size={15} /> Raw fields <span>{Object.keys(raw).length}</span></summary>
        <pre>{Object.keys(raw).length ? JSON.stringify(raw, null, 2) : "No additional fields on this event."}</pre>
      </details>
      <div className="trace-inspector-actions">
        <button onClick={copy}>{copied ? <Check size={15} /> : <Copy size={15} />} {copied ? "Copied" : "Copy event JSON"}</button>
        <button onClick={() => onLifecycle(event.id)}><ArrowSquareOut size={15} /> Open in Lifecycle</button>
      </div>
    </aside>
  );
}

export function TraceView({ run, selectedEventId, onSelectEvent, onLifecycle }) {
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(Math.max(run.events.length - 1, 0));
  const [playing, setPlaying] = useState(false);
  const [expanded, setExpanded] = useState(() => new Set(run.events.map((event) => event.turn || 0)));

  useEffect(() => {
    setCursor(Math.max(run.events.length - 1, 0));
    setExpanded(new Set(run.events.map((event) => event.turn || 0)));
  }, [run]);

  useEffect(() => {
    if (!playing || !run.events.length) return undefined;
    if (cursor >= run.events.length - 1) {
      setPlaying(false);
      return undefined;
    }
    const timer = window.setTimeout(() => {
      const next = cursor + 1;
      setCursor(next);
      onSelectEvent(run.events[next]?.id);
    }, 850);
    return () => window.clearTimeout(timer);
  }, [playing, cursor, run.events, onSelectEvent]);

  const groups = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();
    const visible = run.events.filter((event) => {
      const haystack = `${event.id} ${event.type} ${event.title} ${event.summary} ${event.command || ""}`.toLowerCase();
      return eventMatchesFilter(event, filter) && (!normalizedQuery || haystack.includes(normalizedQuery));
    });
    const byTurn = new Map();
    visible.forEach((event) => {
      const turn = event.turn || 0;
      if (!byTurn.has(turn)) byTurn.set(turn, []);
      byTurn.get(turn).push(event);
    });
    return [...byTurn.entries()].sort(([a], [b]) => a - b).map(([turn, events]) => ({
      turn,
      events,
      phase: run.turns.find((item) => item.turn === turn)?.phase || (turn === 0 ? "Session setup" : "Runtime"),
    }));
  }, [filter, query, run]);

  const selected = selectedEventId ? run.events.find((event) => event.id === selectedEventId) : null;
  const failed = run.events.find((event) => event.status === "failure");
  const recovery = run.events.find((event) => `${event.type} ${event.title}`.toLowerCase().includes("recovery"));
  const verified = [...run.events].reverse().find((event) => event.lane === "verification" && event.status === "success");
  const selectEvent = (id) => {
    onSelectEvent(id);
    const index = run.events.findIndex((event) => event.id === id);
    if (index >= 0) setCursor(index);
  };

  return (
    <div className="trace-page">
      <TraceOutline run={run} groups={groups} selectedId={selectedEventId} onSelect={selectEvent} />
      <main className="trace-main">
        <header className="trace-run-header">
          <div>
            <span className="trace-eyebrow">Agent trace · read-only</span>
            <h1>{run.id}</h1>
            <p>{run.task}</p>
          </div>
          <div className="trace-run-meta">
            <span><Clock size={14} /> {run.duration}</span>
            <span><Sparkle size={14} /> {run.turns.length} model calls</span>
            <span className={run.verification === "Passed" ? "success" : "warning"}><ShieldCheck size={14} /> {run.verification}</span>
          </div>
        </header>
        <section className="trace-critical-path">
          <span className="trace-critical-label">Critical path</span>
          <div className="trace-critical-step"><span className="neutral">1</span><div><small>Failure evidence</small><strong>{failed?.title || "No failure recorded"}</strong></div></div>
          <ArrowRight size={16} />
          <div className="trace-critical-step"><span className="warning">2</span><div><small>Runtime response</small><strong>{recovery?.title || "No recovery needed"}</strong></div></div>
          <ArrowRight size={16} />
          <div className="trace-critical-step"><span className="success">3</span><div><small>Terminal evidence</small><strong>{verified?.title || run.status}</strong></div></div>
        </section>
        <section className="trace-toolbar">
          <label className="trace-search"><MagnifyingGlass size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search IDs, tools, commands, evidence…" /></label>
          <label className="trace-select"><Funnel size={14} /><select value={filter} onChange={(event) => setFilter(event.target.value)}><option value="all">All events</option><option value="failures">Failures only</option><option value="decisions">Plan & permission</option><option value="tools">Tools only</option><option value="verification">Verification</option></select></label>
          <button className="trace-expand" onClick={() => setExpanded(new Set(groups.map((group) => group.turn)))}>Expand all</button>
          <button className="trace-expand" onClick={() => setExpanded(new Set())}>Collapse all</button>
        </section>
        <TraceReplay events={run.events} cursor={cursor} setCursor={(value) => {
          setCursor(value);
          onSelectEvent(run.events[value]?.id);
        }} playing={playing} setPlaying={setPlaying} />
        <div className="trace-column-head"><span>Turn / event chain</span><span>Duration / elapsed</span><span>Status</span></div>
        <div className="trace-tree-scroll">
          {groups.length ? <TraceTree groups={groups} events={run.events} expanded={expanded} setExpanded={setExpanded} selectedId={selectedEventId} onSelect={selectEvent} cursor={cursor} /> : <div className="trace-no-results"><MagnifyingGlass size={24} /><strong>No matching events</strong><button onClick={() => { setFilter("all"); setQuery(""); }}>Reset search</button></div>}
        </div>
      </main>
      <TraceInspector event={selected} events={run.events} onSelect={selectEvent} onClose={() => onSelectEvent(null)} onLifecycle={onLifecycle} />
      <footer className="trace-statusbar"><span><CheckCircle size={14} weight="fill" /> Ordered trace loaded · {run.events.length} events</span><span>Unknown fields remain available under Raw fields</span><span>Local and read-only</span></footer>
    </div>
  );
}
