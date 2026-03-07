# ABOUTME: Repo-local runtime supervision shell for Speedrift/WorkGraph repos
# ABOUTME: Collects runtime snapshots, builds worker ledgers, and runs dispatch loops

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.policy import DriftPolicy, load_drift_policy
from driftdriver.project_autopilot import get_ready_tasks
from driftdriver.workgraph import load_workgraph
from driftdriver.worker_monitor import check_worker_liveness

# -- Re-export state management for backwards compatibility --
from driftdriver.speedriftd_state import (  # noqa: F401
    CONTROL_MODES,
    _append_jsonl,
    _default_control,
    _iso_now,
    _normalize_control_state,
    _parse_iso_timestamp,
    _read_json,
    _safe_slug,
    _write_json,
    load_control_state,
    load_runtime_snapshot,
    runtime_paths,
    write_control_state,
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


def _latest_worker_events(project_dir: Path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in load_worker_events(project_dir):
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


def _event_timestamp(row: dict[str, Any] | None) -> float:
    if not isinstance(row, dict):
        return 0.0
    try:
        return float(row.get("ts") or 0.0)
    except Exception:
        return 0.0


def _worker_runtime(ctx: dict[str, Any], session_id: str) -> str:
    if session_id:
        return "claude"
    name = str(ctx.get("worker_name") or "").strip().lower()
    if "codex" in name:
        return "codex"
    if "tmux" in name:
        return "tmux"
    return "unknown"


def _normalize_health_status(
    *,
    raw_status: str,
    last_seen_ts: float,
    heartbeat_stale_after_seconds: int,
    output_stale_after_seconds: int,
    worker_timeout_seconds: int,
) -> str:
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


def _current_cycle_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"speedriftd-{stamp}"


def _worker_id(repo_name: str, task_id: str, session_id: str, worker_name: str) -> str:
    seed = session_id or worker_name or task_id or "worker"
    return f"{_safe_slug(repo_name)}-{_safe_slug(task_id)}-{_safe_slug(seed)}"


def _build_worker_snapshots(
    *,
    repo_name: str,
    project_dir: Path,
    workers: dict[str, Any],
    latest_events: dict[str, dict[str, Any]],
    cfg: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
        last_seen_ts = max(_event_timestamp(last_event), started_at)
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
            state = _normalize_health_status(
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
            "worker_id": _worker_id(repo_name, task_id, session_id, worker_name),
            "task_id": task_id,
            "task_title": task_title,
            "worker_name": worker_name,
            "session_id": session_id,
            "runtime": _worker_runtime(raw_ctx, session_id),
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
