# ABOUTME: Tests for attractor loading, inheritance resolution, and criteria evaluation.
# ABOUTME: Covers TOML parsing, extends chain, and gap detection against lane findings.

from __future__ import annotations

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.attractors import (
    AttractorCriterion,
    Attractor,
    load_attractor,
    load_attractors_from_dir,
    resolve_attractor,
    evaluate_attractor,
)
from driftdriver.lane_contract import LaneFinding, LaneResult


def test_attractor_criterion_fields():
    c = AttractorCriterion(lane="coredrift", max_actionable_findings=0)
    assert c.lane == "coredrift"
    assert c.max_actionable_findings == 0


def test_attractor_fields():
    a = Attractor(
        id="test",
        description="A test attractor",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    assert a.id == "test"
    assert len(a.criteria) == 1


def test_load_attractor_from_toml():
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "test.toml"
        p.write_text("""
[attractor]
id = "test"
description = "Test attractor"

[[criteria]]
lane = "coredrift"
max_actionable_findings = 0

[[criteria]]
lane = "plandrift"
max_actionable_findings = 0
require = ["test-gates"]
""")
        a = load_attractor(p)
        assert a.id == "test"
        assert len(a.criteria) == 2
        assert a.criteria[1].require == ["test-gates"]


def test_load_attractor_with_extends():
    with TemporaryDirectory() as tmp:
        p = Path(tmp) / "child.toml"
        p.write_text("""
[attractor]
id = "child"
extends = "parent"
description = "Child attractor"

[[criteria]]
lane = "secdrift"
max_actionable_findings = 0
""")
        a = load_attractor(p)
        assert a.extends == "parent"


def test_resolve_attractor_inheritance():
    parent = Attractor(
        id="parent",
        description="Parent",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    child = Attractor(
        id="child",
        description="Child",
        extends="parent",
        criteria=[AttractorCriterion(lane="secdrift", max_actionable_findings=0)],
    )
    registry = {"parent": parent, "child": child}
    resolved = resolve_attractor("child", registry)
    assert len(resolved.criteria) == 2  # parent's coredrift + child's secdrift
    lanes = {c.lane for c in resolved.criteria}
    assert lanes == {"coredrift", "secdrift"}


def test_resolve_attractor_no_extends():
    a = Attractor(id="standalone", description="No parent", criteria=[])
    registry = {"standalone": a}
    resolved = resolve_attractor("standalone", registry)
    assert resolved.id == "standalone"


def test_evaluate_attractor_all_met():
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[
            AttractorCriterion(lane="coredrift", max_actionable_findings=0),
            AttractorCriterion(lane="specdrift", max_actionable_findings=0),
        ],
    )
    lane_results = {
        "coredrift": LaneResult(lane="coredrift", findings=[], exit_code=0, summary="clean"),
        "specdrift": LaneResult(lane="specdrift", findings=[], exit_code=0, summary="clean"),
    }
    gap = evaluate_attractor(attractor, lane_results)
    assert gap.converged is True
    assert gap.unmet_criteria == []


def test_evaluate_attractor_findings_exceed_threshold():
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[
            AttractorCriterion(lane="coredrift", max_actionable_findings=0),
        ],
    )
    lane_results = {
        "coredrift": LaneResult(
            lane="coredrift",
            findings=[LaneFinding(message="scope drift", severity="warning")],
            exit_code=3,
            summary="1 finding",
        ),
    }
    gap = evaluate_attractor(attractor, lane_results)
    assert gap.converged is False
    assert len(gap.unmet_criteria) == 1
    assert gap.unmet_criteria[0].lane == "coredrift"
    assert gap.actionable_finding_count == 1


def test_evaluate_attractor_info_not_actionable():
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[
            AttractorCriterion(lane="coredrift", max_actionable_findings=0),
        ],
    )
    lane_results = {
        "coredrift": LaneResult(
            lane="coredrift",
            findings=[LaneFinding(message="minor note", severity="info")],
            exit_code=0,
            summary="1 info",
        ),
    }
    gap = evaluate_attractor(attractor, lane_results)
    assert gap.converged is True  # info is not actionable
