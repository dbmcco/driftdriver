# ABOUTME: Dead agent detection via session-driver event stream inspection
# ABOUTME: Monitors worker liveness and triages dead/stale workers

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

WORKER_EVENTS_DIR = Path("/tmp/claude-workers")


@dataclass
class WorkerHealthState:
    session_id: str
    last_event_ts: float
    last_event_type: str
    event_count: int
    status: str  # alive, stale, dead, finished, unknown


@dataclass
class TriageAction:
    session_id: str
    action: str  # restart, abandon, escalate
    reason: str
    task_id: str = ""


def parse_last_event(events_file: Path) -> dict | None:
    """Efficiently read the last valid JSON line from an events file."""
    if not events_file.exists():
        return None

    try:
        size = events_file.stat().st_size
        if size == 0:
            return None

        # Read from end of file for efficiency
        read_size = min(size, 8192)
        with events_file.open("rb") as f:
            f.seek(max(0, size - read_size))
            tail = f.read().decode("utf-8", errors="replace")

        lines = [line.strip() for line in tail.splitlines() if line.strip()]
        for line in reversed(lines):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return None


def _count_events(events_file: Path) -> int:
    """Count the number of valid JSON lines in an events file."""
    if not events_file.exists():
        return 0
    count = 0
    try:
        with events_file.open() as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        json.loads(line)
                        count += 1
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return count


def _event_timestamp(event: dict) -> float:
    """Extract a numeric timestamp from an event dict."""
    ts = event.get("ts", 0)
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        # Try parsing ISO format as epoch fallback
        try:
            return float(ts)
        except ValueError:
            pass
    return 0.0


def check_worker_liveness(session_id: str) -> WorkerHealthState:
    """Check liveness of a worker by inspecting its events file."""
    events_file = WORKER_EVENTS_DIR / f"{session_id}.events.jsonl"

    if not events_file.exists():
        return WorkerHealthState(
            session_id=session_id,
            last_event_ts=0.0,
            last_event_type="",
            event_count=0,
            status="unknown",
        )

    last_event = parse_last_event(events_file)
    if last_event is None:
        return WorkerHealthState(
            session_id=session_id,
            last_event_ts=0.0,
            last_event_type="",
            event_count=0,
            status="unknown",
        )

    event_count = _count_events(events_file)
    last_ts = _event_timestamp(last_event)
    last_type = last_event.get("event", "")

    # Terminal events mean the worker is finished
    if last_type in ("session_end", "stop", "completed", "failed"):
        status = "finished"
    else:
        age = time.time() - last_ts if last_ts > 0 else float("inf")
        if age < 300:
            status = "alive"
        elif age < 600:
            status = "stale"
        else:
            status = "dead"

    return WorkerHealthState(
        session_id=session_id,
        last_event_ts=last_ts,
        last_event_type=last_type,
        event_count=event_count,
        status=status,
    )


def detect_dead_workers(
    workers: dict[str, str],
    timeout_seconds: float = 600,
) -> list[str]:
    """Check multiple workers and return session_ids of dead ones.

    Args:
        workers: mapping of session_id → task_id
        timeout_seconds: seconds since last event to consider dead
    """
    dead: list[str] = []
    for session_id in workers:
        state = check_worker_liveness(session_id)
        if state.status == "dead":
            dead.append(session_id)
        elif state.status == "stale" and state.last_event_ts > 0:
            age = time.time() - state.last_event_ts
            if age >= timeout_seconds:
                dead.append(session_id)
    return dead


def triage_dead_worker(
    worker_ctx: dict,
    strategy: str = "conservative",
) -> TriageAction:
    """Decide what to do with a dead worker.

    Args:
        worker_ctx: dict with keys session_id, task_id, status, drift_fail_count
        strategy: 'conservative' (escalate), 'aggressive' (restart), 'abandon'
    """
    session_id = worker_ctx.get("session_id", "")
    task_id = worker_ctx.get("task_id", "")
    drift_fails = worker_ctx.get("drift_fail_count", 0)

    if strategy == "abandon":
        return TriageAction(
            session_id=session_id,
            action="abandon",
            reason="strategy=abandon: marking task as failed",
            task_id=task_id,
        )

    if strategy == "aggressive":
        if drift_fails >= 3:
            return TriageAction(
                session_id=session_id,
                action="escalate",
                reason=f"too many drift failures ({drift_fails}), needs human review",
                task_id=task_id,
            )
        return TriageAction(
            session_id=session_id,
            action="restart",
            reason="strategy=aggressive: restarting worker",
            task_id=task_id,
        )

    # conservative (default)
    return TriageAction(
        session_id=session_id,
        action="escalate",
        reason="strategy=conservative: escalating dead worker for human review",
        task_id=task_id,
    )
