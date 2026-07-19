# ABOUTME: State management for speedriftd runtime supervisor.
# ABOUTME: Handles disk I/O for control state, runtime snapshots, leases, and events.

from __future__ import annotations

import fcntl
import json
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from driftdriver.policy import DriftPolicy, load_drift_policy
from driftdriver.workgraph import find_workgraph_dir

CONTROL_MODES = {"manual", "observe", "supervise", "autonomous"}
CONTROL_RECEIPT_LOCK_FILENAME = "control-receipt.lock"


@contextmanager
def control_receipt_lock(project_dir: Path) -> Iterator[None]:
    """Serialize control mutations with audit receipt appends."""
    lock_path = runtime_paths(project_dir)["dir"] / CONTROL_RECEIPT_LOCK_FILENAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def directives_allowed_for_mode(mode: str) -> set[str]:
    """Map control mode to the set of directive actions allowed.

    - observe / manual: no directives (read-only)
    - supervise: service health + logging only
    - autonomous: full directive vocabulary
    """
    if mode == "supervise":
        return {"start_service", "stop_service", "log_to_task"}
    if mode == "autonomous":
        return {
            "create_task", "claim_task", "complete_task", "fail_task",
            "start_service", "stop_service", "log_to_task",
            "evolve_prompt", "dispatch_to_peer", "block_task",
            "create_validation", "create_upstream_pr",
        }
    # observe, manual → no directives
    return set()


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


def _coerce_lease_ttl(raw: Any) -> tuple[int, bool]:
    """Return a non-negative TTL and whether the raw value was valid."""
    if raw is None or raw == "":
        return 0, True
    try:
        return max(0, int(raw)), True
    except (TypeError, ValueError):
        return 0, False


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
        "lease_ttl_valid": True,
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
    ttl, ttl_valid = _coerce_lease_ttl(control.get("lease_ttl_seconds"))
    control["lease_ttl_seconds"] = ttl
    control["lease_ttl_valid"] = ttl_valid
    acquired = str(control.get("lease_acquired_at") or "").strip()
    expires = str(control.get("lease_expires_at") or "").strip()
    expires_ts = _parse_iso_timestamp(expires)
    if not ttl_valid:
        control["lease_active"] = False
        control["reason"] = "malformed lease_ttl_seconds"
    elif control["lease_owner"]:
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


def dispatch_authority(control: Mapping[str, Any]) -> dict[str, Any]:
    mode = str(control.get("mode") or "observe").strip().lower()
    lease_owner = str(control.get("lease_owner") or "").strip()
    raw_lease_active = control.get("lease_active")
    lease_active = raw_lease_active is True
    if mode not in {"supervise", "autonomous"}:
        reason = "mode does not permit dispatch"
        enabled = False
    elif not lease_owner:
        reason = "lease owner is missing"
        enabled = False
    elif control.get("lease_ttl_valid") is False or not _coerce_lease_ttl(control.get("lease_ttl_seconds"))[1]:
        reason = "lease_ttl_seconds value is malformed"
        enabled = False
    elif not isinstance(raw_lease_active, bool):
        reason = "lease_active value is malformed"
        enabled = False
    elif not lease_active:
        reason = "lease is not active"
        enabled = False
    else:
        reason = "active lease permits dispatch"
        enabled = True
    return {
        "enabled": enabled,
        "mode": mode,
        "lease_active": lease_active,
        "reason": reason,
    }


def _apply_runtime_gate(control: dict[str, Any]) -> dict[str, Any]:
    """Keep dispatch and service start disabled without an active lease."""
    dispatch_enabled = dispatch_authority(control)["enabled"]
    control["dispatch_enabled"] = dispatch_enabled
    control["interactive_service_start"] = dispatch_enabled
    return control


