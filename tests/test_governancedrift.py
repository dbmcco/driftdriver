# ABOUTME: Tests for governancedrift pipe — conformance delta, finding classification, Operational Health scoring.
# ABOUTME: TDD: these tests are written before the implementation.
import pytest
from pathlib import Path

from driftdriver.governancedrift import (
    build_model_prompt,
    classify_finding,
    collect_ecosystem_governance,
    compute_conformance_delta,
    parse_model_response,
    score_operational_health,
    route_remediation,
    check_daemon_socket_live,
    observe_repo,
    FindingCategory,
)


# --- classify_finding ---


def test_retired_repo_with_live_daemon_is_lifecycle_violation():
    finding = classify_finding(
        repo="news-briefing",
        lifecycle="retired",
        daemon_posture="never",
        daemon_socket_live=True,
        live_agent_count=0,
        tasks_ready=0,
        north_star_present=False,
    )
    assert finding["category"] == FindingCategory.LIFECYCLE_VIOLATION
    assert finding["severity"] == "high"


def test_experimental_repo_with_live_daemon_is_lifecycle_violation():
    finding = classify_finding(
        repo="run3",
        lifecycle="experimental",
        daemon_posture="never",
        daemon_socket_live=True,
        live_agent_count=2,
        tasks_ready=1,
        north_star_present=False,
    )
    assert finding["category"] == FindingCategory.LIFECYCLE_VIOLATION
    assert finding["severity"] == "high"


def test_active_repo_with_zombie_agents_is_process_debt():
    finding = classify_finding(
        repo="paia-program",
        lifecycle="active",
        daemon_posture="always-on",
        daemon_socket_live=True,
        live_agent_count=30,
        tasks_ready=0,
        north_star_present=True,
    )
    assert finding["category"] == FindingCategory.PROCESS_DEBT
    assert finding["severity"] == "high"


def test_clean_active_repo_produces_no_finding():
    finding = classify_finding(
        repo="lodestar",
        lifecycle="active",
        daemon_posture="always-on",
        daemon_socket_live=True,
        live_agent_count=2,
        tasks_ready=3,
        north_star_present=True,
    )
    assert finding is None


def test_active_repo_missing_north_star_is_architecture_gap():
    finding = classify_finding(
        repo="garmin-connect-sync",
        lifecycle="active",
        daemon_posture="always-on",
        daemon_socket_live=False,
        live_agent_count=0,
        tasks_ready=0,
        north_star_present=False,
    )
    assert finding["category"] == FindingCategory.ARCHITECTURE_GAP


def test_posture_mismatch_never_but_live():
    finding = classify_finding(
        repo="stable-repo",
        lifecycle="maintenance",
        daemon_posture="never",
        daemon_socket_live=True,
        live_agent_count=0,
        tasks_ready=0,
        north_star_present=True,
    )
    assert finding["category"] == FindingCategory.POSTURE_MISMATCH
    assert finding["severity"] == "medium"


def test_maintenance_repo_no_north_star_not_architecture_gap():
    """Architecture gap only applies to active repos."""
    finding = classify_finding(
        repo="lessons-mcp",
        lifecycle="maintenance",
        daemon_posture="on-demand",
        daemon_socket_live=False,
        live_agent_count=0,
        tasks_ready=0,
        north_star_present=False,
    )
    assert finding is None


def test_active_repo_zero_agents_zero_tasks_no_process_debt():
    """No agents running with no tasks ready is not process debt."""
    finding = classify_finding(
        repo="idle-repo",
        lifecycle="active",
        daemon_posture="always-on",
        daemon_socket_live=False,
        live_agent_count=0,
        tasks_ready=0,
        north_star_present=True,
    )
    assert finding is None


def test_finding_includes_repo_name():
    finding = classify_finding(
        repo="my-repo",
        lifecycle="retired",
        daemon_posture="never",
        daemon_socket_live=True,
        live_agent_count=0,
        tasks_ready=0,
        north_star_present=False,
    )
    assert finding["repo"] == "my-repo"


# --- compute_conformance_delta ---


