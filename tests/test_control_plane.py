# ABOUTME: Tests for the ecosystem control plane — pressure scoring, chain analysis, priority suggestions.
# ABOUTME: TDD foundation for dependency-pressure-aware work prioritization across repos.
from __future__ import annotations

import unittest
from dataclasses import field

from driftdriver.ecosystem_hub.models import RepoSnapshot


def _snap(
    name: str,
    *,
    stalled: bool = False,
    stall_reasons: list[str] | None = None,
    blocked_open: int = 0,
    in_progress: list[dict] | None = None,
    ready: list[dict] | None = None,
    task_counts: dict[str, int] | None = None,
    stale_in_progress: list[dict] | None = None,
    cross_repo_deps: list[dict] | None = None,
    workgraph_exists: bool = True,
    service_running: bool = True,
) -> RepoSnapshot:
    """Build a minimal RepoSnapshot for control-plane testing."""
    snap = RepoSnapshot(
        name=name,
        path=f"/fake/{name}",
        exists=True,
        workgraph_exists=workgraph_exists,
        service_running=service_running,
        stalled=stalled,
        stall_reasons=stall_reasons or [],
        blocked_open=blocked_open,
        in_progress=in_progress or [],
        ready=ready or [],
        task_counts=task_counts or {},
        stale_in_progress=stale_in_progress or [],
        cross_repo_dependencies=cross_repo_deps or [],
    )
    return snap


