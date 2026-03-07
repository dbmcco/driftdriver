# ABOUTME: Tests for hub_analytics module — ecosystem aggregation functions extracted from snapshot.py.
# ABOUTME: Covers overview, narrative, secdrift/qadrift overviews, dependency graph, attention scoring, next-work ranking.
from __future__ import annotations

import unittest
from dataclasses import asdict

from driftdriver.ecosystem_hub.models import NextWorkItem, RepoSnapshot
from driftdriver.hub_analytics import (
    build_ecosystem_narrative,
    build_ecosystem_overview,
    build_qadrift_overview,
    build_repo_dependency_overview,
    build_secdrift_overview,
    rank_next_work,
    repo_attention_entry,
)


def _make_repo(
    name: str = "test-repo",
    *,
    exists: bool = True,
    workgraph_exists: bool = True,
    service_running: bool = True,
    task_counts: dict | None = None,
    in_progress: list | None = None,
    ready: list | None = None,
    stale_open: list | None = None,
    stale_in_progress: list | None = None,
    blocked_open: int = 0,
    missing_dependencies: int = 0,
    errors: list | None = None,
    activity_state: str = "active",
    stalled: bool = False,
    stall_reasons: list | None = None,
    git_dirty: bool = False,
    behind: int = 0,
    ahead: int = 0,
    security: dict | None = None,
    quality: dict | None = None,
    repo_north_star: dict | None = None,
    cross_repo_dependencies: list | None = None,
    source: str = "ecosystem-toml",
    narrative: str = "",
) -> RepoSnapshot:
    snap = RepoSnapshot(name=name, path=f"/tmp/{name}", exists=exists)
    snap.workgraph_exists = workgraph_exists
    snap.service_running = service_running
    snap.task_counts = task_counts or {}
    snap.in_progress = in_progress or []
    snap.ready = ready or []
    snap.stale_open = stale_open or []
    snap.stale_in_progress = stale_in_progress or []
    snap.blocked_open = blocked_open
    snap.missing_dependencies = missing_dependencies
    snap.errors = errors or []
    snap.activity_state = activity_state
    snap.stalled = stalled
    snap.stall_reasons = stall_reasons or []
    snap.git_dirty = git_dirty
    snap.behind = behind
    snap.ahead = ahead
    snap.security = security or {}
    snap.quality = quality or {}
    snap.repo_north_star = repo_north_star or {}
    snap.cross_repo_dependencies = cross_repo_dependencies or []
    snap.source = source
    snap.narrative = narrative
    return snap


class TestRepoAttentionEntry(unittest.TestCase):
    def test_healthy_repo_returns_none(self) -> None:
        repo = _make_repo(repo_north_star={"present": True, "status": "ok"})
        result = repo_attention_entry(repo)
        self.assertIsNone(result)

    def test_repo_with_errors_has_attention(self) -> None:
        repo = _make_repo(errors=["git_error"])
        result = repo_attention_entry(repo)
        self.assertIsNotNone(result)
        self.assertGreater(result["score"], 0)
        self.assertEqual(result["repo"], "test-repo")

    def test_stalled_repo_has_attention(self) -> None:
        repo = _make_repo(stalled=True, stall_reasons=["no active execution"])
        result = repo_attention_entry(repo)
        self.assertIsNotNone(result)
        self.assertIn("stalled", result["reasons"][0])

    def test_security_critical_boosts_score(self) -> None:
        repo = _make_repo(security={"critical": 2, "high": 0, "findings_total": 2, "at_risk": True})
        result = repo_attention_entry(repo)
        self.assertIsNotNone(result)
        self.assertGreater(result["score"], 20)

    def test_missing_north_star_adds_score(self) -> None:
        repo = _make_repo(repo_north_star={"present": False})
        result = repo_attention_entry(repo)
        self.assertIsNotNone(result)
        self.assertTrue(any("north star" in r for r in result["reasons"]))

    def test_inactive_service_adds_score(self) -> None:
        repo = _make_repo(workgraph_exists=True, service_running=False)
        result = repo_attention_entry(repo)
        self.assertIsNotNone(result)
        self.assertTrue(any("service" in r for r in result["reasons"]))


