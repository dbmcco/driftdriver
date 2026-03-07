# ABOUTME: Tests for presence module integration with the ecosystem hub snapshot.
# ABOUTME: Verifies that repos show as "active" when presence heartbeat files exist.
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from driftdriver.actor import Actor
from driftdriver.ecosystem_hub.models import RepoSnapshot
from driftdriver.ecosystem_hub.snapshot import (
    _build_repo_narrative,
    _derive_repo_activity_state,
    _finalize_repo_snapshot,
    collect_repo_snapshot,
)
from driftdriver.presence import write_heartbeat


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
    workgraph_exists: bool = True,
    service_running: bool = False,
    presence_actors: list[dict] | None = None,
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
        presence_actors=presence_actors or [],
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


class TestDeriveActivityStateWithPresence(unittest.TestCase):
    """Presence actors should cause _derive_repo_activity_state to return 'active'."""

    def test_presence_actors_returns_active(self) -> None:
        """A repo with presence actors should be 'active' even without wg service."""
        snap = _snap(
            "repo-p",
            workgraph_exists=True,
            service_running=False,
            task_counts={"open": 2},
            presence_actors=[
                {"id": "claude-1", "name": "Claude Session", "class": "interactive", "task": "", "status": "active"},
            ],
        )
        state, reasons = _derive_repo_activity_state(snap)
        self.assertEqual(state, "active")
        self.assertEqual(reasons, [])

    def test_no_presence_no_service_still_stalled(self) -> None:
        """Without presence actors and no service, open tasks cause stall."""
        snap = _snap(
            "repo-s",
            workgraph_exists=True,
            service_running=False,
            task_counts={"open": 2},
        )
        state, _ = _derive_repo_activity_state(snap)
        self.assertEqual(state, "stalled")

    def test_presence_overrides_no_in_progress(self) -> None:
        """Presence-based activity should fire even with zero in-progress tasks."""
        snap = _snap(
            "repo-idle",
            workgraph_exists=True,
            service_running=False,
            task_counts={},
            presence_actors=[
                {"id": "braydon-1", "name": "Braydon", "class": "human", "task": "", "status": "active"},
            ],
        )
        state, reasons = _derive_repo_activity_state(snap)
        self.assertEqual(state, "active")
        self.assertEqual(reasons, [])

    def test_empty_presence_actors_no_effect(self) -> None:
        """An empty presence_actors list should not influence activity state."""
        snap = _snap(
            "repo-empty",
            workgraph_exists=True,
            service_running=False,
            task_counts={"open": 1},
            presence_actors=[],
        )
        state, _ = _derive_repo_activity_state(snap)
        self.assertNotEqual(state, "active")


class TestBuildRepoNarrativeWithPresence(unittest.TestCase):
    """_build_repo_narrative should mention presence actors."""

    def test_narrative_mentions_presence(self) -> None:
        snap = _snap(
            "repo-p",
            workgraph_exists=True,
            service_running=False,
            task_counts={"open": 1},
            presence_actors=[
                {"id": "claude-1", "name": "Claude Session", "class": "interactive", "task": "", "status": "active"},
            ],
        )
        snap = _finalize_repo_snapshot(snap)
        narrative = snap.narrative
        self.assertIn("active via presence", narrative)
        self.assertIn("Claude Session", narrative)

    def test_narrative_multiple_actors(self) -> None:
        snap = _snap(
            "repo-m",
            workgraph_exists=True,
            service_running=False,
            presence_actors=[
                {"id": "a1", "name": "Alice", "class": "interactive", "task": "", "status": "active"},
                {"id": "a2", "name": "Bob", "class": "worker", "task": "", "status": "active"},
            ],
        )
        snap = _finalize_repo_snapshot(snap)
        self.assertIn("2 active via presence", snap.narrative)

    def test_no_presence_no_mention(self) -> None:
        snap = _snap(
            "repo-n",
            workgraph_exists=True,
            service_running=True,
            task_counts={"done": 5},
        )
        snap = _finalize_repo_snapshot(snap)
        self.assertNotIn("presence", snap.narrative)


class TestCollectRepoSnapshotPresence(unittest.TestCase):
    """collect_repo_snapshot should populate presence_actors from heartbeat files."""

    def test_snapshot_populates_presence_actors(self) -> None:
        """Heartbeat files should appear in snap.presence_actors."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            _write_graph(repo, [
                {"id": "t1", "title": "Open task", "status": "open"},
            ])
            actor = Actor(id="claude-session-1", actor_class="interactive", name="Claude Session")
            write_heartbeat(repo, actor, current_task="t1", status="active")

            snap = collect_repo_snapshot("test-repo", repo)
            self.assertTrue(len(snap.presence_actors) >= 1)
            actor_ids = [a["id"] for a in snap.presence_actors]
            self.assertIn("claude-session-1", actor_ids)
            matched = [a for a in snap.presence_actors if a["id"] == "claude-session-1"][0]
            self.assertEqual(matched["name"], "Claude Session")
            self.assertEqual(matched["class"], "interactive")
            self.assertEqual(matched["task"], "t1")
            self.assertEqual(matched["status"], "active")

    def test_snapshot_active_with_presence_no_service(self) -> None:
        """A repo with presence but no wg service should still show as active."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            _write_graph(repo, [
                {"id": "t1", "title": "Open task", "status": "open"},
            ])
            actor = Actor(id="braydon-cli", actor_class="interactive", name="Braydon CLI")
            write_heartbeat(repo, actor, current_task="", status="active")

            snap = collect_repo_snapshot("test-repo", repo)
            self.assertEqual(snap.activity_state, "active")
            self.assertFalse(snap.stalled)

    def test_snapshot_no_presence_no_actors(self) -> None:
        """Without heartbeat files, presence_actors should be empty."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_repo(repo)
            _write_graph(repo, [
                {"id": "t1", "title": "Open task", "status": "open"},
            ])

            snap = collect_repo_snapshot("test-repo", repo)
            self.assertEqual(snap.presence_actors, [])


if __name__ == "__main__":
    unittest.main()
