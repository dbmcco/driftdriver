# ABOUTME: Presence tracking for the Speedrift authority system.
# ABOUTME: Actors write heartbeat files; the hub reads them for activity detection.

from __future__ import annotations

import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from driftdriver.actor import Actor, actor_from_dict, actor_to_dict


@dataclass
class PresenceRecord:
    actor: Actor
    started_at: str
    last_heartbeat: str
    current_task: str = ""
    status: str = "active"


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _parse_iso(ts: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(ts)


def _record_to_dict(rec: PresenceRecord) -> dict[str, Any]:
    return {
        "actor": actor_to_dict(rec.actor),
        "started_at": rec.started_at,
        "last_heartbeat": rec.last_heartbeat,
        "current_task": rec.current_task,
        "status": rec.status,
    }


def _record_from_dict(d: dict[str, Any]) -> PresenceRecord:
    return PresenceRecord(
        actor=actor_from_dict(d["actor"]),
        started_at=d["started_at"],
        last_heartbeat=d["last_heartbeat"],
        current_task=d.get("current_task", ""),
        status=d.get("status", "active"),
    )


def presence_dir(project_dir: Path) -> Path:
    """Return the presence directory path for a project."""
    return project_dir / ".workgraph" / "presence"


def write_heartbeat(
    project_dir: Path,
    actor: Actor,
    *,
    current_task: str = "",
    status: str = "active",
) -> PresenceRecord:
    """Write or update a heartbeat file for an actor.

    If the file already exists, preserves started_at and updates last_heartbeat.
    If new, sets both started_at and last_heartbeat to now.
    """
    pdir = presence_dir(project_dir)
    pdir.mkdir(parents=True, exist_ok=True)
    fpath = pdir / f"{actor.id}.json"

    now = _iso_now()

    if fpath.exists():
        existing = json.loads(fpath.read_text())
        started_at = existing["started_at"]
    else:
        started_at = now

    rec = PresenceRecord(
        actor=actor,
        started_at=started_at,
        last_heartbeat=now,
        current_task=current_task,
        status=status,
    )
    fpath.write_text(json.dumps(_record_to_dict(rec), indent=2))
    return rec


def read_all_presence(project_dir: Path) -> list[PresenceRecord]:
    """Read all presence files in the project."""
    pdir = presence_dir(project_dir)
    if not pdir.exists():
        return []
    records = []
    for fpath in sorted(pdir.glob("*.json")):
        try:
            data = json.loads(fpath.read_text())
            records.append(_record_from_dict(data))
        except (json.JSONDecodeError, KeyError):
            continue
    return records


def read_presence(project_dir: Path, actor_id: str) -> PresenceRecord | None:
    """Read a single actor's presence record, or None if not found."""
    fpath = presence_dir(project_dir) / f"{actor_id}.json"
    if not fpath.exists():
        return None
    try:
        data = json.loads(fpath.read_text())
        return _record_from_dict(data)
    except (json.JSONDecodeError, KeyError):
        return None


def remove_presence(project_dir: Path, actor_id: str) -> bool:
    """Remove a presence file. Returns True if removed, False if not found."""
    fpath = presence_dir(project_dir) / f"{actor_id}.json"
    if not fpath.exists():
        return False
    fpath.unlink()
    return True


def _is_stale(rec: PresenceRecord, max_age_seconds: int) -> bool:
    """Check if a presence record's last_heartbeat is older than max_age_seconds."""
    hb = _parse_iso(rec.last_heartbeat)
    now = datetime.datetime.now(datetime.timezone.utc)
    age = (now - hb).total_seconds()
    return age > max_age_seconds


def gc_stale_presence(project_dir: Path, max_age_seconds: int = 600) -> int:
    """Remove presence records with last_heartbeat older than max_age_seconds.

    Returns the count of removed records.
    """
    records = read_all_presence(project_dir)
    removed = 0
    for rec in records:
        if _is_stale(rec, max_age_seconds):
            remove_presence(project_dir, rec.actor.id)
            removed += 1
    return removed


def is_repo_active(project_dir: Path, max_age_seconds: int = 600) -> bool:
    """Return True if any non-stale presence record exists."""
    return len(active_actors(project_dir, max_age_seconds)) > 0


def active_actors(project_dir: Path, max_age_seconds: int = 600) -> list[PresenceRecord]:
    """Return all non-stale presence records."""
    records = read_all_presence(project_dir)
    return [r for r in records if not _is_stale(r, max_age_seconds)]