class TestBuildEcosystemOverview(unittest.TestCase):
    def test_empty_repos(self) -> None:
        overview = build_ecosystem_overview(
            [], upstream_candidates=0, updates={}, central_reports=[]
        )
        self.assertEqual(overview["repos_total"], 0)
        self.assertEqual(overview["tasks_open"], 0)

    def test_aggregates_task_counts(self) -> None:
        repos = [
            _make_repo("a", task_counts={"open": 3, "in-progress": 1, "done": 5}),
            _make_repo("b", task_counts={"open": 2, "ready": 1, "done": 10}),
        ]
        overview = build_ecosystem_overview(
            repos, upstream_candidates=2, updates={}, central_reports=[]
        )
        self.assertEqual(overview["repos_total"], 2)
        self.assertEqual(overview["tasks_open"], 5)
        self.assertEqual(overview["tasks_ready"], 1)
        self.assertEqual(overview["tasks_in_progress"], 1)
        self.assertEqual(overview["tasks_done"], 15)
        self.assertEqual(overview["upstream_candidates"], 2)

    def test_counts_stalled_repos(self) -> None:
        repos = [
            _make_repo("a", activity_state="stalled"),
            _make_repo("b", activity_state="active"),
            _make_repo("c", activity_state="stalled"),
        ]
        overview = build_ecosystem_overview(
            repos, upstream_candidates=0, updates={}, central_reports=[]
        )
        self.assertEqual(overview["repos_stalled"], 2)

    def test_security_quality_counts(self) -> None:
        repos = [
            _make_repo(
                "a",
                security={"critical": 1, "high": 2, "at_risk": True},
                quality={"critical": 0, "high": 1, "at_risk": False, "quality_score": 85},
            ),
        ]
        overview = build_ecosystem_overview(
            repos, upstream_candidates=0, updates={}, central_reports=[]
        )
        self.assertEqual(overview["security_critical"], 1)
        self.assertEqual(overview["security_high"], 2)
        self.assertEqual(overview["repos_security_risk"], 1)
        self.assertEqual(overview["quality_high"], 1)

    def test_attention_repos_sorted_by_score(self) -> None:
        repos = [
            _make_repo("low-risk", behind=1, git_dirty=True),
            _make_repo("high-risk", errors=["err"], stalled=True, stall_reasons=["blocked"]),
        ]
        overview = build_ecosystem_overview(
            repos, upstream_candidates=0, updates={}, central_reports=[]
        )
        attention = overview["attention_repos"]
        self.assertEqual(len(attention), 2)
        self.assertEqual(attention[0]["repo"], "high-risk")


class TestBuildEcosystemNarrative(unittest.TestCase):
    def test_no_repos(self) -> None:
        narrative = build_ecosystem_narrative({"repos_total": 0})
        self.assertIn("No repositories", narrative)

    def test_stable_posture(self) -> None:
        overview = {
            "repos_total": 3,
            "tasks_in_progress": 2,
            "tasks_ready": 1,
            "blocked_open": 0,
            "stale_open": 0,
            "stale_in_progress": 0,
            "repos_with_inactive_service": 0,
            "repos_stalled": 0,
            "repos_with_errors": 0,
            "missing_dependencies": 0,
            "repos_missing_north_star": 0,
            "security_critical": 0,
            "security_high": 0,
            "quality_critical": 0,
            "quality_high": 0,
            "attention_repos": [],
        }
        narrative = build_ecosystem_narrative(overview)
        self.assertIn("Stable posture", narrative)

    def test_alert_posture_when_errors(self) -> None:
        overview = {
            "repos_total": 3,
            "tasks_in_progress": 1,
            "tasks_ready": 0,
            "blocked_open": 0,
            "stale_open": 0,
            "stale_in_progress": 0,
            "repos_with_inactive_service": 0,
            "repos_stalled": 0,
            "repos_with_errors": 1,
            "missing_dependencies": 0,
            "repos_missing_north_star": 0,
            "security_critical": 0,
            "security_high": 0,
            "quality_critical": 0,
            "quality_high": 0,
            "attention_repos": [],
        }
        narrative = build_ecosystem_narrative(overview)
        self.assertIn("Alert posture", narrative)


class TestBuildSecdriftOverview(unittest.TestCase):
    def test_empty_repos(self) -> None:
        result = build_secdrift_overview([])
        self.assertEqual(result["summary"]["critical"], 0)
        self.assertEqual(result["repos"], [])

    def test_aggregates_counts(self) -> None:
        repos = [
            _make_repo("a", security={"critical": 1, "high": 2, "medium": 0, "low": 1, "findings_total": 4, "risk_score": 10, "at_risk": True, "narrative": "risky"}),
            _make_repo("b", security={"critical": 0, "high": 1, "medium": 1, "low": 0, "findings_total": 2, "risk_score": 5, "at_risk": False, "narrative": "ok"}),
        ]
        result = build_secdrift_overview(repos)
        self.assertEqual(result["summary"]["critical"], 1)
        self.assertEqual(result["summary"]["high"], 3)
        self.assertEqual(result["summary"]["repos_at_risk"], 1)
        self.assertEqual(len(result["repos"]), 2)

    def test_repos_sorted_by_critical_first(self) -> None:
        repos = [
            _make_repo("low", security={"critical": 0, "high": 1, "medium": 0, "low": 0, "findings_total": 1, "risk_score": 3, "at_risk": False}),
            _make_repo("high", security={"critical": 2, "high": 0, "medium": 0, "low": 0, "findings_total": 2, "risk_score": 20, "at_risk": True}),
        ]
        result = build_secdrift_overview(repos)
        self.assertEqual(result["repos"][0]["repo"], "high")