def load_control_state(project_dir: Path, *, policy: DriftPolicy | None = None) -> dict[str, Any]:
    paths = runtime_paths(project_dir)
    project_dir = paths["wg_dir"].parent.resolve()
    repo_name = project_dir.name
    policy = policy or load_drift_policy(paths["wg_dir"])
    cfg = dict(getattr(policy, "speedriftd", {}) or {})
    raw = _read_json(paths["control"])
    return _apply_runtime_gate(_normalize_control_state(raw, repo_name=repo_name, cfg=cfg))


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
    with control_receipt_lock(project_dir):
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
        normalized = _apply_runtime_gate(
            _normalize_control_state(control, repo_name=repo_name, cfg=cfg)
        )
        _write_json(paths["control"], normalized)
        return normalized


# ---------------------------------------------------------------------------
# Lease-expiry coordinator stop (idempotent transition enforcement)
# ---------------------------------------------------------------------------
#
# When a lease that permitted dispatch transitions to an expired/denied state,
# an already-running coordinator may otherwise keep dispatching. The helpers
# below let the runtime loop detect that transition and stop/revokes the
# coordinator exactly once, persisting terminal evidence keyed by the expired
# lease identity so repeated cycles do not duplicate the stop.

LEASE_EXPIRY_STOP_FILENAME = "lease-expiry-stop.json"


def _lease_is_active_raw(control: Mapping[str, Any]) -> bool:
    """Recompute whether a lease is active from raw control fields.

    Pure mirror of the lease-active derivation in ``_normalize_control_state``.
    Used by the expiry evaluator so transition detection does not depend on a
    precomputed (and possibly stale) ``lease_active`` flag.
    """
    owner = str(control.get("lease_owner") or "").strip()
    if not owner:
        return False
    if control.get("lease_ttl_valid") is False:
        return False
    ttl, ttl_valid = _coerce_lease_ttl(control.get("lease_ttl_seconds"))
    if not ttl_valid:
        return False
    if ttl <= 0:
        return True
    return _parse_iso_timestamp(str(control.get("lease_expires_at") or "")) > time.time()


def _lease_identity(control: Mapping[str, Any]) -> str:
    """Stable identity for a single lease instance (owner + timestamps)."""
    owner = str(control.get("lease_owner") or "").strip()
    acquired = str(control.get("lease_acquired_at") or "").strip()
    expires = str(control.get("lease_expires_at") or "").strip()
    return f"{owner}|{acquired}|{expires}"


