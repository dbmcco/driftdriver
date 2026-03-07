# ABOUTME: Repo-local runtime supervision shell for Speedrift/WorkGraph repos
# ABOUTME: Collects runtime snapshots, builds worker ledgers, and runs dispatch loops

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.dispatch import (
    build_worker_snapshots as _build_worker_snapshots,
    current_cycle_id as _current_cycle_id,
    latest_worker_events as _latest_worker_events,
)
from driftdriver.policy import DriftPolicy, load_drift_policy
from driftdriver.project_autopilot import get_ready_tasks
from driftdriver.workgraph import load_workgraph

from driftdriver.speedriftd_state import (
    _iso_now,
    load_control_state,
    runtime_paths,
    write_runtime_snapshot,
)


def _autopilot_dir(project_dir: Path) -> Path:
    """Get the autopilot state directory."""
    return project_dir / ".workgraph" / ".autopilot"


def load_run_state(project_dir: Path) -> dict | None:
    """Load the last saved run state."""
    f = _autopilot_dir(project_dir) / "run-state.json"
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text())
    except (json.JSONDecodeError, TypeError):
        return None


def load_worker_events(project_dir: Path) -> list[dict]:
    """Load all worker events from the JSONL log."""
    f = _autopilot_dir(project_dir) / "workers.jsonl"
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


def collect_runtime_snapshot(project_dir: Path, *, policy: DriftPolicy | None = None) -> dict[str, Any]:
    paths = runtime_paths(project_dir)
    wg_dir = paths["wg_dir"]
    project_dir = wg_dir.parent.resolve()
    repo_name = project_dir.name
    policy = policy or load_drift_policy(wg_dir)
    cfg = dict(getattr(policy, "speedriftd", {}) or {})
    control = load_control_state(project_dir, policy=policy)
    wg = load_workgraph(wg_dir)

    ready_tasks = get_ready_tasks(project_dir)
    run_state = load_run_state(project_dir) or {}
    workers = run_state.get("workers") if isinstance(run_state.get("workers"), dict) else {}
    latest_events = _latest_worker_events(project_dir)
    active_workers, terminal_workers = _build_worker_snapshots(
        repo_name=repo_name,
        project_dir=project_dir,
        workers=workers,
        latest_events=latest_events,
        cfg=cfg,
    )

    in_progress_tasks = [
        {
            "id": str(task.get("id") or ""),
            "title": str(task.get("title") or ""),
            "status": "in-progress",
        }
        for task in wg.tasks.values()
        if str(task.get("status") or "").strip().lower() == "in-progress"
    ]
    in_progress_tasks.sort(key=lambda row: str(row.get("id") or ""))

    active_task_ids = [str(row["task_id"]) for row in active_workers if str(row.get("task_id") or "")]
    stalled_workers = [row for row in active_workers if str(row.get("state") or "") == "stalled"]
    stalled_task_ids = sorted({str(row["task_id"]) for row in stalled_workers if str(row.get("task_id") or "")})
    runtime_mix = sorted({str(row["runtime"]) for row in active_workers if str(row.get("runtime") or "")})

    # Detect manually-claimed tasks (in-progress but no matching active worker).
    # When respect_manual_claims is true, suppress auto-dispatch while humans work.
    manual_claim_ids = sorted(
        {str(t["id"]) for t in in_progress_tasks if str(t["id"]) not in set(active_task_ids)}
    )
    respect_manual = bool(cfg.get("respect_manual_claims", True))
    dispatch_blocked_by_manual = respect_manual and bool(manual_claim_ids)

    daemon_state = "idle"
    if active_workers:
        daemon_state = "stalled" if stalled_task_ids and len(stalled_task_ids) == len(active_workers) else "running"
    elif stalled_task_ids or in_progress_tasks:
        daemon_state = "stalled"

    next_action = "await new ready work"
    if stalled_task_ids:
        next_action = f"investigate stalled task {stalled_task_ids[0]}"
    elif active_workers:
        next_action = "continue supervision"
    elif dispatch_blocked_by_manual:
        next_action = (
            f"dispatch paused: manual claim on {manual_claim_ids[0]} "
            "(respect_manual_claims=true)"
        )
    elif ready_tasks and bool(control.get("dispatch_enabled")):
        next_action = f"dispatch ready task {ready_tasks[0]['id']}"
    elif ready_tasks:
        next_action = (
            f"{control.get('mode', 'observe')} mode: ready task {ready_tasks[0]['id']} "
            "waiting for explicit supervisor"
        )
    elif in_progress_tasks:
        next_action = f"reconcile in-progress task {in_progress_tasks[0]['id']}"

    heartbeat_ages = []
    for row in active_workers:
        raw_last = str(row.get("last_heartbeat_at") or "").strip()
        if not raw_last:
            continue
        try:
            when = datetime.fromisoformat(raw_last.replace("Z", "+00:00"))
        except ValueError:
            continue
        heartbeat_ages.append(max(0, int(time.time() - when.timestamp())))

    return {
        "repo": repo_name,
        "project_dir": str(project_dir),
        "daemon_state": daemon_state,
        "updated_at": _iso_now(),
        "cycle_id": _current_cycle_id(),
        "policy": {
            "enabled": bool(cfg.get("enabled", True)),
            "interval_seconds": int(cfg.get("interval_seconds", 30)),
            "max_concurrent_workers": int(cfg.get("max_concurrent_workers", 2)),
            "respect_manual_claims": respect_manual,
            "heartbeat_stale_after_seconds": int(cfg.get("heartbeat_stale_after_seconds", 300)),
            "output_stale_after_seconds": int(cfg.get("output_stale_after_seconds", 600)),
            "worker_timeout_seconds": int(cfg.get("worker_timeout_seconds", 1800)),
        },
        "control": control,
        "ready_tasks": ready_tasks,
        "in_progress_tasks": in_progress_tasks,
        "manual_claim_ids": manual_claim_ids,
        "dispatch_blocked_by_manual": dispatch_blocked_by_manual,
        "active_workers": active_workers,
        "terminal_workers": terminal_workers,
        "stalled_task_ids": stalled_task_ids,
        "active_task_ids": active_task_ids,
        "runtime_mix": runtime_mix,
        "last_heartbeat_age_seconds": min(heartbeat_ages) if heartbeat_ages else None,
        "next_action": next_action,
        "autopilot_goal": str(run_state.get("goal") or ""),
        "autopilot_loop_count": int(run_state.get("loop_count") or 0),
    }


def run_runtime_cycle(project_dir: Path, *, policy: DriftPolicy | None = None) -> dict[str, Any]:
    snapshot = collect_runtime_snapshot(project_dir, policy=policy)
    return write_runtime_snapshot(project_dir, snapshot)


def run_runtime_loop(
    project_dir: Path,
    *,
    interval_seconds: int,
    max_cycles: int = 0,
    policy: DriftPolicy | None = None,
) -> dict[str, Any]:
    completed = 0
    latest: dict[str, Any] = {}
    while True:
        latest = run_runtime_cycle(project_dir, policy=policy)
        completed += 1
        if max_cycles > 0 and completed >= max_cycles:
            break
        time.sleep(max(1, interval_seconds))
    latest["cycles_completed"] = completed
    return latest
