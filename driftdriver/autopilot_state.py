# ABOUTME: Persistent state for project autopilot runs
# ABOUTME: Tracks worker contexts, run progress, and findings across loop iterations

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .project_autopilot import AutopilotRun, WorkerContext


def autopilot_dir(project_dir: Path) -> Path:
    """Get the autopilot state directory."""
    return project_dir / ".workgraph" / ".autopilot"


def ensure_dir(project_dir: Path) -> Path:
    """Ensure autopilot state directory exists and return it."""
    d = autopilot_dir(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_worker_event(project_dir: Path, worker: WorkerContext, event: str) -> None:
    """Append a worker event to workers.jsonl."""
    d = ensure_dir(project_dir)
    entry = {
        "ts": time.time(),
        "event": event,
        "task_id": worker.task_id,
        "task_title": worker.task_title,
        "worker_name": worker.worker_name,
        "status": worker.status,
        "drift_fail_count": worker.drift_fail_count,
        "drift_findings": worker.drift_findings,
    }
    with open(d / "workers.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def save_run_state(project_dir: Path, run: AutopilotRun) -> None:
    """Save current run state as JSON snapshot."""
    d = ensure_dir(project_dir)
    state = {
        "ts": time.time(),
        "goal": run.config.goal,
        "loop_count": run.loop_count,
        "completed_tasks": sorted(run.completed_tasks),
        "failed_tasks": sorted(run.failed_tasks),
        "escalated_tasks": sorted(run.escalated_tasks),
        "started_at": run.started_at,
        "workers": {
            tid: {
                "task_id": ctx.task_id,
                "task_title": ctx.task_title,
                "worker_name": ctx.worker_name,
                "status": ctx.status,
                "drift_fail_count": ctx.drift_fail_count,
                "drift_findings": ctx.drift_findings,
            }
            for tid, ctx in run.workers.items()
        },
    }
    (d / "run-state.json").write_text(json.dumps(state, indent=2))


def load_run_state(project_dir: Path) -> dict | None:
    """Load the last saved run state."""
    f = autopilot_dir(project_dir) / "run-state.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except (json.JSONDecodeError, TypeError):
        return None


def load_worker_events(project_dir: Path) -> list[dict]:
    """Load all worker events from the JSONL log."""
    f = autopilot_dir(project_dir) / "workers.jsonl"
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


def clear_run_state(project_dir: Path) -> None:
    """Remove run state files (for fresh start)."""
    d = autopilot_dir(project_dir)
    for name in ("run-state.json", "workers.jsonl"):
        f = d / name
        if f.exists():
            f.unlink()