def evaluate_lease_expiry_stop(
    control: Mapping[str, Any],
    marker: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Pure decision: should the runtime stop a coordinator for this expiry?

    Returns a decision dict only when an elevated mode (supervise/autonomous)
    holds an owner-bearing lease that is no longer active AND we have not
    already recorded a stop for this exact lease identity. Returns ``None`` for
    active leases, observe/manual modes, missing owners, malformed control, or
    leases we have already stopped (idempotent).

    Side-effect-free; callers persist the stop via ``record_lease_expiry_stop``.
    """
    mode = str(control.get("mode") or "").strip().lower()
    if mode not in {"supervise", "autonomous"}:
        return None
    if control.get("lease_ttl_valid") is False or not _coerce_lease_ttl(control.get("lease_ttl_seconds"))[1]:
        return None
    owner = str(control.get("lease_owner") or "").strip()
    if not owner:
        return None
    if _lease_is_active_raw(control):
        return None
    lease_key = _lease_identity(control)
    prior_key = str((marker or {}).get("stopped_lease_key") or "")
    if prior_key == lease_key:
        return None
    return {
        "lease_key": lease_key,
        "reason": "expired_lease",
        "mode": mode,
        "lease_owner": owner,
        "prior_key": prior_key,
    }


def load_lease_expiry_stop(project_dir: Path) -> dict[str, Any]:
    """Read the persisted lease-expiry stop marker (empty when never stopped)."""
    paths = runtime_paths(project_dir)
    return _read_json(paths["dir"] / LEASE_EXPIRY_STOP_FILENAME)


def reserve_lease_expiry_stop(
    project_dir: Path,
    decision: Mapping[str, Any],
) -> dict[str, Any]:
    """Reserve an expiry stop before invoking the external coordinator command."""
    paths = runtime_paths(project_dir)
    repo_dir = paths["wg_dir"].parent.resolve()
    now_iso = _iso_now()
    record: dict[str, Any] = {
        "repo": repo_dir.name,
        "stopped_lease_key": str(decision.get("lease_key") or ""),
        "reserved_at": now_iso,
        "stopped_at": "",
        "stop_state": "stopping",
        "reconciled": False,
        "mode": str(decision.get("mode") or ""),
        "reason": str(decision.get("reason") or "expired_lease"),
        "lease_owner": str(decision.get("lease_owner") or ""),
        "prior_key": str(decision.get("prior_key") or ""),
        "stop_exit_code": None,
        "stop_stdout": "",
        "stop_stderr": "",
    }
    _write_json(paths["dir"] / LEASE_EXPIRY_STOP_FILENAME, record)
    return record


def record_lease_expiry_stop(
    project_dir: Path,
    decision: Mapping[str, Any],
    *,
    stop_result: Mapping[str, Any] | None = None,
    reconciled: bool = False,
) -> dict[str, Any]:
    """Persist terminal evidence that a coordinator was stopped on expiry.

    Writes a marker keyed by the expired lease identity (so repeated cycles do
    not duplicate the stop) and appends a terminal event to the runtime events
    log. Does NOT acquire a new lease or alter the control mode.
    """
    paths = runtime_paths(project_dir)
    repo_dir = paths["wg_dir"].parent.resolve()
    repo_name = repo_dir.name
    now_iso = _iso_now()
    stop = dict(stop_result or {})
    record: dict[str, Any] = {
        "repo": repo_name,
        "stopped_lease_key": str(decision.get("lease_key") or ""),
        "stopped_at": now_iso,
        "stop_state": "stopped" if stop.get("exit_code") == 0 else "failed",
        "reconciled": bool(reconciled),
        "mode": str(decision.get("mode") or ""),
        "reason": str(decision.get("reason") or "expired_lease"),
        "lease_owner": str(decision.get("lease_owner") or ""),
        "prior_key": str(decision.get("prior_key") or ""),
        "stop_exit_code": stop.get("exit_code"),
        "stop_stdout": str(stop.get("stdout") or "")[:500],
        "stop_stderr": str(stop.get("stderr") or "")[:500],
    }
    stop_succeeded = stop.get("exit_code") == 0
    marker_path = paths["dir"] / LEASE_EXPIRY_STOP_FILENAME
    if stop_succeeded:
        _write_json(marker_path, record)
    else:
        try:
            marker_path.unlink()
        except FileNotFoundError:
            pass
    event_date = now_iso[:10] or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _append_jsonl(
        paths["events_dir"] / f"{event_date}.jsonl",
        {
            "event_id": f"evt_{uuid.uuid4().hex}",
            "ts": now_iso,
            "repo": repo_name,
            "event_type": (
                "coordinator_stopped_on_lease_expiry"
                if stop_succeeded
                else "coordinator_stop_failed_on_lease_expiry"
            ),
            "state": "expired" if stop_succeeded else "stop_failed",
            "worker_id": "",
            "task_id": "",
            "runtime": "",
            "cycle_id": "",
            "payload": {
                "stopped_lease_key": record["stopped_lease_key"],
                "mode": record["mode"],
                "lease_owner": record["lease_owner"],
                "stop_exit_code": record["stop_exit_code"],
                "stop_stdout": record["stop_stdout"],
                "stop_stderr": record["stop_stderr"],
                "stop_succeeded": stop_succeeded,
                "reconciled": bool(reconciled),
            },
        },
    )
    return record


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
    # Runtime snapshots are observational. Control state is authoritative and
    # may be changed independently between snapshot collection and persistence;
    # never write the snapshot's stale control copy back to control.json.
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
