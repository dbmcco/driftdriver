# ABOUTME: Repo-local runtime supervision shell for Speedrift/WorkGraph repos
# ABOUTME: Writes runtime snapshots, worker ledgers, leases, and stall markers under .workgraph/service/runtime/

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.autopilot_state import load_run_state, load_worker_events
from driftdriver.policy import DriftPolicy, load_drift_policy
from driftdriver.project_autopilot import get_ready_tasks
from driftdriver.workgraph import find_workgraph_dir, load_workgraph
from driftdriver.worker_monitor import check_worker_liveness


def _iso_now(ts: float | None = None) -> str:
    if ts is None:
        dt = datetime.now(timezone.utc)
    else:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.isoformat()


def _safe_slug(value: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip())
    text = text.strip("-._")
    return text or "unknown"


def runtime_paths(project_dir: Path) -> dict[str, Path]:
    wg_dir = find_workgraph_dir(project_dir)
    base = wg_dir / "service" / "runtime"
    return {
        "wg_dir": wg_dir,
        "dir": base,
        "current": base / "current.json",
        "workers": base / "workers.jsonl",
        "stalls": base / "stalls.jsonl",
        "leases": base / "leases.json",
        "events_dir": base / "events",
        "heartbeats_dir": base / "heartbeats",
        "results_dir": base / "results",
    }


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=False) + "\n")


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
    elif ready_tasks:
        next_action = f"dispatch ready task {ready_tasks[0]['id']}"
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
            "heartbeat_stale_after_seconds": int(cfg.get("heartbeat_stale_after_seconds", 300)),
            "output_stale_after_seconds": int(cfg.get("output_stale_after_seconds", 600)),
            "worker_timeout_seconds": int(cfg.get("worker_timeout_seconds", 1800)),
        },
        "ready_tasks": ready_tasks,
        "in_progress_tasks": in_progress_tasks,
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


def load_runtime_snapshot(project_dir: Path) -> dict[str, Any]:
    return _read_json(runtime_paths(project_dir)["current"])


def write_runtime_snapshot(project_dir: Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    paths = runtime_paths(project_dir)
    now_iso = str(snapshot.get("updated_at") or _iso_now())
    event_date = now_iso[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    event_file = paths["events_dir"] / f"{event_date}.jsonl"

    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["events_dir"].mkdir(parents=True, exist_ok=True)
    paths["heartbeats_dir"].mkdir(parents=True, exist_ok=True)
    paths["results_dir"].mkdir(parents=True, exist_ok=True)
    paths["workers"].touch(exist_ok=True)
    paths["stalls"].touch(exist_ok=True)

    _write_json(paths["current"], snapshot)

    leases = {
        "repo": snapshot.get("repo"),
        "updated_at": now_iso,
        "active_leases": [
            {
                "task_id": row.get("task_id"),
                "worker_id": row.get("worker_id"),
                "runtime": row.get("runtime"),
                "acquired_at": row.get("started_at"),
            }
            for row in snapshot.get("active_workers", [])
            if isinstance(row, dict)
        ],
    }
    _write_json(paths["leases"], leases)

    for row in snapshot.get("active_workers", []):
        if not isinstance(row, dict):
            continue
        heartbeat = {
            "worker_id": row.get("worker_id"),
            "repo": snapshot.get("repo"),
            "task_id": row.get("task_id"),
            "runtime": row.get("runtime"),
            "state": row.get("state"),
            "last_heartbeat_at": row.get("last_heartbeat_at"),
            "last_output_at": row.get("last_output_at"),
            "updated_at": now_iso,
        }
        _write_json(paths["heartbeats_dir"] / f"{_safe_slug(str(row.get('worker_id') or 'worker'))}.json", heartbeat)
        _append_jsonl(
            paths["workers"],
            {
                "ts": now_iso,
                "repo": snapshot.get("repo"),
                "worker_id": row.get("worker_id"),
                "task_id": row.get("task_id"),
                "runtime": row.get("runtime"),
                "state": row.get("state"),
                "event_type": "heartbeat",
            },
        )
        _append_jsonl(
            event_file,
            {
                "event_id": f"evt_{uuid.uuid4().hex}",
                "ts": now_iso,
                "repo": snapshot.get("repo"),
                "cycle_id": snapshot.get("cycle_id"),
                "worker_id": row.get("worker_id"),
                "task_id": row.get("task_id"),
                "runtime": row.get("runtime"),
                "event_type": "heartbeat",
                "state": row.get("state"),
                "payload": {
                    "alive": str(row.get("state") or "") not in {"failed", "stalled"},
                    "last_output_at": row.get("last_output_at"),
                    "event_count": row.get("event_count"),
                },
            },
        )

    for row in snapshot.get("stalled_task_ids", []):
        task_id = str(row or "").strip()
        if not task_id:
            continue
        stall_row = {
            "ts": now_iso,
            "repo": snapshot.get("repo"),
            "task_id": task_id,
            "event_type": "stall_detected",
            "reason": "worker_stalled_or_missing",
            "next_action": snapshot.get("next_action"),
        }
        _append_jsonl(paths["stalls"], stall_row)
        _append_jsonl(
            event_file,
            {
                "event_id": f"evt_{uuid.uuid4().hex}",
                "ts": now_iso,
                "repo": snapshot.get("repo"),
                "cycle_id": snapshot.get("cycle_id"),
                "worker_id": "",
                "task_id": task_id,
                "runtime": "",
                "event_type": "stall_detected",
                "state": "stalled",
                "payload": {
                    "reason": "worker_stalled_or_missing",
                    "next_action": snapshot.get("next_action"),
                },
            },
        )

    for row in snapshot.get("terminal_workers", []):
        if not isinstance(row, dict):
            continue
        terminal_state = str(row.get("state") or "")
        if terminal_state not in {"done", "completed", "failed", "escalated"}:
            continue
        result = {
            "repo": snapshot.get("repo"),
            "worker_id": row.get("worker_id"),
            "task_id": row.get("task_id"),
            "runtime": row.get("runtime"),
            "terminal_state": terminal_state,
            "updated_at": now_iso,
            "summary": f"{row.get('task_id')} -> {terminal_state}",
        }
        _write_json(paths["results_dir"] / f"{_safe_slug(str(row.get('worker_id') or 'worker'))}.json", result)

    _append_jsonl(
        event_file,
        {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "ts": now_iso,
            "repo": snapshot.get("repo"),
            "cycle_id": snapshot.get("cycle_id"),
            "worker_id": "",
            "task_id": "",
            "runtime": "",
            "event_type": "repo_service_state",
            "state": snapshot.get("daemon_state"),
            "payload": {
                "active_worker_count": len(snapshot.get("active_workers", [])),
                "active_task_ids": list(snapshot.get("active_task_ids", [])),
                "stalled_task_ids": list(snapshot.get("stalled_task_ids", [])),
                "runtime_mix": list(snapshot.get("runtime_mix", [])),
                "next_action": snapshot.get("next_action"),
            },
        },
    )
    return snapshot


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