def test_compute_conformance_delta_detects_retired_with_live_daemon():
    repos = [
        {
            "name": "news-briefing",
            "lifecycle": "retired",
            "daemon_posture": "never",
            "daemon_socket_live": True,
            "live_agent_count": 0,
            "tasks_ready": 0,
            "north_star_present": False,
        }
    ]
    findings = compute_conformance_delta(repos)
    assert len(findings) == 1
    assert findings[0]["repo"] == "news-briefing"
    assert findings[0]["category"] == FindingCategory.LIFECYCLE_VIOLATION


def test_compute_conformance_delta_skips_clean_repos():
    repos = [
        {
            "name": "lodestar",
            "lifecycle": "active",
            "daemon_posture": "always-on",
            "daemon_socket_live": True,
            "live_agent_count": 2,
            "tasks_ready": 3,
            "north_star_present": True,
        }
    ]
    findings = compute_conformance_delta(repos)
    assert len(findings) == 0


def test_compute_conformance_delta_experimental_no_daemon_clean():
    repos = [
        {
            "name": "speedrift-ecosystem-v2-run3",
            "lifecycle": "experimental",
            "daemon_posture": "never",
            "daemon_socket_live": False,
            "live_agent_count": 0,
            "tasks_ready": 0,
            "north_star_present": False,
        }
    ]
    findings = compute_conformance_delta(repos)
    assert len(findings) == 0


def test_compute_conformance_delta_multiple_repos():
    repos = [
        {
            "name": "retired-one",
            "lifecycle": "retired",
            "daemon_posture": "never",
            "daemon_socket_live": True,
            "live_agent_count": 0,
            "tasks_ready": 0,
            "north_star_present": False,
        },
        {
            "name": "clean-active",
            "lifecycle": "active",
            "daemon_posture": "always-on",
            "daemon_socket_live": True,
            "live_agent_count": 1,
            "tasks_ready": 2,
            "north_star_present": True,
        },
        {
            "name": "zombie-active",
            "lifecycle": "active",
            "daemon_posture": "always-on",
            "daemon_socket_live": True,
            "live_agent_count": 10,
            "tasks_ready": 0,
            "north_star_present": True,
        },
    ]
    findings = compute_conformance_delta(repos)
    assert len(findings) == 2
    categories = {f["repo"]: f["category"] for f in findings}
    assert categories["retired-one"] == FindingCategory.LIFECYCLE_VIOLATION
    assert categories["zombie-active"] == FindingCategory.PROCESS_DEBT


# --- score_operational_health ---


def test_score_operational_health_clean_ecosystem():
    score = score_operational_health(
        zombie_ratio=0.0,
        failed_abandoned_ratio=0.0,
        posture_alignment_ratio=1.0,
        abandoned_age_pressure=0.0,
    )
    assert score == pytest.approx(100.0)


def test_score_operational_health_bad_ecosystem():
    score = score_operational_health(
        zombie_ratio=0.8,
        failed_abandoned_ratio=0.6,
        posture_alignment_ratio=0.3,
        abandoned_age_pressure=0.9,
    )
    assert score < 40.0


def test_score_operational_health_mid_range():
    score = score_operational_health(
        zombie_ratio=0.2,
        failed_abandoned_ratio=0.1,
        posture_alignment_ratio=0.8,
        abandoned_age_pressure=0.1,
    )
    assert 70.0 < score < 95.0


def test_score_operational_health_clamped_to_0_100():
    score_low = score_operational_health(
        zombie_ratio=1.5,
        failed_abandoned_ratio=1.5,
        posture_alignment_ratio=0.0,
        abandoned_age_pressure=1.5,
    )
    assert score_low >= 0.0

    score_high = score_operational_health(
        zombie_ratio=0.0,
        failed_abandoned_ratio=0.0,
        posture_alignment_ratio=1.0,
        abandoned_age_pressure=0.0,
    )
    assert score_high <= 100.0


# --- route_remediation ---


def test_high_confidence_finding_routes_to_task():
    result = route_remediation(confidence=0.90, finding_category=FindingCategory.LIFECYCLE_VIOLATION)
    assert result == "workgraph_task"


def test_low_confidence_finding_routes_to_inbox():
    result = route_remediation(confidence=0.70, finding_category=FindingCategory.ARCHITECTURE_GAP)
    assert result == "inbox_signal"