class TestComputeRepoPressure(unittest.TestCase):
    """Test compute_repo_pressure — the per-repo pressure scoring function."""

    def test_stalled_repo_with_downstream_gets_high_pressure(self) -> None:
        from driftdriver.control_plane import compute_repo_pressure

        # Repo A is stalled. Repo B depends on A.
        a = _snap("repo-a", stalled=True, stall_reasons=["no active execution"],
                   blocked_open=2, task_counts={"open": 3, "in-progress": 0})
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 6, "reasons": ["explicit_dependency_ref=1"]}])
        repos = [a, b]
        result = compute_repo_pressure(repos)
        self.assertIn("repo-a", result)
        # repo-a should have pressure > 0 because it blocks repo-b
        self.assertGreater(result["repo-a"]["pressure"], 0)
        # repo-b depends on a, it shouldn't generate blocking pressure itself
        self.assertEqual(result["repo-b"]["downstream_count"], 0)

    def test_healthy_repo_has_zero_pressure(self) -> None:
        from driftdriver.control_plane import compute_repo_pressure

        a = _snap("repo-a", task_counts={"done": 5})
        repos = [a]
        result = compute_repo_pressure(repos)
        self.assertEqual(result["repo-a"]["pressure"], 0)

    def test_pressure_scales_with_downstream_count(self) -> None:
        from driftdriver.control_plane import compute_repo_pressure

        # Stalled A, with B and C both depending on it
        a = _snap("repo-a", stalled=True, stall_reasons=["stuck"],
                   blocked_open=1, task_counts={"open": 2})
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        c = _snap("repo-c", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        repos = [a, b, c]
        result = compute_repo_pressure(repos)
        self.assertEqual(result["repo-a"]["downstream_count"], 2)
        # More downstream dependents should yield higher pressure
        self.assertGreater(result["repo-a"]["pressure"], 0)

    def test_non_stalled_but_blocked_generates_moderate_pressure(self) -> None:
        from driftdriver.control_plane import compute_repo_pressure

        # A has blocked tasks but isn't fully stalled
        a = _snap("repo-a", blocked_open=3, task_counts={"open": 5, "in-progress": 1},
                   in_progress=[{"id": "t1", "title": "active task"}])
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        repos = [a, b]
        result = compute_repo_pressure(repos)
        # Should still have some pressure because blocked tasks signal slow output
        self.assertGreater(result["repo-a"]["pressure"], 0)

    def test_empty_repos_returns_empty(self) -> None:
        from driftdriver.control_plane import compute_repo_pressure

        result = compute_repo_pressure([])
        self.assertEqual(result, {})

    def test_pressure_includes_reasons(self) -> None:
        from driftdriver.control_plane import compute_repo_pressure

        a = _snap("repo-a", stalled=True, stall_reasons=["no workers"],
                   blocked_open=1, task_counts={"open": 2})
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        repos = [a, b]
        result = compute_repo_pressure(repos)
        self.assertIsInstance(result["repo-a"]["reasons"], list)
        self.assertTrue(len(result["repo-a"]["reasons"]) > 0)


class TestDependencyChainAnalysis(unittest.TestCase):
    """Test dependency_chain — full downstream chain from a given repo."""

    def test_simple_chain(self) -> None:
        from driftdriver.control_plane import dependency_chain

        # A -> B -> C (B depends on A, C depends on B)
        a = _snap("repo-a")
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        c = _snap("repo-c", cross_repo_deps=[{"repo": "repo-b", "score": 4, "reasons": []}])
        repos = [a, b, c]
        chain = dependency_chain("repo-a", repos)
        self.assertIn("repo-b", chain["downstream"])
        self.assertIn("repo-c", chain["downstream"])
        self.assertEqual(chain["depth"], 2)

    def test_no_downstream(self) -> None:
        from driftdriver.control_plane import dependency_chain

        a = _snap("repo-a")
        b = _snap("repo-b")
        repos = [a, b]
        chain = dependency_chain("repo-a", repos)
        self.assertEqual(chain["downstream"], [])
        self.assertEqual(chain["depth"], 0)

    def test_unknown_repo(self) -> None:
        from driftdriver.control_plane import dependency_chain

        a = _snap("repo-a")
        repos = [a]
        chain = dependency_chain("nonexistent", repos)
        self.assertEqual(chain["downstream"], [])
        self.assertEqual(chain["depth"], 0)
        self.assertEqual(chain["repo"], "nonexistent")

    def test_diamond_dependency(self) -> None:
        from driftdriver.control_plane import dependency_chain

        # A -> B, A -> C, B -> D, C -> D (diamond)
        a = _snap("repo-a")
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        c = _snap("repo-c", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        d = _snap("repo-d", cross_repo_deps=[
            {"repo": "repo-b", "score": 4, "reasons": []},
            {"repo": "repo-c", "score": 4, "reasons": []},
        ])
        repos = [a, b, c, d]
        chain = dependency_chain("repo-a", repos)
        downstream = chain["downstream"]
        self.assertIn("repo-b", downstream)
        self.assertIn("repo-c", downstream)
        self.assertIn("repo-d", downstream)
        self.assertEqual(len(downstream), 3)
        # Depth: A->B->D or A->C->D = 2
        self.assertEqual(chain["depth"], 2)

    def test_cycle_handling(self) -> None:
        from driftdriver.control_plane import dependency_chain

        # A -> B -> A (cycle) — must not infinite loop
        a = _snap("repo-a", cross_repo_deps=[{"repo": "repo-b", "score": 4, "reasons": []}])
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        repos = [a, b]
        chain = dependency_chain("repo-a", repos)
        # Should find repo-b as downstream (since B depends on A)
        self.assertIn("repo-b", chain["downstream"])
        # But should not infinite loop — depth should be finite
        self.assertLessEqual(chain["depth"], 2)

    def test_chain_includes_edges(self) -> None:
        from driftdriver.control_plane import dependency_chain

        a = _snap("repo-a")
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 6, "reasons": ["explicit"]}])
        repos = [a, b]
        chain = dependency_chain("repo-a", repos)
        self.assertIn("edges", chain)
        self.assertTrue(len(chain["edges"]) > 0)
        edge = chain["edges"][0]
        self.assertEqual(edge["from"], "repo-a")
        self.assertEqual(edge["to"], "repo-b")


