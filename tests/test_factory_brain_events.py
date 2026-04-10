# ABOUTME: Tests for factory brain events module — schema, writer, reader, aggregator.
# ABOUTME: Covers emit, read, aggregate, tier routing, and edge cases (empty/missing files).
from __future__ import annotations

import json
import time
from pathlib import Path

from driftdriver.factory_brain.events import (
    EVENTS_REL_PATH,
    TIER_ROUTING,
    Event,
    aggregate_events,
    emit_event,
    events_file_for_repo,
    read_events,
)


def test_emit_event_writes_jsonl(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    ev = emit_event(events_file, kind="loop.started", repo="/tmp/repo-a", payload={"pid": 42})

    assert ev.kind == "loop.started"
    assert ev.repo == "/tmp/repo-a"
    assert ev.payload == {"pid": 42}
    assert ev.ts > 0

    lines = events_file.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["kind"] == "loop.started"
    assert record["repo"] == "/tmp/repo-a"
    assert record["payload"] == {"pid": 42}
    assert "ts" in record

    # Append a second event
    emit_event(events_file, kind="agent.spawned", repo="/tmp/repo-a", payload={"task": "t-1"})
    lines = events_file.read_text().strip().splitlines()
    assert len(lines) == 2


def test_read_events_returns_sorted(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"

    # Write events with slightly different timestamps (natural ordering via sequential emit)
    emit_event(events_file, kind="loop.started", repo="/tmp/r", payload={})
    time.sleep(0.01)
    emit_event(events_file, kind="agent.spawned", repo="/tmp/r", payload={})
    time.sleep(0.01)
    emit_event(events_file, kind="loop.exited", repo="/tmp/r", payload={})

    events = read_events(events_file)
    assert len(events) == 3
    assert events[0].kind == "loop.started"
    assert events[2].kind == "loop.exited"
    # Verify sorted ascending by timestamp
    assert events[0].ts <= events[1].ts <= events[2].ts


def test_read_events_empty_file(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.write_text("")
    events = read_events(events_file)
    assert events == []


def test_read_events_missing_file(tmp_path: Path) -> None:
    events_file = tmp_path / "does_not_exist.jsonl"
    events = read_events(events_file)
    assert events == []


def test_read_events_since_filter(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    ev1 = emit_event(events_file, kind="loop.started", repo="/tmp/r", payload={})
    time.sleep(0.02)
    cutoff = time.time()
    time.sleep(0.02)
    emit_event(events_file, kind="agent.spawned", repo="/tmp/r", payload={})

    events = read_events(events_file, since=cutoff)
    assert len(events) == 1
    assert events[0].kind == "agent.spawned"


def test_read_events_limit(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    for i in range(10):
        emit_event(events_file, kind=f"ev.{i}", repo="/tmp/r", payload={})

    events = read_events(events_file, limit=3)
    assert len(events) == 3


def test_read_events_skips_records_missing_required_fields(tmp_path: Path) -> None:
    events_file = tmp_path / "events.jsonl"
    events_file.write_text(
        "\n".join(
            [
                json.dumps({"kind": "loop.started", "ts": 1.0, "payload": {"note": "missing repo"}}),
                json.dumps({"kind": "agent.spawned", "repo": "/tmp/r", "ts": 2.0, "payload": {"ok": True}}),
            ]
        ),
        encoding="utf-8",
    )

    events = read_events(events_file)

    assert len(events) == 1
    assert events[0].kind == "agent.spawned"
    assert events[0].repo == "/tmp/r"


def test_aggregate_events_across_repos(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"

    # Create the runtime directories and write events
    ef_a = events_file_for_repo(repo_a)
    ef_a.parent.mkdir(parents=True, exist_ok=True)
    emit_event(ef_a, kind="loop.started", repo=str(repo_a), payload={"src": "a"})
    time.sleep(0.01)

    ef_b = events_file_for_repo(repo_b)
    ef_b.parent.mkdir(parents=True, exist_ok=True)
    emit_event(ef_b, kind="agent.spawned", repo=str(repo_b), payload={"src": "b"})
    time.sleep(0.01)

    emit_event(ef_a, kind="loop.exited", repo=str(repo_a), payload={"src": "a2"})

    merged = aggregate_events([repo_a, repo_b])
    assert len(merged) == 3
    # Should be sorted by timestamp across repos
    assert merged[0].ts <= merged[1].ts <= merged[2].ts
    repos_seen = {ev.repo for ev in merged}
    assert str(repo_a) in repos_seen
    assert str(repo_b) in repos_seen


def test_aggregate_events_skips_missing_repos(tmp_path: Path) -> None:
    repo_a = tmp_path / "repo-a"
    repo_missing = tmp_path / "repo-missing"

    ef_a = events_file_for_repo(repo_a)
    ef_a.parent.mkdir(parents=True, exist_ok=True)
    emit_event(ef_a, kind="loop.started", repo=str(repo_a), payload={})

    merged = aggregate_events([repo_a, repo_missing])
    assert len(merged) == 1


def test_tier_routing() -> None:
    # Tier 0 — informational (never routed to brain)
    assert TIER_ROUTING["session.started"] == 0
    assert TIER_ROUTING["session.ended"] == 0

    # Tier 1 spot checks
    assert TIER_ROUTING["loop.started"] == 1
    assert TIER_ROUTING["agent.spawned"] == 1
    assert TIER_ROUTING["agent.died"] == 1
    assert TIER_ROUTING["heartbeat.stale"] == 1
    assert TIER_ROUTING["spawn.failed"] == 1
    assert TIER_ROUTING["daemon.killed"] == 1

    # Tier 2 spot checks
    assert TIER_ROUTING["tasks.exhausted"] == 2
    assert TIER_ROUTING["repo.discovered"] == 2
    assert TIER_ROUTING["snapshot.collected"] == 2
    assert TIER_ROUTING["tier1.escalation"] == 2
    assert TIER_ROUTING["intent.continue"] == 2
    assert TIER_ROUTING["intent.parked"] == 2
    assert TIER_ROUTING["intent.needs_human"] == 2
    assert TIER_ROUTING["compliance.violation"] == 2

    # Tier 3
    assert TIER_ROUTING["tier2.escalation"] == 3


def test_events_file_for_repo(tmp_path: Path) -> None:
    repo = tmp_path / "my-repo"
    result = events_file_for_repo(repo)
    assert result == repo / EVENTS_REL_PATH


def test_intent_events_route_to_tier2() -> None:
    for kind in ("intent.continue", "intent.parked", "intent.needs_human"):
        assert TIER_ROUTING[kind] == 2, f"{kind} should be tier 2"


def test_compliance_violation_routes_to_tier2() -> None:
    assert TIER_ROUTING["compliance.violation"] == 2


def test_event_dataclass_fields() -> None:
    ev = Event(kind="loop.started", repo="/tmp/r", ts=1234567890.0, payload={"k": "v"})
    assert ev.kind == "loop.started"
    assert ev.repo == "/tmp/r"
    assert ev.ts == 1234567890.0
    assert ev.payload == {"k": "v"}
