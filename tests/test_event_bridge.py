# ABOUTME: Tests for event_bridge.py - mapping session-driver JSONL events to Lessons MCP calls
# ABOUTME: Covers map_event, parse_events_file, bridge_events, format_mcp_call, and quality gates

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from driftdriver.event_bridge import (
    MappedEvent,
    bridge_events,
    filter_federated_knowledge,
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


# --- Quality gate tests for filter_federated_knowledge ---


def test_filter_removes_low_confidence_float():
    entries = [
        {"content": "good entry", "confidence": 0.8},
        {"content": "bad entry", "confidence": 0.2},
    ]
    result = filter_federated_knowledge(entries)
    assert len(result) == 1
    assert result[0]["content"] == "good entry"


def test_filter_handles_string_confidence():
    entries = [
        {"content": "high conf", "confidence": "high"},
        {"content": "medium conf", "confidence": "medium"},
        {"content": "low conf", "confidence": "low"},
    ]
    result = filter_federated_knowledge(entries, min_confidence=0.5)
    assert len(result) == 2
    contents = {e["content"] for e in result}
    assert "high conf" in contents
    assert "medium conf" in contents
    assert "low conf" not in contents


def test_filter_deduplicates():
    entries = [
        {"content": "same thing", "confidence": 0.8},
        {"content": "same thing", "confidence": 0.9},
    ]
    result = filter_federated_knowledge(entries)
    assert len(result) == 1


def test_filter_caps_per_peer():
    entries = [
        {"content": f"entry {i}", "confidence": 0.8, "_peer": "repo-a"}
        for i in range(60)
    ]
    result = filter_federated_knowledge(entries, max_per_peer=50)
    assert len(result) == 50


def test_filter_caps_per_peer_multiple_peers():
    entries_a = [
        {"content": f"a-{i}", "confidence": 0.8, "_peer": "repo-a"}
        for i in range(30)
    ]
    entries_b = [
        {"content": f"b-{i}", "confidence": 0.8, "_peer": "repo-b"}
        for i in range(30)
    ]
    result = filter_federated_knowledge(entries_a + entries_b, max_per_peer=20)
    assert len(result) == 40
    a_count = sum(1 for e in result if e["_peer"] == "repo-a")
    b_count = sum(1 for e in result if e["_peer"] == "repo-b")
    assert a_count == 20
    assert b_count == 20


def test_filter_removes_stale_entries():
    old_date = (datetime.now() - timedelta(days=120)).isoformat()
    recent_date = (datetime.now() - timedelta(days=10)).isoformat()
    entries = [
        {"content": "old fact", "confidence": 0.8, "created_at": old_date},
        {"content": "recent fact", "confidence": 0.8, "created_at": recent_date},
    ]
    result = filter_federated_knowledge(entries, max_age_days=90)
    assert len(result) == 1
    assert result[0]["content"] == "recent fact"


def test_filter_staleness_uses_last_confirmed_over_created_at():
    old_created = (datetime.now() - timedelta(days=120)).isoformat()
    recent_confirmed = (datetime.now() - timedelta(days=5)).isoformat()
    entries = [
        {
            "content": "refreshed fact",
            "confidence": 0.8,
            "created_at": old_created,
            "last_confirmed": recent_confirmed,
        },
    ]
    result = filter_federated_knowledge(entries, max_age_days=90)
    assert len(result) == 1


def test_filter_staleness_from_provenance():
    old_date = (datetime.now() - timedelta(days=120)).isoformat()
    entries = [
        {
            "content": "provenance-dated fact",
            "confidence": 0.8,
            "provenance": f"lessons-db:{old_date}",
        },
    ]
    result = filter_federated_knowledge(entries, max_age_days=90)
    assert len(result) == 0


def test_filter_keeps_entries_without_date():
    entries = [
        {"content": "no date fact", "confidence": 0.8},
    ]
    result = filter_federated_knowledge(entries, max_age_days=90)
    assert len(result) == 1


def test_filter_missing_confidence_treated_as_zero():
    entries = [
        {"content": "no confidence field"},
    ]
    result = filter_federated_knowledge(entries, min_confidence=0.5)
    assert len(result) == 0


def test_filter_unknown_string_confidence_treated_as_zero():
    entries = [
        {"content": "unknown label", "confidence": "uncertain"},
    ]
    result = filter_federated_knowledge(entries, min_confidence=0.5)
    assert len(result) == 0


def test_filter_empty_input():
    result = filter_federated_knowledge([])
    assert result == []
