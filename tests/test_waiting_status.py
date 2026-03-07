# ABOUTME: Tests for the "waiting" task status support across driftdriver.
# ABOUTME: Verifies waiting tasks are not treated as stalled, appear in counts, and don't trigger pressure.
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from driftdriver.ecosystem_hub.models import RepoSnapshot
from driftdriver.ecosystem_hub.snapshot import (
    _build_repo_narrative,
    _build_repo_task_graph,
    _derive_repo_activity_state,
    _finalize_repo_snapshot,
    _task_status_rank,
)


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
    stale_open: list[dict] | None = None,
    cross_repo_deps: list[dict] | None = None,
    workgraph_exists: bool = True,
    service_running: bool = True,
) -> RepoSnapshot:
    """Build a minimal RepoSnapshot for testing."""
    return RepoSnapshot(
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
        stale_open=stale_open or [],
        cross_repo_dependencies=cross_repo_deps or [],
    )


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(path), check=True, capture_output=True)
    (path / "README.md").write_text("# repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=str(path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(path), check=True, capture_output=True)


def _write_graph(path: Path, tasks: list[dict]) -> None:
    wg_dir = path / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    graph = wg_dir / "graph.jsonl"
    lines = [json.dumps({**task, "type": "task"}) for task in tasks]
    graph.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestTaskStatusRank(unittest.TestCase):
    """Verify _task_status_rank places waiting alongside blocked/review."""

    def test_waiting_rank_equals_blocked(self) -> None:
        self.assertEqual(_task_status_rank("waiting"), _task_status_rank("blocked"))

    def test_waiting_rank_equals_review(self) -> None:
        self.assertEqual(_task_status_rank("waiting"), _task_status_rank("review"))

    def test_waiting_rank_lower_than_done(self) -> None:
        self.assertLess(_task_status_rank("waiting"), _task_status_rank("done"))

    def test_waiting_rank_higher_than_open(self) -> None:
        self.assertGreater(_task_status_rank("waiting"), _task_status_rank("open"))


class TestWaitingNotStalled(unittest.TestCase):
    """Waiting tasks must not cause a repo to appear stalled."""

    def test_only_waiting_tasks_not_stalled(self) -> None:
        """A repo with only waiting tasks should not be 'stalled'."""
        snap = _snap(
            "repo-w",
            task_counts={"waiting": 3},
            service_running=True,
        )
        state, reasons = _derive_repo_activity_state(snap)
        self.assertNotEqual(state, "stalled")

    def test_only_waiting_tasks_returns_idle_with_parked_message(self) -> None:
        """A repo with only waiting tasks should be 'idle' with a parked message."""
        snap = _snap(
            "repo-w",
            task_counts={"waiting": 2},
            service_running=True,
        )
        state, reasons = _derive_repo_activity_state(snap)
        self.assertEqual(state, "idle")
        self.assertTrue(any("waiting" in r or "parked" in r for r in reasons))

    def test_waiting_plus_done_not_stalled(self) -> None:
        """A repo with waiting + done tasks should not be stalled."""
        snap = _snap(
            "repo-wd",
            task_counts={"waiting": 2, "done": 5},
            service_running=True,
        )
        state, reasons = _derive_repo_activity_state(snap)
        self.assertNotEqual(state, "stalled")

    def test_open_plus_waiting_can_stall(self) -> None:
        """If open tasks exist with no in-progress, stall logic should still fire."""
        snap = _snap(
            "repo-ow",
            task_counts={"open": 3, "waiting": 2},
            service_running=True,
        )
        state, reasons = _derive_repo_activity_state(snap)
        # Open tasks with nothing in-progress triggers stalled
        self.assertEqual(state, "stalled")


class TestWaitingInSnapshotCounts(unittest.TestCase):
    """Waiting tasks should appear in task_counts when snapshot is collected."""

    def test_waiting_counted_in_task_graph(self) -> None:
        """Waiting tasks appear as nodes in the task graph."""
        tasks = {
            "t1": {"id": "t1", "title": "Done task", "status": "done", "after": []},
            "t2": {"id": "t2", "title": "Waiting task", "status": "waiting", "after": ["t1"]},
        }
        nodes, edges = _build_repo_task_graph(tasks)
        node_ids = {n["id"] for n in nodes}
        self.assertIn("t2", node_ids)
        waiting_node = next(n for n in nodes if n["id"] == "t2")
        self.assertEqual(waiting_node["status"], "waiting")

    def test_collect_repo_snapshot_counts_waiting(self) -> None:
        """Full snapshot collection counts waiting tasks."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            _write_graph(repo, [
                {"id": "t1", "title": "Open", "status": "open"},
                {"id": "t2", "title": "Waiting", "status": "waiting"},
                {"id": "t3", "title": "Done", "status": "done"},
            ])
            from driftdriver.ecosystem_hub.snapshot import collect_repo_snapshot

            snap = collect_repo_snapshot("test-repo", repo)
            self.assertEqual(snap.task_counts.get("waiting", 0), 1)
            self.assertEqual(snap.task_counts.get("open", 0), 1)
            self.assertEqual(snap.task_counts.get("done", 0), 1)

    def test_waiting_tasks_not_in_stale_open(self) -> None:
        """Waiting tasks must not appear in the stale_open list."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            _write_graph(repo, [
                {
                    "id": "t1",
                    "title": "Waiting old",
                    "status": "waiting",
                    "created_at": "2020-01-01T00:00:00Z",
                },
            ])
            from driftdriver.ecosystem_hub.snapshot import collect_repo_snapshot

            snap = collect_repo_snapshot("test-repo", repo)
            stale_ids = [s["id"] for s in snap.stale_open]
            self.assertNotIn("t1", stale_ids)

    def test_waiting_tasks_not_in_stale_in_progress(self) -> None:
        """Waiting tasks must not appear in the stale_in_progress list."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            _write_graph(repo, [
                {
                    "id": "t1",
                    "title": "Waiting old",
                    "status": "waiting",
                    "created_at": "2020-01-01T00:00:00Z",
                },
            ])
            from driftdriver.ecosystem_hub.snapshot import collect_repo_snapshot

            snap = collect_repo_snapshot("test-repo", repo)
            stale_ids = [s["id"] for s in snap.stale_in_progress]
            self.assertNotIn("t1", stale_ids)


class TestWaitingNoPressureEscalation(unittest.TestCase):
    """Waiting tasks should not trigger pressure escalation in the control plane."""

    def test_repo_with_only_waiting_zero_pressure(self) -> None:
        """A repo with only waiting tasks has zero pressure."""
        from driftdriver.control_plane import compute_repo_pressure

        a = _snap(
            "repo-a",
            task_counts={"waiting": 5},
            service_running=True,
        )
        result = compute_repo_pressure([a])
        self.assertEqual(result["repo-a"]["pressure"], 0)

    def test_waiting_tasks_dont_inflate_staleness(self) -> None:
        """Waiting tasks don't contribute to the staleness score."""
        from driftdriver.control_plane import _repo_staleness_score

        snap = _snap(
            "repo-a",
            task_counts={"waiting": 10},
            service_running=True,
        )
        self.assertEqual(_repo_staleness_score(snap), 0.0)

    def test_waiting_plus_done_zero_pressure(self) -> None:
        """A repo with waiting + done tasks but nothing open has zero pressure."""
        from driftdriver.control_plane import compute_repo_pressure

        a = _snap(
            "repo-a",
            task_counts={"waiting": 3, "done": 8},
            service_running=True,
        )
        result = compute_repo_pressure([a])
        self.assertEqual(result["repo-a"]["pressure"], 0)