def test_boundary_confidence_routes_correctly():
    assert route_remediation(confidence=0.85, finding_category=FindingCategory.PROCESS_DEBT) == "workgraph_task"
    assert route_remediation(confidence=0.84, finding_category=FindingCategory.PROCESS_DEBT) == "inbox_signal"


# --- check_daemon_socket_live ---


def test_check_daemon_socket_live_no_socket_file(tmp_path):
    assert check_daemon_socket_live(tmp_path) is False


def test_check_daemon_socket_live_socket_file_exists_but_no_listener(tmp_path):
    sock_dir = tmp_path / ".workgraph" / "service"
    sock_dir.mkdir(parents=True)
    sock_path = sock_dir / "daemon.sock"
    sock_path.touch()
    assert check_daemon_socket_live(tmp_path) is False


# --- observe_repo ---


def test_observe_repo_nonexistent_path(tmp_path):
    fake_path = tmp_path / "nonexistent"
    result = observe_repo(fake_path)
    assert result["daemon_socket_live"] is False
    assert result["live_agent_count"] == 0
    assert result["tasks_ready"] == 0
    assert result["north_star_present"] is False


def test_observe_repo_with_north_star(tmp_path):
    (tmp_path / "NORTH_STAR.md").write_text("# North Star\n")
    result = observe_repo(tmp_path)
    assert result["north_star_present"] is True


def test_observe_repo_with_north_star_in_docs(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "NORTH_STAR.md").write_text("# North Star\n")
    result = observe_repo(tmp_path)
    assert result["north_star_present"] is True


def test_observe_repo_with_graph_tasks(tmp_path):
    import json
    wg = tmp_path / ".workgraph"
    wg.mkdir()
    lines = [
        json.dumps({"id": "t1", "status": "open"}),
        json.dumps({"id": "t2", "status": "done"}),
        json.dumps({"id": "t3", "status": "failed"}),
        json.dumps({"id": "t4", "status": "abandoned"}),
    ]
    (wg / "graph.jsonl").write_text("\n".join(lines))
    result = observe_repo(tmp_path)
    assert result["tasks_ready"] == 1
    assert result["tasks_failed"] == 1
    assert result["tasks_abandoned"] == 1
    assert result["tasks_total"] == 4


# --- build_model_prompt ---


def test_build_model_prompt_includes_required_fields():
    finding = {
        "repo": "paia-program",
        "category": FindingCategory.PROCESS_DEBT,
        "severity": "high",
        "declared": "lifecycle=active",
        "observed": "live_agents=30, tasks_ready=0",
    }
    prompt = build_model_prompt(finding)
    assert "paia-program" in prompt
    assert "process-debt" in prompt
    assert "live_agents=30" in prompt
    assert "confidence" in prompt.lower()


def test_build_model_prompt_includes_all_finding_fields():
    finding = {
        "repo": "news-briefing",
        "category": FindingCategory.LIFECYCLE_VIOLATION,
        "severity": "high",
        "declared": "lifecycle=retired, daemon_posture=never",
        "observed": "daemon_socket_live=True, live_agents=0",
    }
    prompt = build_model_prompt(finding)
    assert "news-briefing" in prompt
    assert "lifecycle-violation" in prompt
    assert "high" in prompt
    assert "retired" in prompt


# --- parse_model_response ---


def test_parse_model_response_extracts_confidence_and_path():
    model_output = """
    confidence: 0.92
    remediation: workgraph_task
    narrative: paia-program has 30 agents alive with no ready tasks, indicating a runaway executor.
    claude_prompt: Stop the paia-program daemon and archive failed tasks.
    """
    result = parse_model_response(model_output)
    assert result["confidence"] == pytest.approx(0.92)
    assert result["remediation_path"] == "workgraph_task"
    assert "runaway" in result["narrative"]
    assert result["claude_prompt"] is not None


def test_parse_model_response_handles_missing_confidence():
    result = parse_model_response("some output without confidence field")
    assert result["confidence"] == 0.5  # safe default


def test_parse_model_response_handles_missing_remediation():
    result = parse_model_response("confidence: 0.88\nnarrative: something broke")
    assert result["remediation_path"] == "inbox_signal"  # safe default


