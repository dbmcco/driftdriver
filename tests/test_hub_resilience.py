# ABOUTME: Tests for ecosystem hub resilience fixes — stale lock sweep, port exclusivity,
# ABOUTME: session suppression in factorydrift, and wg timeout exception safety.

from __future__ import annotations

import socket
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from driftdriver.ecosystem_hub.server import _clear_stale_graph_locks, _port_is_available
from driftdriver.presence import write_heartbeat


class TestStaleLockSweep:
    def test_clears_zero_byte_lock_files(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / "repo-a" / ".workgraph"
        wg_dir.mkdir(parents=True)
        lock = wg_dir / "graph.lock"
        lock.write_text("")

        cleared = _clear_stale_graph_locks(tmp_path)

        assert cleared == 1
        assert not lock.exists()

    def test_ignores_non_empty_lock_files(self, tmp_path: Path) -> None:
        wg_dir = tmp_path / "repo-a" / ".workgraph"
        wg_dir.mkdir(parents=True)
        lock = wg_dir / "graph.lock"
        lock.write_text("pid:12345")

        cleared = _clear_stale_graph_locks(tmp_path)

        assert cleared == 0
        assert lock.exists()

    def test_clears_multiple_repos(self, tmp_path: Path) -> None:
        for name in ["repo-a", "repo-b", "repo-c"]:
            wg_dir = tmp_path / name / ".workgraph"
            wg_dir.mkdir(parents=True)
            (wg_dir / "graph.lock").write_text("")

        cleared = _clear_stale_graph_locks(tmp_path)
        assert cleared == 3

    def test_handles_nested_repos(self, tmp_path: Path) -> None:
        nested = tmp_path / "experiments" / "sub-repo" / ".workgraph"
        nested.mkdir(parents=True)
        (nested / "graph.lock").write_text("")

        cleared = _clear_stale_graph_locks(tmp_path, max_depth=3)
        assert cleared == 1

    def test_respects_max_depth(self, tmp_path: Path) -> None:
        deep = tmp_path / "a" / "b" / "c" / "d" / ".workgraph"
        deep.mkdir(parents=True)
        (deep / "graph.lock").write_text("")

        cleared = _clear_stale_graph_locks(tmp_path, max_depth=2)
        assert cleared == 0

    def test_no_workgraph_dirs_returns_zero(self, tmp_path: Path) -> None:
        (tmp_path / "empty-repo").mkdir()
        cleared = _clear_stale_graph_locks(tmp_path)
        assert cleared == 0

    def test_skips_hidden_directories(self, tmp_path: Path) -> None:
        hidden = tmp_path / ".hidden" / ".workgraph"
        hidden.mkdir(parents=True)
        (hidden / "graph.lock").write_text("")

        cleared = _clear_stale_graph_locks(tmp_path)
        assert cleared == 0

    def test_handles_permission_errors(self, tmp_path: Path) -> None:
        # Should not crash on unreadable directories
        cleared = _clear_stale_graph_locks(tmp_path / "nonexistent")
        assert cleared == 0


class TestPortExclusivity:
    def test_available_port_returns_true(self) -> None:
        # Find a free port
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            _, port = s.getsockname()
        # Port should now be free
        assert _port_is_available("127.0.0.1", port) is True

    def test_occupied_port_returns_false(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            _, port = s.getsockname()
            s.listen(1)
            # Port is occupied by our socket
            assert _port_is_available("127.0.0.1", port) is False


class TestWgTimeoutExceptionSafety:
    def test_guarded_add_returns_error_on_wg_show_timeout(self, tmp_path: Path) -> None:
        from driftdriver.drift_task_guard import guarded_add_drift_task

        wg_dir = tmp_path / ".workgraph"
        wg_dir.mkdir()
        # Create a minimal graph.jsonl so _load_task_list doesn't fail
        (wg_dir / "graph.jsonl").write_text("")

        # Mock _run_wg to simulate timeout on `wg show`
        def fake_run_wg(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "timed out after 10.0 seconds")
            return (1, "", "not found")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=fake_run_wg):
            result = guarded_add_drift_task(
                wg_dir=wg_dir,
                task_id="test-task",
                title="Test",
                description="test",
                lane_tag="factory",
                cwd=tmp_path,
            )

        assert result == "error"


class TestSessionSuppressionInFactorydrift:
    def test_repos_with_active_sessions_returns_empty_without_presence(self) -> None:
        from driftdriver.factorydrift import _repos_with_active_sessions

        snapshot = {"repos": [{"name": "repo-a", "path": "/tmp/repo-a"}]}
        result = _repos_with_active_sessions(snapshot)
        assert result == set()

    def test_repos_with_active_sessions_detects_interactive(self, tmp_path: Path) -> None:
        from driftdriver.factorydrift import _repos_with_active_sessions
        from driftdriver.actor import Actor

        repo_path = tmp_path / "repo-a"
        repo_path.mkdir()
        actor = Actor(id="session-1", actor_class="interactive", name="claude-code")
        write_heartbeat(repo_path, actor)

        snapshot = {"repos": [{"name": "repo-a", "path": str(repo_path)}]}
        result = _repos_with_active_sessions(snapshot)
        assert "repo-a" in result

    def test_repos_with_active_sessions_ignores_workers(self, tmp_path: Path) -> None:
        from driftdriver.factorydrift import _repos_with_active_sessions
        from driftdriver.actor import Actor

        repo_path = tmp_path / "repo-b"
        repo_path.mkdir()
        actor = Actor(id="worker-1", actor_class="worker", name="agent")
        write_heartbeat(repo_path, actor)

        snapshot = {"repos": [{"name": "repo-b", "path": str(repo_path)}]}
        result = _repos_with_active_sessions(snapshot)
        assert result == set()

    def test_build_factory_cycle_excludes_session_repos(self, tmp_path: Path) -> None:
        from types import SimpleNamespace
        from driftdriver.factorydrift import build_factory_cycle

        # Build a minimal snapshot with two repos, one with an active session
        repo_a = tmp_path / "repo-a"
        repo_a.mkdir()
        repo_b = tmp_path / "repo-b"
        repo_b.mkdir()

        # Register interactive presence on repo-a
        from driftdriver.actor import Actor
        actor = Actor(id="session-1", actor_class="interactive", name="claude-code")
        write_heartbeat(repo_a, actor)

        snapshot = {
            "repos": [
                {
                    "name": "repo-a",
                    "path": str(repo_a),
                    "stalled": True,
                    "stall_reasons": ["no agents"],
                    "service_running": False,
                    "workgraph_exists": True,
                    "activity_state": "stalled",
                    "in_progress": [],
                    "ready": [{"id": "t1", "title": "Task 1"}],
                    "blocked_open": 0,
                    "task_counts": {"open": 1, "done": 0},
                    "git_dirty": False,
                    "dirty_file_count": 0,
                    "ahead": 0,
                    "security": {"at_risk": False, "critical": 0, "high": 0},
                    "quality": {"quality_score": 90, "findings_total": 0},
                },
                {
                    "name": "repo-b",
                    "path": str(repo_b),
                    "stalled": True,
                    "stall_reasons": ["no agents"],
                    "service_running": False,
                    "workgraph_exists": True,
                    "activity_state": "stalled",
                    "in_progress": [],
                    "ready": [{"id": "t2", "title": "Task 2"}],
                    "blocked_open": 0,
                    "task_counts": {"open": 1, "done": 0},
                    "git_dirty": False,
                    "dirty_file_count": 0,
                    "ahead": 0,
                    "security": {"at_risk": False, "critical": 0, "high": 0},
                    "quality": {"quality_score": 90, "findings_total": 0},
                },
            ],
        }

        policy = SimpleNamespace(
            factory={
                "enabled": True,
                "plan_only": True,
                "max_repos_per_cycle": 5,
                "max_actions_per_cycle": 12,
            },
            secdrift={},
            qadrift={},
            sessiondriver={},
            plandrift={},
            model={},
        )

        cycle = build_factory_cycle(
            snapshot=snapshot,
            policy=policy,
            project_name="test",
        )

        # repo-a should be excluded (has interactive session)
        action_repos = {str(a.get("repo") or "") for a in (cycle.get("action_plan") or [])}
        assert "repo-a" not in action_repos
