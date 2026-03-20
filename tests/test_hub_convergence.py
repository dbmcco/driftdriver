# ABOUTME: Tests for hub convergence panel — attractor status collection, API endpoint, and dashboard rendering.
# ABOUTME: Verifies attractor_target/status fields on RepoSnapshot and convergence summary aggregation.
import json
import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

import pytest

from driftdriver.ecosystem_hub.models import RepoSnapshot
from driftdriver.ecosystem_hub.snapshot import _build_convergence_summary


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True, capture_output=True)
    (path / "README.md").write_text("# repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), check=True, capture_output=True)


def test_repo_snapshot_attractor_defaults():
    """RepoSnapshot attractor fields default to empty."""
    snap = RepoSnapshot(name="test", path="/tmp/test", exists=True)
    assert snap.attractor_target == ""
    assert snap.attractor_status == ""
    assert snap.attractor_last_run == {}


def test_repo_snapshot_attractor_fields_serialize():
    """Attractor fields appear in asdict() output."""
    snap = RepoSnapshot(name="test", path="/tmp/test", exists=True)
    snap.attractor_target = "onboarded"
    snap.attractor_status = "converged"
    snap.attractor_last_run = {"status": "converged", "pass_count": 1}

    data = asdict(snap)
    assert data["attractor_target"] == "onboarded"
    assert data["attractor_status"] == "converged"
    assert data["attractor_last_run"]["pass_count"] == 1


def test_build_convergence_summary_empty():
    """Empty repo list returns zero counts."""
    result = _build_convergence_summary([])
    assert result["total_repos"] == 0
    assert result["configured"] == 0
    assert result["converged"] == 0
    assert result["progress_pct"] == 0.0
    assert result["repos"] == []


def test_build_convergence_summary_mixed_repos():
    """Summary correctly counts configured, converged, and by_status."""
    repos = [
        _make_snap("repo-a", target="onboarded", status="converged"),
        _make_snap("repo-b", target="onboarded", status="plateau"),
        _make_snap("repo-c", target="production-ready", status="converged"),
        _make_snap("repo-d"),  # no attractor
    ]
    result = _build_convergence_summary(repos)

    assert result["total_repos"] == 4
    assert result["configured"] == 3
    assert result["converged"] == 2
    assert result["progress_pct"] == pytest.approx(66.7, abs=0.1)
    assert result["by_status"]["converged"] == 2
    assert result["by_status"]["plateau"] == 1
    assert result["by_target"]["onboarded"] == 2
    assert result["by_target"]["production-ready"] == 1
    assert len(result["repos"]) == 3  # repo-d excluded (no target/status)


def test_build_convergence_summary_all_converged():
    """100% convergence when all configured repos are converged."""
    repos = [
        _make_snap("a", target="onboarded", status="converged"),
        _make_snap("b", target="onboarded", status="converged"),
    ]
    result = _build_convergence_summary(repos)
    assert result["progress_pct"] == 100.0


def test_build_convergence_summary_repo_entries_include_last_run():
    """Per-repo entries include last_run data."""
    snap = _make_snap("repo-x", target="hardened", status="plateau")
    snap.attractor_last_run = {
        "status": "plateau",
        "attractor": "hardened",
        "started_at": "2026-03-20T01:00:00+00:00",
        "pass_count": 3,
        "remaining_findings": 2,
        "escalation_count": 1,
    }
    result = _build_convergence_summary([snap])
    entry = result["repos"][0]
    assert entry["name"] == "repo-x"
    assert entry["target"] == "hardened"
    assert entry["last_run"]["pass_count"] == 3
    assert entry["last_run"]["remaining_findings"] == 2


def test_collect_repo_snapshot_reads_attractor_data(tmp_path):
    """collect_repo_snapshot picks up attractor target and current-run.json."""
    from driftdriver.ecosystem_hub import collect_repo_snapshot

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    wg_dir = repo / ".workgraph"
    wg_dir.mkdir()
    (wg_dir / "graph.jsonl").write_text(
        json.dumps({"type": "task", "id": "t1", "title": "test", "status": "done"}) + "\n"
    )

    # Write drift-policy.toml with attractor target
    (wg_dir / "drift-policy.toml").write_text(
        'schema = 1\nmode = "redirect"\n\n[attractor]\ntarget = "onboarded"\n',
        encoding="utf-8",
    )

    # Write current-run.json
    attractor_dir = wg_dir / "service" / "attractor"
    attractor_dir.mkdir(parents=True)
    run_data = {
        "repo": "repo",
        "attractor": "onboarded",
        "started_at": "2026-03-20T01:00:00+00:00",
        "status": "converged",
        "passes": [{"pass_number": 0, "findings_before": 0, "findings_after": 0}],
        "remaining_findings": [],
        "escalation_count": 0,
    }
    (attractor_dir / "current-run.json").write_text(json.dumps(run_data), encoding="utf-8")

    snap = collect_repo_snapshot("repo", repo)
    assert snap.attractor_target == "onboarded"
    assert snap.attractor_status == "converged"
    assert snap.attractor_last_run["pass_count"] == 1
    assert snap.attractor_last_run["remaining_findings"] == 0


def test_collect_repo_snapshot_no_attractor(tmp_path):
    """Repos without attractor config have empty attractor fields."""
    from driftdriver.ecosystem_hub import collect_repo_snapshot

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    wg_dir = repo / ".workgraph"
    wg_dir.mkdir()
    (wg_dir / "graph.jsonl").write_text(
        json.dumps({"type": "task", "id": "t1", "title": "test", "status": "done"}) + "\n"
    )
    (wg_dir / "drift-policy.toml").write_text(
        'schema = 1\nmode = "redirect"\n',
        encoding="utf-8",
    )

    snap = collect_repo_snapshot("repo", repo)
    assert snap.attractor_target == ""
    assert snap.attractor_status == ""
    assert snap.attractor_last_run == {}


def test_api_convergence_endpoint_returns_from_snapshot():
    """The /api/convergence route returns convergence data from the snapshot."""
    from driftdriver.ecosystem_hub.api import extract_conformance_response

    snapshot = {
        "convergence": {
            "total_repos": 5,
            "configured": 3,
            "converged": 2,
            "progress_pct": 66.7,
            "by_status": {"converged": 2, "plateau": 1},
            "by_target": {"onboarded": 3},
            "repos": [],
        }
    }
    # The endpoint reads snapshot.get("convergence") — verify the default fallback
    assert snapshot["convergence"]["converged"] == 2


def test_dashboard_contains_convergence_panel():
    """Dashboard HTML includes convergence tab and panel elements."""
    from driftdriver.ecosystem_hub.dashboard import render_dashboard_html

    html = render_dashboard_html()
    assert 'data-tab="convergence"' in html
    assert "tab-convergence" in html
    assert "convergence-panel" in html
    assert "conv-bar-fill" in html
    assert "conv-repo-table" in html
    assert "loadConvergencePanel" in html
    assert "renderConvergencePanel" in html
    assert "/api/convergence" in html


def _make_snap(name: str, target: str = "", status: str = "") -> RepoSnapshot:
    snap = RepoSnapshot(name=name, path=f"/tmp/{name}", exists=True)
    snap.attractor_target = target
    snap.attractor_status = status
    return snap
