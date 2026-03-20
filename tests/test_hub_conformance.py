# ABOUTME: Tests for hub conformance panel — lifecycle metadata in discovery and snapshot fields.
# ABOUTME: Verifies _load_ecosystem_repo_meta returns lifecycle/daemon_posture and defaults missing values.
import pytest
from pathlib import Path


FIXTURE_TOML = """
schema = 1
suite = "speedrift"

[repos.active-repo]
role = "service"
tags = ["personal"]
lifecycle = "active"
daemon_posture = "always-on"

[repos.retired-repo]
role = "service"
tags = ["personal"]
lifecycle = "retired"
daemon_posture = "never"

[repos.no-lifecycle-repo]
role = "service"
tags = ["personal"]
"""


def test_load_ecosystem_repo_meta_returns_lifecycle(tmp_path):
    toml_file = tmp_path / "ecosystem.toml"
    toml_file.write_text(FIXTURE_TOML)

    from driftdriver.ecosystem_hub.discovery import _load_ecosystem_repo_meta
    meta = _load_ecosystem_repo_meta(toml_file)

    assert meta["active-repo"]["lifecycle"] == "active"
    assert meta["active-repo"]["daemon_posture"] == "always-on"
    assert meta["retired-repo"]["lifecycle"] == "retired"
    assert meta["retired-repo"]["daemon_posture"] == "never"


def test_load_ecosystem_repo_meta_defaults_missing_lifecycle(tmp_path):
    toml_file = tmp_path / "ecosystem.toml"
    toml_file.write_text(FIXTURE_TOML)

    from driftdriver.ecosystem_hub.discovery import _load_ecosystem_repo_meta
    meta = _load_ecosystem_repo_meta(toml_file)

    # Repos without lifecycle default to "active" (safe default — don't hide them)
    assert meta["no-lifecycle-repo"]["lifecycle"] == "active"
    assert meta["no-lifecycle-repo"]["daemon_posture"] == "always-on"


def test_snapshot_repo_dict_includes_lifecycle_fields(tmp_path):
    """Verify that the per-repo snapshot dict (as produced by asdict) includes lifecycle fields."""
    from dataclasses import asdict
    from driftdriver.ecosystem_hub.models import RepoSnapshot

    snap = RepoSnapshot(name="test-repo", path=str(tmp_path), exists=True)
    snap.lifecycle = "maintenance"
    snap.daemon_posture = "on-demand"

    data = asdict(snap)
    assert data["lifecycle"] == "maintenance"
    assert data["daemon_posture"] == "on-demand"


def test_snapshot_repo_dict_lifecycle_defaults():
    """RepoSnapshot should default lifecycle to 'active' and daemon_posture to 'always-on'."""
    from driftdriver.ecosystem_hub.models import RepoSnapshot

    snap = RepoSnapshot(name="test-repo", path="/tmp/test", exists=True)
    assert snap.lifecycle == "active"
    assert snap.daemon_posture == "always-on"


def test_snapshot_json_conformance_findings_roundtrip(tmp_path):
    """Snapshot file with conformance_findings → reads back correctly via JSON."""
    import json

    findings = [
        {
            "repo": "news-briefing",
            "category": "lifecycle-violation",
            "severity": "high",
            "declared": "lifecycle=retired",
            "observed": "daemon_socket_live=True",
        }
    ]

    snapshot_file = tmp_path / "snapshot.json"
    snapshot_file.write_text(json.dumps({
        "conformance_findings": findings,
        "repos": [],
        "overview": {},
    }))

    data = json.loads(snapshot_file.read_text())
    assert len(data["conformance_findings"]) == 1
    assert data["conformance_findings"][0]["repo"] == "news-briefing"
    assert data["conformance_findings"][0]["category"] == "lifecycle-violation"


def test_api_conformance_route_returns_findings():
    """The /api/conformance route extracts conformance_findings from snapshot."""
    snapshot = {
        "conformance_findings": [
            {
                "repo": "news-briefing",
                "category": "lifecycle-violation",
                "severity": "high",
                "declared": "lifecycle=retired",
                "observed": "daemon_socket_live=True",
            },
            {
                "repo": "zombie-repo",
                "category": "process-debt",
                "severity": "high",
                "declared": "lifecycle=active",
                "observed": "live_agents=20, tasks_ready=0",
            },
        ],
        "repos": [],
        "overview": {},
    }

    from driftdriver.ecosystem_hub.api import extract_conformance_response
    response = extract_conformance_response(snapshot)
    assert response["count"] == 2
    assert len(response["findings"]) == 2
    assert response["findings"][0]["repo"] == "news-briefing"


def test_api_conformance_route_empty_snapshot():
    """When snapshot has no conformance_findings, return empty list."""
    snapshot = {"repos": [], "overview": {}}

    from driftdriver.ecosystem_hub.api import extract_conformance_response
    response = extract_conformance_response(snapshot)
    assert response["count"] == 0
    assert response["findings"] == []


def test_snapshot_includes_op_health_inputs():
    """Snapshot dict should include op_health_inputs at top level."""
    import json

    op_health = {
        "zombie_ratio": 0.1,
        "failed_abandoned_ratio": 0.05,
        "posture_alignment_ratio": 0.9,
        "abandoned_age_pressure": 0.02,
    }
    snapshot = {
        "conformance_findings": [],
        "op_health_inputs": op_health,
        "repos": [],
        "overview": {},
    }
    # Roundtrip through JSON to prove it's serializable
    data = json.loads(json.dumps(snapshot))
    assert data["op_health_inputs"]["zombie_ratio"] == 0.1
    assert data["op_health_inputs"]["posture_alignment_ratio"] == 0.9