class TestWaitingInEcosystemOverview(unittest.TestCase):
    """Ecosystem overview should include tasks_waiting."""

    def test_overview_includes_tasks_waiting(self) -> None:
        from driftdriver.hub_analytics import build_ecosystem_overview

        a = _snap("repo-a", task_counts={"waiting": 3, "open": 1, "done": 5})
        b = _snap("repo-b", task_counts={"waiting": 2})
        overview = build_ecosystem_overview(
            [a, b],
            upstream_candidates=0,
            updates={},
            central_reports=[],
        )
        self.assertEqual(overview["tasks_waiting"], 5)
        self.assertEqual(overview["tasks_open"], 1)
        self.assertEqual(overview["tasks_done"], 5)


class TestWaitingInNarrative(unittest.TestCase):
    """Repo narrative should mention waiting tasks."""

    def test_narrative_mentions_waiting(self) -> None:
        snap = _snap(
            "repo-a",
            task_counts={"waiting": 4},
            service_running=True,
        )
        snap = _finalize_repo_snapshot(snap)
        narrative = snap.narrative
        self.assertIn("waiting", narrative.lower())


class TestQadriftWaitingNotStalled(unittest.TestCase):
    """qadrift should not flag a repo as work-stalled due to waiting tasks."""

    def test_program_scan_no_stalled_finding_for_waiting_only(self) -> None:
        """Waiting-only snapshot should not produce a work-stalled finding."""
        from driftdriver.qadrift import run_program_quality_scan

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            report = run_program_quality_scan(
                repo_name="demo",
                repo_path=repo,
                repo_snapshot={
                    "stalled": False,
                    "stall_reasons": [],
                    "missing_dependencies": 0,
                    "blocked_open": 0,
                    "workgraph_exists": True,
                    "service_running": True,
                    "in_progress": [],
                    "ready": [],
                },
                policy_cfg={"include_playwright": False},
            )
            findings = report.get("findings") or []
            stalled_findings = [
                f for f in findings
                if isinstance(f, dict) and f.get("category") == "work-stalled"
            ]
            self.assertEqual(len(stalled_findings), 0)


if __name__ == "__main__":
    unittest.main()
