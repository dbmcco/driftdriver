# ABOUTME: Append-only JSONL ledger tracking actor operations for budget enforcement.
# ABOUTME: Provides recent_count queries for hourly rate limiting in the authority system.

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class BudgetEntry:
    actor_id: str
    actor_class: str
    operation: str  # "create" or "dispatch"
    repo: str
    timestamp: str  # ISO 8601
    detail: str = ""  # e.g., task_id created, lane_tag


def record_operation(
    ledger_path: Path,
    actor_id: str,
    actor_class: str,
    operation: str,
    repo: str = "",
    detail: str = "",
) -> BudgetEntry:
    """Append an operation to the budget ledger."""
    entry = BudgetEntry(
        actor_id=actor_id,
        actor_class=actor_class,
        operation=operation,
        repo=repo,
        timestamp=datetime.now(timezone.utc).isoformat(),
        detail=detail,
    )
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    with open(ledger_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry)) + "\n")
    return entry


def _parse_ledger(ledger_path: Path) -> list[dict[str, str]]:
    """Read all entries from the ledger, skipping malformed lines."""
    if not ledger_path.exists():
        return []
    entries: list[dict[str, str]] = []
    with open(ledger_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except (json.JSONDecodeError, TypeError):
                continue
    return entries


def recent_count(
    ledger_path: Path,
    actor_id: str,
    operation: str,
    window_seconds: int = 3600,
) -> int:
    """Count operations by actor_id of given type within the time window."""
    entries = _parse_ledger(ledger_path)
    cutoff = datetime.now(timezone.utc).timestamp() - window_seconds
    count = 0
    for entry in entries:
        if entry.get("actor_id") != actor_id:
            continue
        if entry.get("operation") != operation:
            continue
        ts_str = entry.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str).timestamp()
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            count += 1
    return count


def recent_count_by_class(
    ledger_path: Path,
    actor_class: str,
    operation: str,
    window_seconds: int = 3600,
) -> int:
    """Count operations by actor_class of given type within the time window.

    For class-level budgets — aggregates across all actors of the same class.
    """
    entries = _parse_ledger(ledger_path)
    cutoff = datetime.now(timezone.utc).timestamp() - window_seconds
    count = 0
    for entry in entries:
        if entry.get("actor_class") != actor_class:
            continue
        if entry.get("operation") != operation:
            continue
        ts_str = entry.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str).timestamp()
        except (ValueError, TypeError):
            continue
        if ts >= cutoff:
            count += 1
    return count