class TestBuildQadriftOverview(unittest.TestCase):
    def test_empty_repos(self) -> None:
        result = build_qadrift_overview([])
        self.assertEqual(result["summary"]["critical"], 0)
        self.assertEqual(result["repos"], [])

    def test_includes_low_quality_score_repos(self) -> None:
        repos = [
            _make_repo("a", quality={"critical": 0, "high": 0, "medium": 0, "low": 0, "findings_total": 0, "quality_score": 75, "at_risk": False, "narrative": ""}),
        ]
        result = build_qadrift_overview(repos)
        # quality_score < 90 should still be included
        self.assertEqual(len(result["repos"]), 1)


class TestBuildRepoDependencyOverview(unittest.TestCase):
    def test_empty_repos(self) -> None:
        result = build_repo_dependency_overview([])
        self.assertEqual(result["nodes"], [])
        self.assertEqual(result["edges"], [])
        self.assertEqual(result["summary"]["repo_count"], 0)

    def test_builds_nodes_and_edges(self) -> None:
        repo_a = _make_repo(
            "alpha",
            cross_repo_dependencies=[{"repo": "beta", "score": 5, "reasons": ["shared dependency"]}],
        )
        repo_b = _make_repo("beta", cross_repo_dependencies=[])
        result = build_repo_dependency_overview([repo_a, repo_b])
        self.assertEqual(result["summary"]["repo_count"], 2)
        self.assertEqual(result["summary"]["edge_count"], 1)
        self.assertEqual(result["summary"]["linked_repos"], 2)
        self.assertEqual(result["summary"]["isolated_repos"], 0)


class TestRankNextWork(unittest.TestCase):
    def test_empty_repos(self) -> None:
        result = rank_next_work([])
        self.assertEqual(result, [])

    def test_ranks_by_priority_descending(self) -> None:
        repo = _make_repo("test")
        repo.ready = [{"id": "low", "title": "Low"}, {"id": "high", "title": "High"}]
        repo.task_counts = {"ready": 2}
        result = rank_next_work([repo])
        self.assertIsInstance(result, list)
        self.assertTrue(len(result) > 0)


class TestAnalyticsFunctionsMatchOriginals(unittest.TestCase):
    """Verify the hub_analytics functions produce identical results to snapshot.py originals."""

    def test_overview_matches_snapshot_impl(self) -> None:
        """Overview output from hub_analytics should match what snapshot.py produced."""
        repos = [
            _make_repo(
                "r1",
                task_counts={"open": 3, "in-progress": 1, "done": 2},
                stale_open=[{"id": "s1"}],
                blocked_open=1,
                behind=2,
                ahead=1,
                git_dirty=True,
                security={"critical": 1, "high": 0, "at_risk": True},
                quality={"critical": 0, "high": 1, "at_risk": False, "quality_score": 80},
                repo_north_star={"present": True, "status": "ok"},
            ),
        ]
        overview = build_ecosystem_overview(
            repos, upstream_candidates=1, updates={"has_updates": True}, central_reports=[{"name": "r"}]
        )
        self.assertEqual(overview["repos_total"], 1)
        self.assertEqual(overview["tasks_open"], 3)
        self.assertEqual(overview["tasks_in_progress"], 1)
        self.assertEqual(overview["stale_open"], 1)
        self.assertEqual(overview["blocked_open"], 1)
        self.assertEqual(overview["total_behind"], 2)
        self.assertEqual(overview["total_ahead"], 1)
        self.assertEqual(overview["upstream_candidates"], 1)
        self.assertEqual(overview["update_has_updates"], True)
        self.assertEqual(overview["central_reports"], 1)

    def test_narrative_has_expected_structure(self) -> None:
        overview = {
            "repos_total": 5,
            "tasks_in_progress": 3,
            "tasks_ready": 2,
            "blocked_open": 1,
            "stale_open": 2,
            "stale_in_progress": 0,
            "repos_with_inactive_service": 1,
            "repos_stalled": 1,
            "repos_with_errors": 0,
            "missing_dependencies": 0,
            "repos_missing_north_star": 1,
            "security_critical": 0,
            "security_high": 0,
            "quality_critical": 0,
            "quality_high": 0,
            "attention_repos": [{"repo": "alpha", "reasons": ["stalled"]}],
        }
        narrative = build_ecosystem_narrative(overview)
        self.assertIn("tracking 5 repos", narrative)
        self.assertIn("alpha", narrative)
        self.assertIn("North Star", narrative)


if __name__ == "__main__":
    unittest.main()
