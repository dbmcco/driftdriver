# ABOUTME: Dispatch service — assigns ready tasks to available agent runtimes.
# ABOUTME: Runtime-agnostic worker matching, health normalization, and worker snapshot building.

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.speedriftd_state import _iso_now, _safe_slug
from driftdriver.worker_monitor import check_worker_liveness


def worker_runtime(ctx: dict[str, Any], session_id: str) -> str:
    """Detect worker runtime type from context and session ID."""
    if session_id:
        return "claude"
    name = str(ctx.get("worker_name") or "").strip().lower()
    if "codex" in name:
        return "codex"
    if "tmux" in name:
        return "tmux"
    return "unknown"


def normalize_health_status(
    *,
    raw_status: str,
    last_seen_ts: float,
    heartbeat_stale_after_seconds: int,
    output_stale_after_seconds: int,
    worker_timeout_seconds: int,
) -> str:
    """Normalize raw health probe status into a canonical dispatch state."""
    raw = str(raw_status or "").strip().lower()
    if raw == "alive":
        return "running"
    if raw == "stale":
        return "watch"
    if raw == "dead":
        return "stalled"
    if raw == "finished":
        return "done"

    if last_seen_ts <= 0:
        return "watch"

    age = max(0.0, time.time() - last_seen_ts)
    if age >= max(worker_timeout_seconds, heartbeat_stale_after_seconds):
        return "stalled"
    if age >= output_stale_after_seconds:
        return "watch"
    return "running"


def current_cycle_id() -> str:
    """Generate a timestamped cycle identifier."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"speedriftd-{stamp}"


def worker_id(repo_name: str, task_id: str, session_id: str, worker_name: str) -> str:
    """Build a deterministic worker identifier from context."""
    seed = session_id or worker_name or task_id or "worker"
    return f"{_safe_slug(repo_name)}-{_safe_slug(task_id)}-{_safe_slug(seed)}"


def event_timestamp(row: dict[str, Any] | None) -> float:
    """Extract a float timestamp from an event row, returning 0.0 on failure."""
    if not isinstance(row, dict):
        return 0.0
    try:
        return float(row.get("ts") or 0.0)
    except Exception:
        return 0.0


def _load_worker_events(project_dir: Path) -> list[dict]:
    """Load all worker events from the JSONL log."""
    f = project_dir / ".workgraph" / ".autopilot" / "workers.jsonl"
    if not f.exists():
        return []
    events = []
    for line in f.read_text().splitlines():
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def latest_worker_events(project_dir: Path) -> dict[str, dict[str, Any]]:
    """Return the most recent event per task_id from worker event logs."""
    latest: dict[str, dict[str, Any]] = {}
    for row in _load_worker_events(project_dir):
        if not isinstance(row, dict):
            continue
        task_id = str(row.get("task_id") or "").strip()
        if not task_id:
            continue
        ts = float(row.get("ts") or 0.0)
        previous = latest.get(task_id)
        if previous is None or float(previous.get("ts") or 0.0) <= ts:
            latest[task_id] = row
    return latest


def build_worker_snapshots(
    *,
    repo_name: str,
    project_dir: Path,
    workers: dict[str, Any],
    latest_events: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build active and terminal worker snapshot lists from raw worker state.

    Returns (active_workers, terminal_workers) sorted by (task_id, worker_id).
    """
    active_workers: list[dict[str, Any]] = []
    terminal_workers: list[dict[str, Any]] = []

    for task_id, raw_ctx in workers.items():
        if not isinstance(raw_ctx, dict):
            continue
        task_id = str(task_id or raw_ctx.get("task_id") or "").strip()
        if not task_id:
            continue
        task_title = str(raw_ctx.get("task_title") or "").strip()
        session_id = str(raw_ctx.get("session_id") or "").strip()
        worker_name = str(raw_ctx.get("worker_name") or "").strip()
        started_at = float(raw_ctx.get("started_at") or 0.0)
        stored_status = str(raw_ctx.get("status") or "pending").strip().lower()
        last_event = latest_events.get(task_id) or {}
        last_seen_ts = max(event_timestamp(last_event), started_at)
        health_status = ""
        last_event_type = str(last_event.get("event") or "")
        event_count = 0

        if session_id:
            health = check_worker_liveness(session_id)
            health_status = health.status
            if health.last_event_ts > 0:
                last_seen_ts = max(last_seen_ts, health.last_event_ts)
            last_event_type = health.last_event_type or last_event_type
            event_count = int(health.event_count or 0)

        state = stored_status
        if stored_status in {"running", "pending"}:
            state = normalize_health_status(
                raw_status=health_status,
                last_seen_ts=last_seen_ts,
                heartbeat_stale_after_seconds=int(cfg["heartbeat_stale_after_seconds"]),
                output_stale_after_seconds=int(cfg["output_stale_after_seconds"]),
                worker_timeout_seconds=int(cfg["worker_timeout_seconds"]),
            )
            if stored_status == "pending" and state == "running":
                state = "starting"
        elif stored_status in {"completed", "done"}:
            state = "done"
        elif stored_status in {"failed", "escalated"}:
            state = stored_status

        row = {
            "worker_id": worker_id(repo_name, task_id, session_id, worker_name),
            "task_id": task_id,
            "task_title": task_title,
            "worker_name": worker_name,
            "session_id": session_id,
            "runtime": worker_runtime(raw_ctx, session_id),
            "state": state,
            "started_at": _iso_now(started_at) if started_at > 0 else "",
            "last_heartbeat_at": _iso_now(last_seen_ts) if last_seen_ts > 0 else "",
            "last_output_at": _iso_now(last_seen_ts) if last_seen_ts > 0 else "",
            "last_event_type": last_event_type,
            "event_count": event_count,
            "drift_fail_count": int(raw_ctx.get("drift_fail_count") or 0),
            "drift_findings": list(raw_ctx.get("drift_findings") or []),
            "project_dir": str(project_dir),
        }
        if state in {"running", "starting", "watch", "stalled"}:
            active_workers.append(row)
        else:
            terminal_workers.append(row)

    active_workers.sort(key=lambda row: (str(row["task_id"]), str(row["worker_id"])))
    terminal_workers.sort(key=lambda row: (str(row["task_id"]), str(row["worker_id"])))
    return active_workers, terminal_workers
