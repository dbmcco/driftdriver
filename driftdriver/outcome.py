# ABOUTME: Outcome feedback schema and JSONL ledger for tracking drift recommendation results.
# ABOUTME: Records what driftdriver recommended vs what actually happened after agent action.

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

OUTCOME_VALUES = ("resolved", "ignored", "worsened", "deferred")


@dataclass
class DriftOutcome:
    task_id: str
    lane: str
    finding_key: str
    recommendation: str
    action_taken: str
    outcome: str
    evidence: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    actor_id: str = ""
    bundle_id: str = ""

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOME_VALUES:
            raise ValueError(
                f"invalid outcome {self.outcome!r}; must be one of {OUTCOME_VALUES}"
            )

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "lane": self.lane,
            "finding_key": self.finding_key,
            "recommendation": self.recommendation,
            "action_taken": self.action_taken,
            "outcome": self.outcome,
            "evidence": self.evidence,
            "timestamp": self.timestamp.isoformat(),
            "actor_id": self.actor_id,
            "bundle_id": self.bundle_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DriftOutcome:
        data = dict(data)
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
        data.setdefault("actor_id", "")
        data.setdefault("bundle_id", "")
        return cls(**data)


def write_outcome(path: Path, outcome: DriftOutcome) -> None:
    """Append a single DriftOutcome as a JSON line to the given file."""
    with open(path, "a") as f:
        f.write(json.dumps(outcome.to_dict()) + "\n")


def read_outcomes(path: Path) -> list[DriftOutcome]:
    """Read all DriftOutcome records from a JSONL file."""
    if not path.exists():
        return []
    results: list[DriftOutcome] = []
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            results.append(DriftOutcome.from_dict(json.loads(stripped)))
    return results


def query_outcomes(
    path: Path,
    lane: str | None = None,
    task_id: str | None = None,
) -> list[DriftOutcome]:
    """Read outcomes filtered by lane and/or task_id."""
    outcomes = read_outcomes(path)
    if lane is not None:
        outcomes = [o for o in outcomes if o.lane == lane]
    if task_id is not None:
        outcomes = [o for o in outcomes if o.task_id == task_id]
    return outcomes


def outcome_rates(
    path: Path,
    lane: str | None = None,
) -> dict[str, float]:
    """Return the rate (0.0-1.0) of each outcome value for the given lane or all lanes."""
    outcomes = query_outcomes(path, lane=lane)
    total = len(outcomes)
    if total == 0:
        return {v: 0.0 for v in OUTCOME_VALUES}
    counts = {v: 0 for v in OUTCOME_VALUES}
    for o in outcomes:
        counts[o.outcome] += 1
    return {v: counts[v] / total for v in OUTCOME_VALUES}
