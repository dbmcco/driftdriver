# ABOUTME: Tests for northstardrift governance updates — axis rename, Op. Health, lifecycle filter.
# ABOUTME: Validates product_quality replaces quality, operational_health added, active-only overall score.
from __future__ import annotations

import pytest

from driftdriver.northstardrift import (
    AXIS_NAMES,
    AXIS_WEIGHTS,
    compute_northstardrift,
    _default_targets_cfg,
)


# --- Axis constants ---


def test_axes_include_product_quality_not_quality():
    assert "product_quality" in AXIS_NAMES
    assert "quality" not in AXIS_NAMES


def test_axes_include_operational_health():
    assert "operational_health" in AXIS_NAMES


def test_axis_count_is_six():
    assert len(AXIS_NAMES) == 6


def test_axis_weights_sum_to_one():
    total = sum(AXIS_WEIGHTS.values())
    assert abs(total - 1.0) < 0.001


def test_axis_weights_cover_all_axes():
    for name in AXIS_NAMES:
        assert name in AXIS_WEIGHTS, f"Missing weight for axis {name}"


def test_axis_weights_match_spec():
    assert AXIS_WEIGHTS["continuity"] == pytest.approx(0.22)
    assert AXIS_WEIGHTS["autonomy"] == pytest.approx(0.18)
    assert AXIS_WEIGHTS["product_quality"] == pytest.approx(0.18)
    assert AXIS_WEIGHTS["coordination"] == pytest.approx(0.18)
    assert AXIS_WEIGHTS["self_improvement"] == pytest.approx(0.12)
    assert AXIS_WEIGHTS["operational_health"] == pytest.approx(0.12)


# --- Default targets ---


def test_default_targets_use_product_quality_not_quality():
    targets = _default_targets_cfg()
    axes = targets["axes"]
    assert "product_quality" in axes
    assert "quality" not in axes
    assert "operational_health" in axes


def test_operational_health_target_is_75():
    targets = _default_targets_cfg()
    assert targets["axes"]["operational_health"] == 75.0


# --- Compute output structure ---


def _repo(name, *, lifecycle="active", **kwargs):
    """Minimal repo fixture for northstardrift computation."""
    defaults = {
        "name": name,
        "exists": True,
        "workgraph_exists": True,
        "reporting": True,
        "heartbeat_age_seconds": 120,
        "service_running": True,
        "errors": [],
        "blocked_open": 0,
        "missing_dependencies": 0,
        "stale_open": [],
        "stale_in_progress": [],
        "stalled": False,
        "stall_reasons": [],
        "git_dirty": False,
        "dirty_file_count": 0,
        "behind": 0,
        "in_progress": [{"id": f"{name}-t1", "title": "work"}],
        "ready": [],
        "task_counts": {"open": 0, "ready": 0, "in-progress": 1, "done": 1},
        "security": {"critical": 0, "high": 0, "medium": 0, "low": 0, "findings_total": 0, "at_risk": False},
        "quality": {"critical": 0, "high": 0, "medium": 0, "low": 0, "findings_total": 0, "quality_score": 95, "at_risk": False},
        "repo_north_star": {"present": True, "status": "present", "canonical": True, "approved": True},
        "lifecycle": lifecycle,
    }
    defaults.update(kwargs)
    return defaults


def _snapshot(repos, *, op_health_inputs=None):
    overview = {
        "tasks_ready": 0,
        "tasks_in_progress": len(repos),
        "blocked_open": 0,
        "stale_open": 0,
        "stale_in_progress": 0,
        "repos_stalled": 0,
        "repos_dirty": 0,
        "missing_dependencies": 0,
        "total_behind": 0,
    }
    if op_health_inputs is not None:
        overview["op_health_inputs"] = op_health_inputs
    return {
        "generated_at": "2026-03-19T12:00:00Z",
        "repos": repos,
        "overview": overview,
        "repo_dependency_overview": {},
        "factory": {},
        "supervisor": {},
        "updates": {},
        "upstream_candidates": [],
    }


def test_compute_output_has_product_quality_axis():
    snap = _snapshot([_repo("alpha")])
    result = compute_northstardrift(snap)
    assert "product_quality" in result["axes"]
    assert "quality" not in result["axes"]


def test_compute_output_has_operational_health_axis():
    snap = _snapshot([_repo("alpha")])
    result = compute_northstardrift(snap)
    assert "operational_health" in result["axes"]
    assert "score" in result["axes"]["operational_health"]


def test_operational_health_uses_snapshot_inputs():
    snap = _snapshot(
        [_repo("alpha")],
        op_health_inputs={
            "zombie_ratio": 0.0,
            "failed_abandoned_ratio": 0.0,
            "posture_alignment_ratio": 1.0,
            "abandoned_age_pressure": 0.0,
        },
    )
    result = compute_northstardrift(snap)
    assert result["axes"]["operational_health"]["score"] == pytest.approx(100.0)


def test_operational_health_degrades_with_bad_inputs():
    snap = _snapshot(
        [_repo("alpha")],
        op_health_inputs={
            "zombie_ratio": 0.8,
            "failed_abandoned_ratio": 0.6,
            "posture_alignment_ratio": 0.3,
            "abandoned_age_pressure": 0.9,
        },
    )
    result = compute_northstardrift(snap)
    assert result["axes"]["operational_health"]["score"] < 40.0


def test_lifecycle_filter_excludes_nonactive_from_overall():
    """Retired/maintenance repos should not drag down the overall score."""
    active_repo = _repo("alpha", lifecycle="active")
    retired_repo = _repo("retired-thing", lifecycle="retired", reporting=False, workgraph_exists=False)

    snap_both = _snapshot([active_repo, retired_repo])
    result_both = compute_northstardrift(snap_both)

    snap_active_only = _snapshot([active_repo])
    result_active = compute_northstardrift(snap_active_only)

    # The active-only count should be 1 in both cases
    assert result_both["counts"]["participating_repos_active"] == 1
    # Overall score should be based on active repos, so adding a retired repo
    # should not significantly change the overall score
    diff = abs(result_both["summary"]["overall_score"] - result_active["summary"]["overall_score"])
    assert diff < 5.0, f"Retired repo caused {diff:.1f} point swing in overall score"


def test_all_six_axes_in_output():
    snap = _snapshot([_repo("alpha")])
    result = compute_northstardrift(snap)
    for name in AXIS_NAMES:
        assert name in result["axes"], f"Missing axis {name} in output"
        assert "score" in result["axes"][name]
        assert "tier" in result["axes"][name]


def test_targets_include_all_six_axes():
    snap = _snapshot([_repo("alpha")])
    result = compute_northstardrift(snap)
    for name in AXIS_NAMES:
        assert name in result["targets"]["axes"], f"Missing target for axis {name}"