def test_parse_model_response_handles_empty_string():
    result = parse_model_response("")
    assert result["confidence"] == 0.5
    assert result["remediation_path"] == "inbox_signal"
    assert result["narrative"] == ""
    assert result["claude_prompt"] is None


# --- collect_ecosystem_governance ---


def test_collect_ecosystem_governance_returns_findings_and_op_health(tmp_path):
    """collect_ecosystem_governance returns conformance_findings and op_health_inputs."""
    # Set up a retired repo with a daemon socket that will not connect
    retired = tmp_path / "retired-repo"
    retired.mkdir()
    # No daemon socket, so daemon_socket_live=False => no lifecycle violation

    active = tmp_path / "active-repo"
    active.mkdir()
    (active / "NORTH_STAR.md").write_text("# North Star\n")

    repos = [
        {"name": "retired-repo", "lifecycle": "retired", "daemon_posture": "never"},
        {"name": "active-repo", "lifecycle": "active", "daemon_posture": "always-on"},
    ]
    result = collect_ecosystem_governance(repos, tmp_path)
    assert "conformance_findings" in result
    assert "op_health_inputs" in result
    assert isinstance(result["conformance_findings"], list)
    assert isinstance(result["op_health_inputs"], dict)
    for key in ("zombie_ratio", "failed_abandoned_ratio", "posture_alignment_ratio", "abandoned_age_pressure"):
        assert key in result["op_health_inputs"]


def test_collect_ecosystem_governance_skips_nonexistent_repos(tmp_path):
    """Repos that don't exist on disk are silently skipped."""
    repos = [
        {"name": "does-not-exist", "lifecycle": "active", "daemon_posture": "always-on"},
    ]
    result = collect_ecosystem_governance(repos, tmp_path)
    assert result["conformance_findings"] == []
    assert result["op_health_inputs"]["posture_alignment_ratio"] == 0.0


def test_collect_ecosystem_governance_finding_category_is_string(tmp_path):
    """Finding categories are serialized as strings, not enum objects."""
    import json
    retired = tmp_path / "retired-repo"
    retired.mkdir()
    wg = retired / ".workgraph" / "service"
    wg.mkdir(parents=True)

    repos = [
        {"name": "retired-repo", "lifecycle": "active", "daemon_posture": "always-on"},
    ]
    result = collect_ecosystem_governance(repos, tmp_path)
    # Should be JSON-serializable (no enum objects)
    json.dumps(result)


def test_collect_ecosystem_governance_posture_alignment(tmp_path):
    """on-demand posture always counts as aligned."""
    repo = tmp_path / "flex-repo"
    repo.mkdir()
    (repo / "NORTH_STAR.md").write_text("# North Star\n")

    repos = [
        {"name": "flex-repo", "lifecycle": "maintenance", "daemon_posture": "on-demand"},
    ]
    result = collect_ecosystem_governance(repos, tmp_path)
    assert result["op_health_inputs"]["posture_alignment_ratio"] == 1.0


# --- enforce_daemon_posture ---


from driftdriver.governancedrift import enforce_daemon_posture


def test_enforce_daemon_posture_stops_retired_repo_with_running_service():
    """Retired repo with service_running=True should be stopped."""
    repos = [
        {
            "name": "news-briefing",
            "path": "/tmp/fake/news-briefing",
            "lifecycle": "retired",
            "daemon_posture": "never",
            "service_running": True,
            "workgraph_exists": True,
        },
    ]
    result = enforce_daemon_posture(repos_payload=repos, dry_run=True)
    assert result["checked"] >= 1
    assert result["enforcement_candidates"] == 1
    assert len(result["actions"]) == 1
    assert result["actions"][0]["repo"] == "news-briefing"
    assert result["actions"][0]["action"] == "stop_service"
    assert result["actions"][0]["reason_category"] in ("lifecycle-violation", "posture-mismatch")


def test_enforce_daemon_posture_stops_experimental_repo_with_running_service():
    """Experimental repo with service_running=True should be stopped."""
    repos = [
        {
            "name": "run3",
            "path": "/tmp/fake/run3",
            "lifecycle": "experimental",
            "daemon_posture": "never",
            "service_running": True,
            "workgraph_exists": True,
        },
    ]
    result = enforce_daemon_posture(repos_payload=repos, dry_run=True)
    assert result["enforcement_candidates"] == 1
    assert result["actions"][0]["repo"] == "run3"


