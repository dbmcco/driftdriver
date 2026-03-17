# ABOUTME: Factory brain event schema, writer, reader, and cross-repo aggregator.
# ABOUTME: Events are JSONL records routed by tier (1=critical, 2=operational, 3=escalation).
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

EVENTS_FILENAME = "events.jsonl"
EVENTS_REL_PATH = Path(".workgraph") / "service" / "runtime" / EVENTS_FILENAME

TIER_ROUTING: dict[str, int] = {
    # Tier 0 — informational (never routed to brain, audit trail only)
    "session.started": 0,
    "session.ended": 0,
    # Tier 1 — critical lifecycle events
    "loop.started": 1,
    "loop.exited": 1,
    "loop.crashed": 1,
    "agent.spawned": 1,
    "agent.died": 1,
    "agent.completed": 1,
    "spawn.failed": 1,
    "daemon.killed": 1,
    "heartbeat.stale": 1,
    # Tier 2 — operational events
    "tasks.exhausted": 2,
    "repo.discovered": 2,
    "repo.enrolled": 2,
    "repo.unenrolled": 2,
    "attractor.converged": 2,
    "attractor.plateaued": 2,
    "snapshot.collected": 2,
    "tier1.escalation": 2,
    "intent.continue": 2,
    "intent.parked": 2,
    "intent.needs_human": 2,
    "compliance.violation": 2,
    # Tier 3 — escalation
    "tier2.escalation": 3,
}


@dataclass
class Event:
    kind: str
    repo: str
    ts: float
    payload: dict


def emit_event(
    events_file: Path,
    *,
    kind: str,
    repo: str,
    payload: dict,
) -> Event:
    """Append a single JSONL event record and return the Event."""
    ts = time.time()
    record = {"kind": kind, "repo": repo, "ts": ts, "payload": payload}
    events_file.parent.mkdir(parents=True, exist_ok=True)
    with events_file.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return Event(kind=kind, repo=repo, ts=ts, payload=payload)


def read_events(
    events_file: Path,
    *,
    since: float | None = None,
    limit: int = 200,
) -> list[Event]:
    """Read JSONL events, optionally filtering by timestamp. Returns sorted by ts."""
    if not events_file.exists():
        return []

    events: list[Event] = []
    for line in events_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = Event(
            kind=record["kind"],
            repo=record["repo"],
            ts=record["ts"],
            payload=record.get("payload", {}),
        )
        if since is not None and ev.ts <= since:
            continue
        events.append(ev)

    events.sort(key=lambda e: e.ts)
    return events[:limit]


def aggregate_events(
    repo_paths: list[Path],
    *,
    since: float | None = None,
    limit: int = 200,
) -> list[Event]:
    """Read events from multiple repos, merge and sort by timestamp."""
    all_events: list[Event] = []
    for repo_path in repo_paths:
        ef = events_file_for_repo(repo_path)
        all_events.extend(read_events(ef, since=since, limit=limit))

    all_events.sort(key=lambda e: e.ts)
    return all_events[:limit]


def events_file_for_repo(repo_path: Path) -> Path:
    """Return the canonical events file path for a repo."""
    return repo_path / EVENTS_REL_PATH
