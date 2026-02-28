# ABOUTME: Tests for event_bridge.py - mapping session-driver JSONL events to Lessons MCP calls
# ABOUTME: Covers map_event, parse_events_file, bridge_events, and format_mcp_call

import json
import tempfile
from pathlib import Path

from driftdriver.event_bridge import (
    MappedEvent,
    bridge_events,
    format_mcp_call,
    map_event,
    parse_events_file,
)


def test_map_pre_tool_use_to_tool_use():
    raw = {"ts": "2026-01-15T10:30:00Z", "event": "pre_tool_use", "tool": "Bash", "tool_input": {"command": "ls"}}
    result = map_event(raw, session_id="sess-1", project="myproject")
    assert result is not None
    assert result.event_type == "tool_use"
    assert result.session_id == "sess-1"
    assert result.project == "myproject"
    assert result.payload["tool"] == "Bash"
    assert result.payload["tool_input"] == {"command": "ls"}


def test_map_session_start_to_observation():
    raw = {"ts": "2026-01-15T10:30:00Z", "event": "session_start", "cwd": "/path/to/project"}
    result = map_event(raw, session_id="sess-2", project="myproject")
    assert result is not None
    assert result.event_type == "observation"
    assert result.session_id == "sess-2"
    assert result.project == "myproject"
    assert result.payload["event"] == "session_start"


def test_map_unknown_event_returns_none():
    raw = {"ts": "2026-01-15T10:30:00Z", "event": "some_unknown_event"}
    result = map_event(raw, session_id="sess-3", project="myproject")
    assert result is None


def test_parse_events_file_handles_malformed_lines():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"event": "stop", "ts": "2026-01-15T10:30:00Z"}\n')
        f.write("not valid json {\n")
        f.write('{"event": "session_start", "ts": "2026-01-15T10:31:00Z", "cwd": "/x"}\n')
        tmp_path = Path(f.name)

    events = parse_events_file(tmp_path)
    assert len(events) == 2
    assert events[0]["event"] == "stop"
    assert events[1]["event"] == "session_start"


def test_bridge_events_filters_none():
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write('{"event": "pre_tool_use", "ts": "2026-01-15T10:30:00Z", "tool": "Read", "tool_input": {}}\n')
        f.write('{"event": "unknown_garbage", "ts": "2026-01-15T10:30:00Z"}\n')
        f.write('{"event": "stop", "ts": "2026-01-15T10:30:00Z"}\n')
        tmp_path = Path(f.name)

    results = bridge_events(tmp_path, session_id="sess-4", project="proj")
    assert len(results) == 2
    assert all(isinstance(r, MappedEvent) for r in results)
    event_types = {r.event_type for r in results}
    assert "tool_use" in event_types
    assert "observation" in event_types


def test_format_mcp_call_shape():
    event = MappedEvent(
        session_id="sess-5",
        event_type="tool_use",
        project="proj",
        payload={"tool": "Bash", "tool_input": {"command": "ls"}},
    )
    result = format_mcp_call(event)
    assert result["session_id"] == "sess-5"
    assert result["event_type"] == "tool_use"
    assert result["project"] == "proj"
    assert result["payload"] == {"tool": "Bash", "tool_input": {"command": "ls"}}
    assert set(result.keys()) == {"session_id", "event_type", "project", "payload"}
