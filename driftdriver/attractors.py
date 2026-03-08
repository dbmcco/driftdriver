# ABOUTME: Attractor loader with inheritance resolution and criteria evaluation.
# ABOUTME: Defines target states repos converge toward via the attractor loop.

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from driftdriver.lane_contract import LaneResult

ACTIONABLE_SEVERITIES = {"warning", "error", "critical"}


@dataclass
class AttractorCriterion:
    """A single criterion that must be met for an attractor to be satisfied."""

    lane: str = ""
    custom: str = ""
    max_actionable_findings: int = 0
    require: list[str] = field(default_factory=list)
    threshold: float = 0.0


@dataclass
class Attractor:
    """A target state for a repo."""

    id: str
    description: str
    extends: str = ""
    criteria: list[AttractorCriterion] = field(default_factory=list)


@dataclass
class AttractorGap:
    """Result of evaluating an attractor against current state."""

    converged: bool
    unmet_criteria: list[AttractorCriterion]
    actionable_finding_count: int = 0


def load_attractor(path: Path) -> Attractor:
    """Load an attractor from a TOML file."""
    with open(path, "rb") as f:
        data = tomllib.load(f)

    a = data.get("attractor", {})
    if "id" not in a:
        raise ValueError(f"Attractor at {path} missing 'id'")

    criteria = []
    for c in data.get("criteria", []):
        criteria.append(AttractorCriterion(
            lane=c.get("lane", ""),
            custom=c.get("custom", ""),
            max_actionable_findings=c.get("max_actionable_findings", 0),
            require=c.get("require", []),
            threshold=c.get("threshold", 0.0),
        ))

    return Attractor(
        id=a["id"],
        description=a.get("description", ""),
        extends=a.get("extends", ""),
        criteria=criteria,
    )


def load_attractors_from_dir(directory: Path) -> dict[str, Attractor]:
    """Load all attractors from a directory, keyed by ID."""
    registry: dict[str, Attractor] = {}
    for p in sorted(directory.glob("*.toml")):
        a = load_attractor(p)
        registry[a.id] = a
    return registry


def resolve_attractor(attractor_id: str, registry: dict[str, Attractor]) -> Attractor:
    """Resolve inheritance chain and return a fully-merged attractor."""
    if attractor_id not in registry:
        raise ValueError(f"Unknown attractor: {attractor_id}")

    attractor = registry[attractor_id]
    if not attractor.extends:
        return attractor

    parent = resolve_attractor(attractor.extends, registry)
    merged_criteria = list(parent.criteria) + list(attractor.criteria)

    return Attractor(
        id=attractor.id,
        description=attractor.description,
        criteria=merged_criteria,
    )


def _count_actionable(result: LaneResult) -> int:
    """Count findings with actionable severity."""
    return sum(1 for f in result.findings if f.severity in ACTIONABLE_SEVERITIES)


def evaluate_attractor(
    attractor: Attractor,
    lane_results: dict[str, LaneResult],
) -> AttractorGap:
    """Evaluate whether an attractor's criteria are met by current lane results."""
    unmet: list[AttractorCriterion] = []
    total_actionable = 0

    for criterion in attractor.criteria:
        if criterion.lane:
            result = lane_results.get(criterion.lane)
            if result is None:
                unmet.append(criterion)
                continue
            actionable = _count_actionable(result)
            total_actionable += actionable
            if actionable > criterion.max_actionable_findings:
                unmet.append(criterion)

    return AttractorGap(
        converged=len(unmet) == 0,
        unmet_criteria=unmet,
        actionable_finding_count=total_actionable,
    )