class TestPrioritySuggestions(unittest.TestCase):
    """Test suggest_priorities — ecosystem-wide work ordering by dependency pressure."""

    def test_stalled_blocker_ranked_first(self) -> None:
        from driftdriver.control_plane import suggest_priorities

        # A is stalled and blocks B and C. D is healthy.
        a = _snap("repo-a", stalled=True, stall_reasons=["stuck"],
                   blocked_open=2, task_counts={"open": 3})
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 6, "reasons": []}])
        c = _snap("repo-c", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        d = _snap("repo-d", task_counts={"open": 1})
        repos = [a, b, c, d]
        suggestions = suggest_priorities(repos)
        self.assertTrue(len(suggestions) > 0)
        # A should be first since it blocks 2 repos and is stalled
        self.assertEqual(suggestions[0]["repo"], "repo-a")

    def test_healthy_ecosystem_returns_empty(self) -> None:
        from driftdriver.control_plane import suggest_priorities

        a = _snap("repo-a", task_counts={"done": 5})
        b = _snap("repo-b", task_counts={"done": 3})
        repos = [a, b]
        suggestions = suggest_priorities(repos)
        self.assertEqual(suggestions, [])

    def test_suggestions_include_action_hint(self) -> None:
        from driftdriver.control_plane import suggest_priorities

        a = _snap("repo-a", stalled=True, stall_reasons=["no workers"],
                   blocked_open=1, task_counts={"open": 2})
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        repos = [a, b]
        suggestions = suggest_priorities(repos)
        self.assertTrue(len(suggestions) > 0)
        self.assertIn("action", suggestions[0])
        self.assertIsInstance(suggestions[0]["action"], str)
        self.assertTrue(len(suggestions[0]["action"]) > 0)

    def test_limit_parameter(self) -> None:
        from driftdriver.control_plane import suggest_priorities

        repos = []
        for i in range(10):
            name = f"repo-{i}"
            repos.append(_snap(name, stalled=True, stall_reasons=["stuck"],
                               blocked_open=1, task_counts={"open": 2}))
        # Add a depender for each
        for i in range(10):
            depender = _snap(f"dep-{i}", cross_repo_deps=[
                {"repo": f"repo-{i}", "score": 4, "reasons": []}
            ])
            repos.append(depender)
        suggestions = suggest_priorities(repos, limit=3)
        self.assertLessEqual(len(suggestions), 3)


class TestBuildPressurePayload(unittest.TestCase):
    """Test build_pressure_payload — the full API response builder."""

    def test_payload_structure(self) -> None:
        from driftdriver.control_plane import build_pressure_payload

        a = _snap("repo-a", stalled=True, stall_reasons=["stuck"],
                   blocked_open=1, task_counts={"open": 2})
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        repos = [a, b]
        payload = build_pressure_payload(repos)
        self.assertIn("pressure_scores", payload)
        self.assertIn("suggestions", payload)
        self.assertIn("summary", payload)

    def test_payload_summary_has_max_pressure(self) -> None:
        from driftdriver.control_plane import build_pressure_payload

        a = _snap("repo-a", stalled=True, stall_reasons=["stuck"],
                   blocked_open=1, task_counts={"open": 2})
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        repos = [a, b]
        payload = build_pressure_payload(repos)
        summary = payload["summary"]
        self.assertIn("max_pressure", summary)
        self.assertIn("repos_under_pressure", summary)
        self.assertGreater(summary["max_pressure"], 0)
        self.assertGreater(summary["repos_under_pressure"], 0)

    def test_empty_repos(self) -> None:
        from driftdriver.control_plane import build_pressure_payload

        payload = build_pressure_payload([])
        self.assertEqual(payload["pressure_scores"], {})
        self.assertEqual(payload["suggestions"], [])
        self.assertEqual(payload["summary"]["max_pressure"], 0)
        self.assertEqual(payload["summary"]["repos_under_pressure"], 0)


class TestChainPayload(unittest.TestCase):
    """Test build_chain_payload — single-repo chain API response."""

    def test_chain_payload_for_known_repo(self) -> None:
        from driftdriver.control_plane import build_chain_payload

        a = _snap("repo-a")
        b = _snap("repo-b", cross_repo_deps=[{"repo": "repo-a", "score": 4, "reasons": []}])
        repos = [a, b]
        payload = build_chain_payload("repo-a", repos)
        self.assertEqual(payload["repo"], "repo-a")
        self.assertIn("repo-b", payload["downstream"])
        self.assertIn("edges", payload)
        self.assertIn("depth", payload)


class TestReposFromSnapshot(unittest.TestCase):
    """Test repos_from_snapshot — reconstructing RepoSnapshots from JSON."""

    def test_basic_reconstruction(self) -> None:
        from driftdriver.control_plane import repos_from_snapshot

        snapshot = {
            "repos": [
                {
                    "name": "repo-a",
                    "path": "/fake/repo-a",
                    "exists": True,
                    "workgraph_exists": True,
                    "service_running": False,
                    "stalled": True,
                    "stall_reasons": ["no workers"],
                    "blocked_open": 3,
                    "in_progress": [],
                    "ready": [{"id": "t1", "title": "ready task"}],
                    "task_counts": {"open": 4, "in-progress": 0},
                    "stale_in_progress": [],
                    "cross_repo_dependencies": [
                        {"repo": "repo-b", "score": 6, "reasons": ["explicit"]}
                    ],
                },
                {
                    "name": "repo-b",
                    "path": "/fake/repo-b",
                    "exists": True,
                    "workgraph_exists": True,
                    "service_running": True,
                    "stalled": False,
                    "blocked_open": 0,
                    "task_counts": {"done": 5},
                    "cross_repo_dependencies": [],
                },
            ]
        }
        repos = repos_from_snapshot(snapshot)
        self.assertEqual(len(repos), 2)
        self.assertEqual(repos[0].name, "repo-a")
        self.assertTrue(repos[0].stalled)
        self.assertEqual(repos[0].blocked_open, 3)
        self.assertEqual(len(repos[0].cross_repo_dependencies), 1)

    def test_empty_snapshot(self) -> None:
        from driftdriver.control_plane import repos_from_snapshot

        self.assertEqual(repos_from_snapshot({}), [])
        self.assertEqual(repos_from_snapshot({"repos": []}), [])
        self.assertEqual(repos_from_snapshot({"repos": "not_a_list"}), [])

    def test_skips_invalid_rows(self) -> None:
        from driftdriver.control_plane import repos_from_snapshot

        snapshot = {
            "repos": [
                "not_a_dict",
                {"name": ""},  # empty name
                {"name": "valid", "path": "/fake/valid"},
            ]
        }
        repos = repos_from_snapshot(snapshot)
        self.assertEqual(len(repos), 1)
        self.assertEqual(repos[0].name, "valid")


class TestEndToEndPressureFromSnapshot(unittest.TestCase):
    """Integration: build_pressure_payload from a serialized snapshot."""

    def test_pressure_from_snapshot_dict(self) -> None:
        from driftdriver.control_plane import build_pressure_payload, repos_from_snapshot

        snapshot = {
            "repos": [
                {
                    "name": "blocker",
                    "path": "/fake/blocker",
                    "stalled": True,
                    "stall_reasons": ["stuck"],
                    "blocked_open": 2,
                    "task_counts": {"open": 3},
                    "workgraph_exists": True,
                    "service_running": False,
                    "cross_repo_dependencies": [],
                },
                {
                    "name": "downstream",
                    "path": "/fake/downstream",
                    "task_counts": {"open": 1},
                    "cross_repo_dependencies": [
                        {"repo": "blocker", "score": 6, "reasons": ["explicit_dependency_ref=1"]}
                    ],
                },
            ]
        }
        repos = repos_from_snapshot(snapshot)
        payload = build_pressure_payload(repos)
        self.assertGreater(payload["summary"]["max_pressure"], 0)
        # Both repos have pressure: blocker (stalled, high) + downstream (idle open, low)
        self.assertGreaterEqual(payload["summary"]["repos_under_pressure"], 1)
        self.assertTrue(len(payload["suggestions"]) > 0)
        # Blocker should be ranked first — it has much higher pressure
        self.assertEqual(payload["suggestions"][0]["repo"], "blocker")


if __name__ == "__main__":
    unittest.main()
