# ABOUTME: Centralized decision queue for pending human decisions.
# ABOUTME: JSONL-backed CRUD with create, read, answer, and filtering.
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class DecisionRecord:
    id: str
    repo: str
    status: str  # "pending" | "answered" | "expired"
    question: str
    context: dict[str, Any]
    category: str  # "aesthetic" | "feature" | "business" | "external_dep"
    created_at: str
    notified_via: list[str] = field(default_factory=list)
    answered_at: str | None = None
    answered_via: str | None = None
    answer: str | None = None
    resolution_task: str | None = None


def _decisions_path(project_dir: Path) -> Path:
    return project_dir / ".workgraph" / "service" / "runtime" / "decisions.jsonl"


def _generate_id() -> str:
    now = datetime.now(timezone.utc)
    short = uuid.uuid4().hex[:6]
    return f"dec-{now.strftime('%Y%m%d')}-{short}"


def _record_to_dict(rec: DecisionRecord) -> dict[str, Any]:
    return {
        "id": rec.id,
        "repo": rec.repo,
        "status": rec.status,
        "question": rec.question,
        "context": rec.context,
        "category": rec.category,
        "created_at": rec.created_at,
        "notified_via": rec.notified_via,
        "answered_at": rec.answered_at,
        "answered_via": rec.answered_via,
        "answer": rec.answer,
        "resolution_task": rec.resolution_task,
    }


def _dict_to_record(d: dict[str, Any]) -> DecisionRecord:
    return DecisionRecord(
        id=d["id"],
        repo=d.get("repo", ""),
        status=d.get("status", "pending"),
        question=d.get("question", ""),
        context=d.get("context", {}),
        category=d.get("category", ""),
        created_at=d.get("created_at", ""),
        notified_via=d.get("notified_via", []),
        answered_at=d.get("answered_at"),
        answered_via=d.get("answered_via"),
        answer=d.get("answer"),
        resolution_task=d.get("resolution_task"),
    )


def create_decision(
    project_dir: Path,
    *,
    repo: str,
    question: str,
    category: str,
    context: dict[str, Any] | None = None,
) -> DecisionRecord:
    """Create a new pending decision and append to decisions.jsonl."""
    record = DecisionRecord(
        id=_generate_id(),
        repo=repo,
        status="pending",
        question=question,
        context=context or {},
        category=category,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    path = _decisions_path(project_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(_record_to_dict(record)) + "\n")
    return record


def read_decisions(project_dir: Path) -> list[DecisionRecord]:
    """Read all decision records from decisions.jsonl."""
    path = _decisions_path(project_dir)
    if not path.exists():
        return []
    records: list[DecisionRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(_dict_to_record(json.loads(line)))
        except (json.JSONDecodeError, KeyError):
            continue
    return records


def read_pending_decisions(project_dir: Path) -> list[DecisionRecord]:
    """Read only pending decisions."""
    return [d for d in read_decisions(project_dir) if d.status == "pending"]


def decision_age_hours(decision: dict[str, Any], *, now: datetime | None = None) -> float:
    """Return decision age in hours for queue shaping."""
    created_at = str(decision.get("created_at") or "").strip()
    if not created_at:
        return 0.0
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    current = now or datetime.now(timezone.utc)
    return max(0.0, (current - created).total_seconds() / 3600.0)


def classify_gate_bucket(decision: dict[str, Any]) -> str:
    """Classify a pending decision into decide/watch for the operator surface."""
    context = decision.get("context") or {}
    severity = str(context.get("severity") or "medium").lower()
    confidence = float(context.get("confidence") or 0.0)
    if severity == "low" or confidence < 0.5 or decision_age_hours(decision) >= 72.0:
        return "watch"
    return "decide"


def answer_decision(
    project_dir: Path,
    *,
    decision_id: str,
    answer: str,
    answered_via: str,
) -> DecisionRecord | None:
    """Mark a decision as answered. Rewrites the JSONL file with updated record."""
    path = _decisions_path(project_dir)
    if not path.exists():
        return None

    records = read_decisions(project_dir)
    target: DecisionRecord | None = None
    for rec in records:
        if rec.id == decision_id and rec.status == "pending":
            rec.status = "answered"
            rec.answer = answer
            rec.answered_via = answered_via
            rec.answered_at = datetime.now(timezone.utc).isoformat()
            target = rec
            break

    if target is None:
        return None

    # Rewrite file atomically
    tmp = path.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(_record_to_dict(rec)) + "\n")
    tmp.replace(path)
    return target
