# ABOUTME: Builds per-repo agent session history from events.jsonl and graph.jsonl.
# ABOUTME: Pure function: no writes, no daemon, no caching. Called on-demand from api.py.
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_STILL_RUNNING_THRESHOLD_SECONDS = 600  # 10 minutes
_SESSION_KINDS = {
    "session.started",
    "session.ended",
    "agent.died",
    "agent.completed",
    "loop.crashed",
    "heartbeat.stale",
}
_CRASH_KINDS = {"agent.died", "loop.crashed"}
_STALL_KINDS = {"heartbeat.stale"}


@dataclass
class _OpenSession:
    session_id: str
    agent_type: str
    started_at: float
    ended_at: float | None = None
    outcome: str = "unknown"
    tasks_completed: list[str] = field(default_factory=list)
    tasks_claimed: list[str] = field(default_factory=list)
    commits_in_window: int = 0
    task_titles: list[str] = field(default_factory=list)


def _epoch_to_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _iso_to_epoch(iso: str) -> float | None:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.timestamp()
    except (ValueError, AttributeError):
        return None


def _coerce_event_ts(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return _iso_to_epoch(text)


def _load_events(repo_path: Path) -> list[dict[str, Any]]:
    events_file = repo_path / ".workgraph" / "service" / "runtime" / "events.jsonl"
    if not events_file.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in events_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                events.append(obj)
        except json.JSONDecodeError:
            continue
    return events


def _load_tasks(repo_path: Path) -> list[dict[str, Any]]:
    graph_file = repo_path / ".workgraph" / "graph.jsonl"
    if not graph_file.exists():
        return []
    tasks: list[dict[str, Any]] = []
    for line in graph_file.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and obj.get("status") == "done":
                tasks.append(obj)
        except json.JSONDecodeError:
            continue
    return tasks


def build_agent_history(
    repo_path: Path,
    *,
    limit: int = 20,
    activity_digest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build agent session history from events.jsonl + graph.jsonl.

    Returns a dict with keys: sessions, total_sessions_in_file, history_since.
    """
    raw_events = _load_events(repo_path)
    if not raw_events:
        return {"sessions": [], "total_sessions_in_file": 0, "history_since": None}

    # Filter to session-relevant kinds and sort by ts ascending
    relevant = [
        e for e in raw_events
        if isinstance(e.get("kind"), str) and e["kind"] in _SESSION_KINDS
    ]
    relevant.sort(key=lambda e: _coerce_event_ts(e.get("ts")) or 0.0)

    # Oldest event timestamp for history_since
    all_timestamps = [
        ts for e in raw_events
        if (ts := _coerce_event_ts(e.get("ts"))) is not None
    ]
    oldest_ts = min(all_timestamps) if all_timestamps else None

    now = time.time()

    # Step 2: Build session spans
    open_sessions: dict[str, _OpenSession] = {}
    closed: list[_OpenSession] = []

    for event in relevant:
        kind = event.get("kind", "")
        ts = _coerce_event_ts(event.get("ts")) or 0.0
        payload = event.get("payload") or {}

        if kind == "session.started":
            actor_id = str(payload.get("actor_id") or f"anon-{ts:.0f}")
            cli = str(payload.get("cli") or "unknown")
            # If this actor_id already has an open session, close it as unknown
            if actor_id in open_sessions:
                prev = open_sessions.pop(actor_id)
                closed.append(prev)
            open_sessions[actor_id] = _OpenSession(
                session_id=actor_id,
                agent_type=cli,
                started_at=ts,
            )

        elif kind == "session.ended":
            actor_id = str(payload.get("actor_id") or "")
            if actor_id in open_sessions:
                sess = open_sessions.pop(actor_id)
                sess.ended_at = ts
                sess.outcome = "clean_exit"
                closed.append(sess)
            elif open_sessions:
                last_key = list(open_sessions)[-1]
                sess = open_sessions.pop(last_key)
                sess.ended_at = ts
                sess.outcome = "clean_exit"
                closed.append(sess)

        elif kind in _CRASH_KINDS:
            for sess in open_sessions.values():
                if sess.started_at <= ts:
                    sess.outcome = "crashed"
                    break

        elif kind in _STALL_KINDS:
            for sess in open_sessions.values():
                if sess.started_at <= ts:
                    sess.outcome = "stalled"
                    break

    # Step 3: Infer ends for remaining open sessions
    all_sessions = list(closed)
    open_list = sorted(open_sessions.values(), key=lambda s: s.started_at)
    for i, sess in enumerate(open_list):
        age = now - sess.started_at
        if age < _STILL_RUNNING_THRESHOLD_SECONDS:
            sess.outcome = "still_running"
        else:
            if i + 1 < len(open_list):
                sess.ended_at = open_list[i + 1].started_at - 1
        all_sessions.append(sess)

    total = len(all_sessions)

    # Step 4: Correlate tasks
    tasks = _load_tasks(repo_path)
    if tasks and all_sessions:
        earliest_start = min(s.started_at for s in all_sessions)
        for task in tasks:
            completed_at_iso = task.get("completed_at") or ""
            started_at_iso = task.get("started_at") or ""
            completed_ts = _iso_to_epoch(completed_at_iso)
            started_ts = _iso_to_epoch(started_at_iso)
            if completed_ts is None:
                continue
            if completed_ts < earliest_start:
                continue
            title = str(task.get("title") or "")[:80]
            task_id = str(task.get("id") or "")
            for sess in all_sessions:
                end = sess.ended_at or now
                if sess.started_at <= completed_ts <= end:
                    if task_id not in sess.tasks_completed:
                        sess.tasks_completed.append(task_id)
                        if len(sess.task_titles) < 3:
                            sess.task_titles.append(title)
                    break
                if started_ts is not None and sess.started_at <= started_ts <= end:
                    if task_id not in sess.tasks_claimed:
                        sess.tasks_claimed.append(task_id)

    # Step 5: Correlate commits from activity_digest
    if activity_digest and all_sessions:
        repo_name = repo_path.name
        repos_list = activity_digest.get("repos") or []
        for repo_entry in repos_list:
            if str(repo_entry.get("name") or "") != repo_name:
                continue
            commits = repo_entry.get("timeline") or []
            for commit in commits:
                commit_ts = _iso_to_epoch(str(commit.get("timestamp") or ""))
                if commit_ts is None:
                    continue
                for sess in all_sessions:
                    end = sess.ended_at or now
                    if sess.started_at <= commit_ts <= end:
                        sess.commits_in_window += 1
                        break

    # Step 6: Sort most-recent first, cap at limit
    all_sessions.sort(key=lambda s: s.started_at, reverse=True)
    capped = all_sessions[:limit]

    def _to_dict(s: _OpenSession) -> dict[str, Any]:
        return {
            "session_id": s.session_id,
            "agent_type": s.agent_type,
            "started_at": _epoch_to_iso(s.started_at),
            "ended_at": _epoch_to_iso(s.ended_at) if s.ended_at is not None else None,
            "duration_seconds": int(s.ended_at - s.started_at) if s.ended_at is not None else None,
            "tasks_completed": s.tasks_completed,
            "tasks_claimed": s.tasks_claimed,
            "commits_in_window": s.commits_in_window,
            "outcome": s.outcome,
            "task_titles": s.task_titles,
        }

    return {
        "sessions": [_to_dict(s) for s in capped],
        "total_sessions_in_file": total,
        "history_since": _epoch_to_iso(oldest_ts) if oldest_ts else None,
    }
