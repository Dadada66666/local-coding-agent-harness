from __future__ import annotations

import json
from pathlib import Path


def render_replay(path: Path) -> str:
    events = _read_trace_events(path)
    if not events:
        return "No trace events found."

    turns: dict[int, list[dict]] = {}
    for event in events:
        turn_id = event.get("turn_id")
        if turn_id is not None:
            turns.setdefault(int(turn_id), []).append(event)

    if not turns:
        return "No turn-scoped trace events found."

    lines: list[str] = []
    for turn_id in sorted(turns):
        turn_events = turns[turn_id]
        lines.append(f"Turn {turn_id}")
        model_event = _first_event(turn_events, "model_call_end")
        if model_event:
            lines.append(
                "  model_call: "
                f"input_tokens={model_event.get('input_tokens')} "
                f"output_tokens={model_event.get('output_tokens')} "
                f"tools={model_event.get('tool_names', [])}"
            )

        results = {
            event.get("tool_call_id"): event
            for event in turn_events
            if event.get("type") == "tool_result" and event.get("tool_call_id")
        }
        for event in turn_events:
            if event.get("type") != "tool_use":
                continue
            result = results.get(event.get("tool_call_id"))
            lines.append(_render_tool_replay(event, result))

    return "\n".join(lines)


def _read_trace_events(path: Path) -> list[dict]:
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _first_event(events: list[dict], event_type: str) -> dict | None:
    for event in events:
        if event.get("type") == event_type:
            return event
    return None


def _render_tool_replay(event: dict, result: dict | None) -> str:
    tool = event.get("tool")
    normalized = event.get("normalized_args") or event.get("args") or {}
    status = "pending" if result is None else "ok" if result.get("ok") else "failed"
    suffix = _tool_semantics(tool, result)
    return f"  tool: {tool} args={event.get('args', {})} normalized={normalized} -> {status}{suffix}"


def _tool_semantics(tool: str, result: dict | None) -> str:
    metadata = (result or {}).get("metadata") or {}
    if tool == "list_dir":
        return " (searches file names)"
    if tool == "grep":
        match_count = metadata.get("match_count")
        if match_count == 0:
            return " (no matches, searched file contents only)"
        return " (searches file contents)"
    return ""

