# ABOUTME: State management for speedriftd runtime supervisor.
# ABOUTME: Handles disk I/O for control state, runtime snapshots, leases, and events.

from __future__ import annotations

import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.policy import DriftPolicy, load_drift_policy
from driftdriver.workgraph import find_workgraph_dir

CONTROL_MODES = {"manual", "observe", "supervise", "autonomous"}


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


def _parse_iso_timestamp(raw: str) -> float:
    value = str(raw or "").strip()
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


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
        "control": base / "control.json",
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


def _default_control(repo_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    mode = str(cfg.get("default_mode", "observe") or "observe").strip().lower()
    if mode not in CONTROL_MODES:
        mode = "observe"
    dispatch_enabled = mode in {"supervise", "autonomous"}
    return {
        "repo": repo_name,
        "updated_at": _iso_now(),
        "mode": mode,
        "dispatch_enabled": dispatch_enabled,
        "interactive_service_start": dispatch_enabled,
        "lease_owner": "",
        "lease_acquired_at": "",
        "lease_ttl_seconds": int(cfg.get("default_lease_ttl_seconds", 0) or 0),
        "lease_expires_at": "",
        "lease_active": False,
        "source": "default",
        "reason": "default runtime control",
    }


def _normalize_control_state(raw: dict[str, Any], *, repo_name: str, cfg: dict[str, Any]) -> dict[str, Any]:
    control = _default_control(repo_name, cfg)
    if isinstance(raw, dict):
        control.update({k: v for k, v in raw.items() if k in control or k in {"repo"}})
    mode = str(control.get("mode") or control["mode"]).strip().lower()
    if mode not in CONTROL_MODES:
        mode = control["mode"]
    control["mode"] = mode
    control["dispatch_enabled"] = mode in {"supervise", "autonomous"}
    control["interactive_service_start"] = bool(control["dispatch_enabled"])
    control["repo"] = repo_name
    control["lease_owner"] = str(control.get("lease_owner") or "").strip()
    control["lease_ttl_seconds"] = max(0, int(control.get("lease_ttl_seconds") or 0))
    acquired = str(control.get("lease_acquired_at") or "").strip()
    expires = str(control.get("lease_expires_at") or "").strip()
    expires_ts = _parse_iso_timestamp(expires)
    if control["lease_owner"]:
        if control["lease_ttl_seconds"] <= 0:
            control["lease_active"] = True
        else:
            control["lease_active"] = expires_ts > time.time()
    else:
        control["lease_active"] = False
        control["lease_acquired_at"] = ""
        control["lease_expires_at"] = ""
    if not acquired and control["lease_owner"] and control["lease_active"]:
        control["lease_acquired_at"] = _iso_now()
    control["updated_at"] = str(control.get("updated_at") or _iso_now())
    control["source"] = str(control.get("source") or "default")
    control["reason"] = str(control.get("reason") or "runtime control update")
    return control


def load_control_state(project_dir: Path, *, policy: DriftPolicy | None = None) -> dict[str, Any]:
    paths = runtime_paths(project_dir)
    project_dir = paths["wg_dir"].parent.resolve()
    repo_name = project_dir.name
    policy = policy or load_drift_policy(paths["wg_dir"])
    cfg = dict(getattr(policy, "speedriftd", {}) or {})
    raw = _read_json(paths["control"])
    return _normalize_control_state(raw, repo_name=repo_name, cfg=cfg)


def write_control_state(
    project_dir: Path,
    *,
    policy: DriftPolicy | None = None,
    mode: str | None = None,
    lease_owner: str | None = None,
    lease_ttl_seconds: int | None = None,
    release_lease: bool = False,
    source: str = "cli",
    reason: str = "",
) -> dict[str, Any]:
    paths = runtime_paths(project_dir)
    project_dir = paths["wg_dir"].parent.resolve()
    repo_name = project_dir.name
    policy = policy or load_drift_policy(paths["wg_dir"])
    cfg = dict(getattr(policy, "speedriftd", {}) or {})
    control = load_control_state(project_dir, policy=policy)
    now_iso = _iso_now()
    if mode is not None:
        normalized_mode = str(mode or "").strip().lower()
        if normalized_mode in CONTROL_MODES:
            control["mode"] = normalized_mode
    if lease_ttl_seconds is not None:
        control["lease_ttl_seconds"] = max(0, int(lease_ttl_seconds))
    if release_lease:
        control["lease_owner"] = ""
        control["lease_acquired_at"] = ""
        control["lease_expires_at"] = ""
        control["lease_active"] = False
    elif lease_owner is not None:
        owner = str(lease_owner or "").strip()
        control["lease_owner"] = owner
        if owner:
            control["lease_acquired_at"] = now_iso
            ttl = int(control.get("lease_ttl_seconds") or 0)
            control["lease_expires_at"] = _iso_now(time.time() + ttl) if ttl > 0 else ""
            control["lease_active"] = True
        else:
            control["lease_acquired_at"] = ""
            control["lease_expires_at"] = ""
            control["lease_active"] = False
    control["updated_at"] = now_iso
    control["source"] = source
    if reason:
        control["reason"] = reason
    normalized = _normalize_control_state(control, repo_name=repo_name, cfg=cfg)
    _write_json(paths["control"], normalized)
    return normalized


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
    _write_json(paths["control"], snapshot.get("control") if isinstance(snapshot.get("control"), dict) else {})

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
                "control_mode": ((snapshot.get("control") or {}).get("mode") if isinstance(snapshot.get("control"), dict) else ""),
                "lease_owner": ((snapshot.get("control") or {}).get("lease_owner") if isinstance(snapshot.get("control"), dict) else ""),
            },
        },
    )
    return snapshot
