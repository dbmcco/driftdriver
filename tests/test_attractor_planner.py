# ABOUTME: Tests for the attractor planner — bundle selection, composition, and plan generation.
# ABOUTME: Covers deterministic planning (no model call) and plan structure validation.

from __future__ import annotations

from driftdriver.attractor_planner import (
    ConvergencePlan,
    EscalationRecord,
    select_bundles_for_findings,
    build_convergence_plan,
)
from driftdriver.bundles import Bundle, TaskTemplate, BundleInstance
from driftdriver.attractors import Attractor, AttractorCriterion, AttractorGap
from driftdriver.lane_contract import LaneFinding, LaneResult


def _make_bundle(id: str, finding_kinds: list[str]) -> Bundle:
    return Bundle(
        id=id,
        finding_kinds=finding_kinds,
        description=f"Bundle {id}",
        tasks=[
            TaskTemplate(id_template="{finding_id}-fix", title_template="Fix {task_title}"),
            TaskTemplate(
                id_template="{finding_id}-verify",
                title_template="Verify {task_title}",
                after=["{finding_id}-fix"],
            ),
        ],
    )


def test_select_bundles_exact_match():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    findings = [LaneFinding(message="scope drift", severity="warning", tags=["scope_drift"])]
    selected = select_bundles_for_findings(findings, bundles, lane="coredrift", task_id="task-1")
    assert len(selected) == 1
    assert selected[0].bundle_id == "scope-drift"
    assert selected[0].confidence == "high"


def test_select_bundles_no_match():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    findings = [LaneFinding(message="unknown thing", severity="warning", tags=["unknown"])]
    selected = select_bundles_for_findings(findings, bundles, lane="coredrift", task_id="task-1")
    assert len(selected) == 0


def test_select_bundles_dedup():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    findings = [
        LaneFinding(message="drift 1", severity="warning", tags=["scope_drift"]),
        LaneFinding(message="drift 2", severity="error", tags=["scope_drift"]),
    ]
    selected = select_bundles_for_findings(findings, bundles, lane="coredrift", task_id="task-1")
    # Same bundle shouldn't be selected twice for same task
    assert len(selected) == 1


def test_select_bundles_skips_info():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    findings = [LaneFinding(message="minor", severity="info", tags=["scope_drift"])]
    selected = select_bundles_for_findings(findings, bundles, lane="coredrift", task_id="task-1")
    assert len(selected) == 0


def test_build_convergence_plan():
    bundles = [_make_bundle("scope-drift", ["scope_drift"])]
    lane_results = {
        "coredrift": LaneResult(
            lane="coredrift",
            findings=[LaneFinding(message="scope drift", severity="warning", tags=["scope_drift"])],
            exit_code=3,
            summary="1 finding",
        ),
    }
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    plan = build_convergence_plan(
        attractor=attractor,
        lane_results=lane_results,
        bundles=bundles,
        repo="test-repo",
        pass_number=0,
    )
    assert plan.attractor == "clean"
    assert plan.repo == "test-repo"
    assert len(plan.bundle_instances) == 1
    assert plan.budget_cost == 2  # 2 tasks in the bundle
    assert plan.escalations == []


def test_build_convergence_plan_with_escalation():
    lane_results = {
        "coredrift": LaneResult(
            lane="coredrift",
            findings=[LaneFinding(message="unknown", severity="error", tags=["novel_finding"])],
            exit_code=3,
            summary="1 finding",
        ),
    }
    attractor = Attractor(
        id="clean",
        description="No findings",
        criteria=[AttractorCriterion(lane="coredrift", max_actionable_findings=0)],
    )
    plan = build_convergence_plan(
        attractor=attractor,
        lane_results=lane_results,
        bundles=[],  # no bundles match
        repo="test-repo",
        pass_number=0,
    )
    assert len(plan.bundle_instances) == 0
    assert len(plan.escalations) == 1
    assert plan.escalations[0].reason == "no_matching_bundle"