def test_enforce_daemon_posture_stops_never_posture_with_running_service():
    """Any repo with daemon_posture=never and running service should be stopped."""
    repos = [
        {
            "name": "stable-repo",
            "path": "/tmp/fake/stable-repo",
            "lifecycle": "maintenance",
            "daemon_posture": "never",
            "service_running": True,
            "workgraph_exists": True,
        },
    ]
    result = enforce_daemon_posture(repos_payload=repos, dry_run=True)
    assert result["enforcement_candidates"] == 1
    assert result["actions"][0]["reason_category"] == "posture-mismatch"


def test_enforce_daemon_posture_skips_active_always_on():
    """Active repos with always-on posture should not be stopped."""
    repos = [
        {
            "name": "paia-os",
            "path": "/tmp/fake/paia-os",
            "lifecycle": "active",
            "daemon_posture": "always-on",
            "service_running": True,
            "workgraph_exists": True,
        },
    ]
    result = enforce_daemon_posture(repos_payload=repos, dry_run=True)
    assert result["enforcement_candidates"] == 0
    assert len(result["actions"]) == 0


def test_enforce_daemon_posture_skips_already_stopped():
    """Repos with service_running=False need no enforcement."""
    repos = [
        {
            "name": "news-briefing",
            "path": "/tmp/fake/news-briefing",
            "lifecycle": "retired",
            "daemon_posture": "never",
            "service_running": False,
            "workgraph_exists": True,
        },
    ]
    result = enforce_daemon_posture(repos_payload=repos, dry_run=True)
    assert result["enforcement_candidates"] == 0


def test_enforce_daemon_posture_skips_no_workgraph():
    """Repos without workgraph can't have a daemon to stop."""
    repos = [
        {
            "name": "no-wg",
            "path": "/tmp/fake/no-wg",
            "lifecycle": "retired",
            "daemon_posture": "never",
            "service_running": False,
            "workgraph_exists": False,
        },
    ]
    result = enforce_daemon_posture(repos_payload=repos, dry_run=True)
    assert result["enforcement_candidates"] == 0


def test_enforce_daemon_posture_multiple_repos_mixed():
    """Only violating repos get enforcement actions."""
    repos = [
        {
            "name": "retired-live",
            "path": "/tmp/fake/retired-live",
            "lifecycle": "retired",
            "daemon_posture": "never",
            "service_running": True,
            "workgraph_exists": True,
        },
        {
            "name": "active-healthy",
            "path": "/tmp/fake/active-healthy",
            "lifecycle": "active",
            "daemon_posture": "always-on",
            "service_running": True,
            "workgraph_exists": True,
        },
        {
            "name": "experimental-live",
            "path": "/tmp/fake/experimental-live",
            "lifecycle": "experimental",
            "daemon_posture": "never",
            "service_running": True,
            "workgraph_exists": True,
        },
    ]
    result = enforce_daemon_posture(repos_payload=repos, dry_run=True)
    assert result["checked"] == 3
    assert result["enforcement_candidates"] == 2
    names = {a["repo"] for a in result["actions"]}
    assert names == {"retired-live", "experimental-live"}


def test_enforce_daemon_posture_on_demand_not_enforced():
    """on-demand posture repos are not stopped even if daemon is running."""
    repos = [
        {
            "name": "flex-repo",
            "path": "/tmp/fake/flex-repo",
            "lifecycle": "maintenance",
            "daemon_posture": "on-demand",
            "service_running": True,
            "workgraph_exists": True,
        },
    ]
    result = enforce_daemon_posture(repos_payload=repos, dry_run=True)
    assert result["enforcement_candidates"] == 0


def test_enforce_daemon_posture_respects_max_stops():
    """Should not exceed max_stops per cycle."""
    repos = [
        {
            "name": f"retired-{i}",
            "path": f"/tmp/fake/retired-{i}",
            "lifecycle": "retired",
            "daemon_posture": "never",
            "service_running": True,
            "workgraph_exists": True,
        }
        for i in range(10)
    ]
    result = enforce_daemon_posture(repos_payload=repos, dry_run=True, max_stops=3)
    assert len(result["actions"]) == 3
