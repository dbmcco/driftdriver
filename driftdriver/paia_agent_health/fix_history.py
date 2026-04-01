# driftdriver/paia_agent_health/fix_history.py
# ABOUTME: FixRecord persistence — stores applied and proposed fixes with 7-day outcome tracking.
# ABOUTME: Persists to ~/.config/workgraph/agent_health_fixes.json.

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config" / "workgraph" / "agent_health_fixes.json"


@dataclass
class FixRecord:
    fix_id: str
    applied_at: str           # ISO 8601
    agent: str
    component: str            # e.g. "skills/outreach_templates.md"
    finding_pattern: str      # e.g. "tool_failure"
    change_summary: str
    diff: str
    auto_applied: bool
    check_after: str          # ISO 8601, applied_at + 7 days
    outcome: str | None       # None | "resolved" | "persists" | "unknown"


def load_history(path: Path = DEFAULT_PATH) -> list[FixRecord]:
    """Load all fix records from disk. Returns empty list if file missing."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
        return [FixRecord(**r) for r in data]
    except (json.JSONDecodeError, TypeError, KeyError):
        return []


def save_history(records: list[FixRecord], path: Path = DEFAULT_PATH) -> None:
    """Write all fix records to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([asdict(r) for r in records], indent=2))


def add_fix(path: Path, record: FixRecord) -> None:
    """Append a new fix record. Does not deduplicate."""
    records = load_history(path)
    records.append(record)
    save_history(records, path)


def update_outcome(path: Path, fix_id: str, outcome: str) -> None:
    """Set the outcome field on the record with the given fix_id."""
    records = load_history(path)
    for r in records:
        if r.fix_id == fix_id:
            r.outcome = outcome
    save_history(records, path)


def pending_checks(path: Path = DEFAULT_PATH) -> list[FixRecord]:
    """Return records where check_after has passed and outcome is still None."""
    now = datetime.now(timezone.utc)
    due: list[FixRecord] = []
    for r in load_history(path):
        if r.outcome is not None:
            continue
        try:
            check_dt = datetime.fromisoformat(r.check_after)
            if check_dt.tzinfo is None:
                check_dt = check_dt.replace(tzinfo=timezone.utc)
            if now >= check_dt:
                due.append(r)
        except ValueError:
            continue
    return due


def is_duplicate_pending(
    path: Path,
    agent: str,
    component: str,
    pattern: str,
    max_age_hours: int = 48,
) -> bool:
    """True if a pending fix for (agent, component, pattern) exists within max_age_hours."""
    now = datetime.now(timezone.utc)
    for r in load_history(path):
        if r.outcome is not None:
            continue
        if r.agent != agent or r.component != component or r.finding_pattern != pattern:
            continue
        try:
            applied = datetime.fromisoformat(r.applied_at)
            if applied.tzinfo is None:
                applied = applied.replace(tzinfo=timezone.utc)
            age_hours = (now - applied).total_seconds() / 3600
            if age_hours < max_age_hours:
                return True
        except ValueError:
            continue
    return False
