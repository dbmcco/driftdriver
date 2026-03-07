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
    match_scope_relevance,
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


# --- Default confidence threshold tests ---


def test_filter_default_confidence_is_0_6():
    """Default min_confidence should be 0.6, rejecting entries at 0.55."""
    entries = [
        {"content": "borderline entry", "confidence": 0.55},
        {"content": "above threshold", "confidence": 0.65},
    ]
    result = filter_federated_knowledge(entries)
    assert len(result) == 1
    assert result[0]["content"] == "above threshold"


# --- Scope relevance matching tests ---


def test_match_scope_relevance_keyword_in_content():
    """Entry content containing a scope keyword should match."""
    entry = {"content": "Use pytest fixtures for database testing", "category": "testing"}
    assert match_scope_relevance(entry, ["pytest"]) is True


def test_match_scope_relevance_keyword_in_category():
    """Entry category matching a scope keyword should match."""
    entry = {"content": "Always validate inputs", "category": "python"}
    assert match_scope_relevance(entry, ["python"]) is True


def test_match_scope_relevance_keyword_in_fact_type():
    """Entry fact_type matching a scope keyword should match."""
    entry = {"content": "Watch out for race conditions", "fact_type": "security"}
    assert match_scope_relevance(entry, ["security"]) is True


def test_match_scope_relevance_no_match():
    """Entry with no overlapping keywords should not match."""
    entry = {"content": "Use React hooks for state management", "category": "frontend"}
    assert match_scope_relevance(entry, ["python", "pytest", "fastapi"]) is False


def test_match_scope_relevance_case_insensitive():
    """Matching should be case-insensitive."""
    entry = {"content": "Always use TypeScript strict mode", "category": "Frontend"}
    assert match_scope_relevance(entry, ["typescript"]) is True


def test_match_scope_relevance_empty_tags_accepts_all():
    """Empty scope tags means no filtering — accept everything."""
    entry = {"content": "Anything goes", "category": "misc"}
    assert match_scope_relevance(entry, []) is True


def test_match_scope_relevance_checks_tags_field():
    """Entry with explicit tags field should be checked."""
    entry = {"content": "General advice", "tags": ["python", "testing"]}
    assert match_scope_relevance(entry, ["python"]) is True


def test_match_scope_relevance_partial_word_no_match():
    """'type' should not match 'typescript' — require word boundaries or exact tag match."""
    entry = {"content": "Check the type of variable", "category": "general"}
    assert match_scope_relevance(entry, ["typescript"]) is False


def test_filter_with_scope_tags():
    """filter_federated_knowledge with scope_tags filters irrelevant entries."""
    entries = [
        {"content": "Use pytest fixtures for testing", "confidence": 0.8, "category": "python"},
        {"content": "Use React hooks for components", "confidence": 0.8, "category": "frontend"},
        {"content": "Always validate API inputs", "confidence": 0.8, "category": "security"},
    ]
    result = filter_federated_knowledge(entries, scope_tags=["python", "pytest"])
    contents = {e["content"] for e in result}
    assert "Use pytest fixtures for testing" in contents
    assert "Use React hooks for components" not in contents


def test_filter_scope_tags_none_skips_scope_check():
    """When scope_tags is None, no scope filtering happens."""
    entries = [
        {"content": "Anything about javascript", "confidence": 0.8, "category": "js"},
        {"content": "Anything about python", "confidence": 0.8, "category": "py"},
    ]
    result = filter_federated_knowledge(entries, scope_tags=None)
    assert len(result) == 2


def test_filter_combined_gates_with_scope():
    """All gates (confidence, staleness, dedup, cap, scope) work together."""
    recent = (datetime.now() - timedelta(days=5)).isoformat()
    entries = [
        # Passes all gates
        {"content": "Use pytest for python testing", "confidence": 0.8, "category": "python", "created_at": recent},
        # Fails confidence gate
        {"content": "Low confidence python tip", "confidence": 0.3, "category": "python", "created_at": recent},
        # Fails scope gate
        {"content": "Use React hooks always", "confidence": 0.8, "category": "frontend", "created_at": recent},
    ]
    result = filter_federated_knowledge(entries, scope_tags=["python", "pytest"])
    assert len(result) == 1
    assert result[0]["content"] == "Use pytest for python testing"
