# ABOUTME: Tests for project profile generation from Lessons MCP event data
# ABOUTME: Covers lane stats, failure patterns, validation rate, and report formatting

from driftdriver.project_profiles import (
    LaneStats,
    ProjectProfile,
    build_profile,
    extract_lane_stats,
    extract_failure_patterns,
    format_profile_report,
)


def _make_events():
    """Return a representative set of mixed event dicts."""
    return [
        # decision events → total_tasks
        {"type": "decision", "project": "myproj", "task_id": "t1", "status": "completed"},
        {"type": "decision", "project": "myproj", "task_id": "t2", "status": "completed"},
        {"type": "decision", "project": "myproj", "task_id": "t3", "status": "pending"},
        # drift check events → lane stats
        {"type": "drift_check", "lane": "spec", "drift_detected": False, "score": 0.9},
        {"type": "drift_check", "lane": "spec", "drift_detected": True,  "score": 0.4},
        {"type": "drift_check", "lane": "ux",   "drift_detected": False, "score": 0.8},
        # validation events → validation_rate
        {"type": "validation", "task_id": "t1"},
        {"type": "validation", "task_id": "t2"},
        # error events → failure patterns (first two share a 50-char prefix)
        {"type": "error", "message": "FileNotFoundError: config.yaml not found in path /etc/config.yaml"},
        {"type": "error", "message": "FileNotFoundError: config.yaml not found in path /home/config.yaml"},
        {"type": "error", "message": "TimeoutError: agent did not respond in time"},
    ]


# ---------------------------------------------------------------------------
# test_build_profile_empty_events
# ---------------------------------------------------------------------------

def test_build_profile_empty_events():
    profile = build_profile("empty", [])
    assert profile.project == "empty"
    assert profile.total_tasks == 0
    assert profile.completed_tasks == 0
    assert profile.lane_stats == []
    assert profile.common_failures == []
    assert profile.validation_rate == 0.0


# ---------------------------------------------------------------------------
# test_extract_lane_stats_groups_by_lane
# ---------------------------------------------------------------------------

def test_extract_lane_stats_groups_by_lane():
    events = _make_events()
    stats = extract_lane_stats(events)

    by_lane = {s.lane: s for s in stats}
    assert "spec" in by_lane
    assert "ux" in by_lane

    spec = by_lane["spec"]
    assert spec.runs == 2
    assert spec.drift_detected == 1
    assert abs(spec.avg_score - 0.65) < 1e-9  # (0.9 + 0.4) / 2

    ux = by_lane["ux"]
    assert ux.runs == 1
    assert ux.drift_detected == 0
    assert abs(ux.avg_score - 0.8) < 1e-9


# ---------------------------------------------------------------------------
# test_extract_failure_patterns_returns_top_n
# ---------------------------------------------------------------------------

def test_extract_failure_patterns_returns_top_n():
    events = _make_events()
    # top 1 should be FileNotFoundError pattern (2 occurrences vs 1)
    patterns = extract_failure_patterns(events, top_n=1)
    assert len(patterns) == 1
    assert "FileNotFoundError" in patterns[0]


def test_extract_failure_patterns_top_n_limits_results():
    events = _make_events()
    patterns = extract_failure_patterns(events, top_n=5)
    # only 2 distinct prefixes exist in events
    assert len(patterns) == 2


def test_extract_failure_patterns_empty():
    patterns = extract_failure_patterns([], top_n=5)
    assert patterns == []


# ---------------------------------------------------------------------------
# test_validation_rate_calculation
# ---------------------------------------------------------------------------

def test_validation_rate_calculation():
    events = _make_events()
    profile = build_profile("myproj", events)
    # 3 decision events (total tasks), 2 validation events
    assert profile.total_tasks == 3
    assert abs(profile.validation_rate - (2 / 3)) < 1e-9


def test_validation_rate_zero_when_no_tasks():
    profile = build_profile("x", [])
    assert profile.validation_rate == 0.0


# ---------------------------------------------------------------------------
# test_format_profile_report_includes_all_sections
# ---------------------------------------------------------------------------

def test_format_profile_report_includes_all_sections():
    events = _make_events()
    profile = build_profile("myproj", events)
    report = format_profile_report(profile)

    assert "myproj" in report
    assert "spec" in report
    assert "ux" in report
    assert "FileNotFoundError" in report
    # validation rate present (formatted as %)
    assert "%" in report
