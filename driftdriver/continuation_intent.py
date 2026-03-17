# ABOUTME: Continuation intent read/write for repo control state.
# ABOUTME: Tracks whether a repo should continue, park, or wait for human input.
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

VALID_INTENTS = {"continue", "parked", "needs_human"}
VALID_SET_BY = {"agent", "brain", "human"}


@dataclass
class ContinuationIntent:
    intent: str  # "continue" | "parked" | "needs_human"
    reason: str
    set_by: str  # "agent" | "brain" | "human"
    set_at: str  # ISO timestamp
    decision_id: str | None = None


def _control_path(project_dir: Path) -> Path:
    return project_dir / ".workgraph" / "service" / "runtime" / "control.json"


def _read_control(project_dir: Path) -> dict | None:
    path = _control_path(project_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_control(project_dir: Path, control: dict) -> None:
    path = _control_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(control, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_intent(project_dir: Path) -> ContinuationIntent | None:
    """Read continuation intent from control state. Returns None if not set."""
    control = _read_control(project_dir)
    if control is None:
        return None
    raw = control.get("continuation_intent")
    if not isinstance(raw, dict):
        return None
    return ContinuationIntent(
        intent=raw.get("intent", ""),
        reason=raw.get("reason", ""),
        set_by=raw.get("set_by", ""),
        set_at=raw.get("set_at", ""),
        decision_id=raw.get("decision_id"),
    )


def write_intent(
    project_dir: Path,
    *,
    intent: str,
    set_by: str,
    reason: str,
    decision_id: str | None = None,
) -> ContinuationIntent:
    """Write continuation intent to control state."""
    if intent not in VALID_INTENTS:
        raise ValueError(f"Invalid intent: {intent!r}. Must be one of {VALID_INTENTS}")
    if set_by not in VALID_SET_BY:
        raise ValueError(f"Invalid set_by: {set_by!r}. Must be one of {VALID_SET_BY}")

    control = _read_control(project_dir) or {}
    now = datetime.now(timezone.utc).isoformat()
    intent_record = {
        "intent": intent,
        "reason": reason,
        "set_by": set_by,
        "set_at": now,
        "decision_id": decision_id,
    }
    control["continuation_intent"] = intent_record
    _write_control(project_dir, control)

    return ContinuationIntent(
        intent=intent,
        reason=reason,
        set_by=set_by,
        set_at=now,
        decision_id=decision_id,
    )
