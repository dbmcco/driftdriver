# ABOUTME: Maps claude-session-driver JSONL events to Lessons MCP record_event calls
# ABOUTME: Bridges worker lifecycle events to knowledge capture

import json
from dataclasses import dataclass
from pathlib import Path

_EVENT_TYPE_MAP = {
    "pre_tool_use": "tool_use",
    "session_start": "observation",
    "session_end": "observation",
    "stop": "observation",
    "user_prompt_submit": "observation",
}


@dataclass
class MappedEvent:
    session_id: str
    event_type: str  # decision, error, observation, tool_use
    project: str
    payload: dict


def map_event(raw_event: dict, session_id: str, project: str) -> MappedEvent | None:
    """Map a session-driver event dict to a MappedEvent, or None if unknown."""
    event_name = raw_event.get("event")
    event_type = _EVENT_TYPE_MAP.get(event_name)
    if event_type is None:
        return None

    if event_name == "pre_tool_use":
        payload = {
            "event": event_name,
            "tool": raw_event.get("tool"),
            "tool_input": raw_event.get("tool_input", {}),
        }
    else:
        payload = {k: v for k, v in raw_event.items() if k != "ts"}

    return MappedEvent(
        session_id=session_id,
        event_type=event_type,
        project=project,
        payload=payload,
    )


def parse_events_file(events_file: Path) -> list[dict]:
    """Read a JSONL file and return a list of parsed event dicts, skipping malformed lines."""
    events = []
    with events_file.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events


def bridge_events(events_file: Path, session_id: str, project: str) -> list[MappedEvent]:
    """Parse events file and map all events, filtering out unmapped ones."""
    raw_events = parse_events_file(events_file)
    mapped = [map_event(e, session_id=session_id, project=project) for e in raw_events]
    return [m for m in mapped if m is not None]


def format_mcp_call(event: MappedEvent) -> dict:
    """Format a MappedEvent into the JSON shape Lessons MCP record_event expects."""
    return {
        "session_id": event.session_id,
        "event_type": event.event_type,
        "project": event.project,
        "payload": event.payload,
    }
