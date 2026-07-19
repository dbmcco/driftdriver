# ABOUTME: Repo-local runtime supervision shell for Speedrift/WorkGraph repos
# ABOUTME: Collects runtime snapshots, builds worker ledgers, and runs dispatch loops

from __future__ import annotations

import fcntl
import json
import subprocess
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

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
    _lease_identity,
    _lease_is_active_raw,
    load_control_state,
    load_runtime_snapshot,
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


def _workgraph_service_running(project_dir: Path) -> bool | None:
    """Return service state when it can be read, otherwise ``None``."""
    paths = runtime_paths(project_dir)
    try:
        proc = subprocess.run(  # noqa: S603
            ["wg", "--dir", str(paths["wg_dir"]), "service", "status", "--json"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        return None
    if proc.returncode != 0:
        return None
    try:
        status = json.loads(proc.stdout or "")
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(status, dict):
        return None
    if isinstance(status.get("running"), bool):
        return status["running"]
    state = str(status.get("status") or "").strip().lower()
    if state in {"running", "stopped"}:
        return state == "running"
    return None


def _stop_workgraph_service(project_dir: Path) -> dict[str, Any]:
    """Best-effort stop of the local Workgraph coordinator.

    Keep command execution behind this private helper so the runtime transition
    is patchable and failures can be persisted as terminal evidence.
    """
    paths = runtime_paths(project_dir)
    try:
        proc = subprocess.run(  # noqa: S603
            ["wg", "--dir", str(paths["wg_dir"]), "service", "stop"],
            cwd=str(project_dir),
            capture_output=True,
            text=True,
            timeout=15,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout[:500],
            "stderr": proc.stderr[:500],
        }
    except Exception as exc:
        return {"exit_code": None, "stdout": "", "stderr": str(exc)[:200]}


@contextmanager
def _lease_expiry_lock(project_dir: Path):
    """Serialize expiry stop reservation, command, and evidence recording."""
    paths = runtime_paths(project_dir)
    lock_path = paths["dir"] / "lease-expiry-stop.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def handle_lease_expiry(
    project_dir: Path,
    *,
    control: Mapping[str, Any],
    previous_control: Mapping[str, Any] | None = None,
    previous_stop: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Stop an already-running coordinator once on lease expiry transition.

    A stop is considered only when the previous runtime snapshot had an active
    elevated lease and the current control is denied in an elevated mode. A
    prior failed stop for the same lease is also retried. This prevents a first
    cycle from stopping a coordinator for a pre-existing expired lease while
    allowing failures to recover. The persisted marker makes successful stops
    idempotent.
    """
    from driftdriver.speedriftd_state import (
        control_receipt_lock,
        evaluate_lease_expiry_stop,
        load_lease_expiry_stop,
        record_lease_expiry_stop,
        reserve_lease_expiry_stop,
    )

    previous = previous_control or {}
    previous_mode = str(previous.get("mode") or "").strip().lower()
    current_mode = str(control.get("mode") or "").strip().lower()
    prior_active_transition = (
        previous.get("lease_active") is True
        and previous_mode in {"supervise", "autonomous"}
    )
    current_owner = str(control.get("lease_owner") or "").strip()
    failed_release_transition = (
        not current_owner
        and isinstance(previous_stop, Mapping)
        and bool(str(previous_stop.get("stopped_lease_key") or ""))
        and previous_stop.get("stop_exit_code") != 0
    )
    released_transition = (prior_active_transition or failed_release_transition) and not current_owner
    release_key = (
        _lease_identity(previous)
        if str(previous.get("lease_owner") or "").strip()
        else str((previous_stop or {}).get("stopped_lease_key") or "")
    )
    expected_failed_key = release_key if released_transition else _lease_identity(control)
    same_lease_failed = (
        isinstance(previous_stop, Mapping)
        and str(previous_stop.get("stopped_lease_key") or "") == expected_failed_key
        and previous_stop.get("stop_exit_code") != 0
    )
    if (
        not (prior_active_transition or same_lease_failed)
        or current_mode not in {"supervise", "autonomous"}
        or _lease_is_active_raw(control)
    ):
        return None

    with _lease_expiry_lock(project_dir):
        # Serialize the final control recheck and the safety stop with lease
        # mutation. A writer must not acquire a new lease between this check
        # and stopping the coordinator for the expired lease.
        with control_receipt_lock(project_dir):
            # Control may have been re-armed after the snapshot was collected
            # but before this process acquired the expiry lock. Never stop a
            # coordinator for an active or replaced lease.
            locked_control = load_control_state(project_dir)
            if _lease_is_active_raw(locked_control):
                return None
            if released_transition:
                # An empty owner is a meaningful release only if no replacement
                # lease was acquired before the locked decision.
                if str(locked_control.get("lease_owner") or "").strip():
                    return None
                lease_key = release_key
            else:
                if _lease_identity(locked_control) != _lease_identity(control):
                    return None
                lease_key = _lease_identity(locked_control)
            marker = load_lease_expiry_stop(project_dir)
            if (
                str(marker.get("stopped_lease_key") or "") == lease_key
                and marker.get("stop_state") == "stopping"
            ):
                reconciled_decision = {
                    "lease_key": lease_key,
                    "reason": str(marker.get("reason") or "expired_lease"),
                    "mode": str(marker.get("mode") or ""),
                    "lease_owner": str(marker.get("lease_owner") or ""),
                    "prior_key": str(marker.get("prior_key") or ""),
                }
                service_running = _workgraph_service_running(project_dir)
                if service_running is False:
                    stop_result = {
                        "exit_code": 0,
                        "stdout": "reconciled stopped service state",
                        "stderr": "",
                    }
                elif service_running is None:
                    stop_result = {
                        "exit_code": None,
                        "unknown": True,
                        "stdout": "reconciled with unverifiable service state; no duplicate stop issued",
                        "stderr": "",
                    }
                else:
                    stop_result = _stop_workgraph_service(project_dir)
                return record_lease_expiry_stop(
                    project_dir,
                    reconciled_decision,
                    stop_result=stop_result,
                    reconciled=True,
                )

            if released_transition:
                decision = {
                    "lease_key": lease_key,
                    "reason": "released_lease",
                    "mode": previous_mode,
                    "lease_owner": str(
                        previous.get("lease_owner")
                        or (previous_stop or {}).get("lease_owner")
                        or ""
                    ).strip(),
                    "prior_key": str(marker.get("stopped_lease_key") or ""),
                }
                if str(marker.get("stopped_lease_key") or "") == lease_key:
                    return None
            else:
                decision = evaluate_lease_expiry_stop(locked_control, marker)
                if not decision:
                    return None

            reserve_lease_expiry_stop(project_dir, decision)
            stop_result = _stop_workgraph_service(project_dir)
            return record_lease_expiry_stop(project_dir, decision, stop_result=stop_result)


def run_runtime_cycle(project_dir: Path, *, policy: DriftPolicy | None = None) -> dict[str, Any]:
    previous_snapshot = load_runtime_snapshot(project_dir)
    snapshot = collect_runtime_snapshot(project_dir, policy=policy)
    expiry = handle_lease_expiry(
        project_dir,
        control=snapshot.get("control") or {},
        previous_control=previous_snapshot.get("control") or {},
        previous_stop=previous_snapshot.get("last_lease_expiry_stop"),
    )
    if expiry:
        snapshot["last_lease_expiry_stop"] = expiry
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
