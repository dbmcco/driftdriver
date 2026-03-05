# ABOUTME: Tests for peer_registry.py - workgraph peer discovery and health checking
# ABOUTME: Covers discover_peers, get_peer_detail, cache TTL, health checks, socket paths

from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.peer_registry import (
    HealthReport,
    PeerInfo,
    PeerRegistry,
    auto_discover_sibling_peers,
    check_peer_health,
    discover_peers,
    get_peer_detail,
    get_peer_socket,
    register_peer,
)


def _make_subprocess_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> object:
    """Create a mock-like subprocess result."""
    class Result:
        pass
    r = Result()
    r.returncode = returncode
    r.stdout = stdout
    r.stderr = stderr
    return r


class DiscoverPeersTests(unittest.TestCase):
    @patch("driftdriver.peer_registry.subprocess.run")
    def test_discover_peers_empty(self, mock_run) -> None:
        mock_run.return_value = _make_subprocess_result(stdout="[]")
        peers = discover_peers(Path("/tmp/fake"))
        self.assertEqual(peers, [])

    @patch("driftdriver.peer_registry.subprocess.run")
    def test_discover_peers_populated(self, mock_run) -> None:
        data = [
            {"name": "workgraph", "path": "/projects/workgraph", "description": "core engine", "service_running": True, "socket_path": "/tmp/wg.sock", "pid": 1234, "task_counts": {"open": 3, "done": 7}},
            {"name": "beads", "path": "/projects/beads", "description": "task tracker"},
        ]
        mock_run.return_value = _make_subprocess_result(stdout=json.dumps(data))
        peers = discover_peers(Path("/tmp/fake"))
        self.assertEqual(len(peers), 2)
        self.assertEqual(peers[0].name, "workgraph")
        self.assertEqual(peers[0].path, "/projects/workgraph")
        self.assertTrue(peers[0].service_running)
        self.assertEqual(peers[0].socket_path, "/tmp/wg.sock")
        self.assertEqual(peers[0].pid, 1234)
        self.assertEqual(peers[0].task_counts, {"open": 3, "done": 7})
        self.assertEqual(peers[1].name, "beads")
        self.assertFalse(peers[1].service_running)

    @patch("driftdriver.peer_registry.subprocess.run")
    def test_discover_peers_command_failure(self, mock_run) -> None:
        mock_run.return_value = _make_subprocess_result(returncode=1, stderr="not found")
        peers = discover_peers(Path("/tmp/fake"))
        self.assertEqual(peers, [])

    @patch("driftdriver.peer_registry.subprocess.run")
    def test_discover_peers_invalid_json(self, mock_run) -> None:
        mock_run.return_value = _make_subprocess_result(stdout="not json")
        peers = discover_peers(Path("/tmp/fake"))
        self.assertEqual(peers, [])


class GetPeerDetailTests(unittest.TestCase):
    @patch("driftdriver.peer_registry.subprocess.run")
    def test_get_peer_detail_not_found(self, mock_run) -> None:
        mock_run.return_value = _make_subprocess_result(returncode=1, stderr="peer not found")
        result = get_peer_detail(Path("/tmp/fake"), "nonexistent")
        self.assertIsNone(result)

    @patch("driftdriver.peer_registry.subprocess.run")
    def test_get_peer_detail_success(self, mock_run) -> None:
        data = {"name": "workgraph", "path": "/projects/wg", "description": "engine", "service_running": True, "socket_path": "/tmp/wg.sock"}
        mock_run.return_value = _make_subprocess_result(stdout=json.dumps(data))
        result = get_peer_detail(Path("/tmp/fake"), "workgraph")
        self.assertIsNotNone(result)
        self.assertEqual(result.name, "workgraph")
        self.assertTrue(result.service_running)


class GetPeerSocketTests(unittest.TestCase):
    @patch("driftdriver.peer_registry.subprocess.run")
    def test_get_peer_socket_from_detail(self, mock_run) -> None:
        data = {"name": "wg", "path": "/projects/wg", "socket_path": "/tmp/explicit.sock"}
        mock_run.return_value = _make_subprocess_result(stdout=json.dumps(data))
        result = get_peer_socket(Path("/tmp/fake"), "wg")
        self.assertEqual(result, "/tmp/explicit.sock")

    @patch("driftdriver.peer_registry.subprocess.run")
    def test_get_peer_socket_fallback_convention(self, mock_run) -> None:
        data = {"name": "wg", "path": "/projects/wg", "socket_path": ""}
        mock_run.return_value = _make_subprocess_result(stdout=json.dumps(data))
        result = get_peer_socket(Path("/tmp/fake"), "wg")
        self.assertEqual(result, "/projects/wg/.workgraph/service/daemon.sock")

    @patch("driftdriver.peer_registry.subprocess.run")
    def test_get_peer_socket_not_found(self, mock_run) -> None:
        mock_run.return_value = _make_subprocess_result(returncode=1)
        result = get_peer_socket(Path("/tmp/fake"), "gone")
        self.assertIsNone(result)


class PeerRegistryCacheTests(unittest.TestCase):
    @patch("driftdriver.peer_registry.discover_peers")
    def test_cache_ttl_returns_cached(self, mock_discover) -> None:
        mock_discover.return_value = [PeerInfo(name="a", path="/a")]
        registry = PeerRegistry(Path("/tmp"), cache_ttl=30.0)

        # First call populates cache
        peers1 = registry.peers()
        self.assertEqual(len(peers1), 1)
        self.assertEqual(mock_discover.call_count, 1)

        # Second call uses cache
        peers2 = registry.peers()
        self.assertEqual(len(peers2), 1)
        self.assertEqual(mock_discover.call_count, 1)

    @patch("driftdriver.peer_registry.discover_peers")
    def test_cache_invalidate_forces_refresh(self, mock_discover) -> None:
        mock_discover.return_value = [PeerInfo(name="a", path="/a")]
        registry = PeerRegistry(Path("/tmp"), cache_ttl=30.0)

        registry.peers()
        self.assertEqual(mock_discover.call_count, 1)

        registry.invalidate()
        registry.peers()
        self.assertEqual(mock_discover.call_count, 2)


class HealthCheckTests(unittest.TestCase):
    @patch("driftdriver.peer_registry.subprocess.run")
    def test_health_check_unreachable_peer(self, mock_run) -> None:
        mock_run.return_value = _make_subprocess_result(returncode=1, stderr="connection refused")
        peer = PeerInfo(name="dead", path="/projects/dead")
        report = check_peer_health(Path("/tmp/fake"), peer)
        self.assertIsInstance(report, HealthReport)
        self.assertFalse(report.reachable)
        self.assertFalse(report.service_running)
        self.assertIn("connection refused", report.error)

    @patch("driftdriver.peer_registry.subprocess.run")
    def test_health_check_healthy_peer(self, mock_run) -> None:
        data = {"name": "wg", "service_running": True, "task_counts": {"open": 2}}
        mock_run.return_value = _make_subprocess_result(stdout=json.dumps(data))
        peer = PeerInfo(name="wg", path="/projects/wg")
        report = check_peer_health(Path("/tmp/fake"), peer)
        self.assertTrue(report.reachable)
        self.assertTrue(report.service_running)
        self.assertEqual(report.task_summary, {"open": 2})
        self.assertGreater(report.latency_ms, 0)


class RegisterPeerTests(unittest.TestCase):
    @patch("driftdriver.peer_registry.subprocess.run")
    def test_register_peer_success(self, mock_run) -> None:
        mock_run.return_value = _make_subprocess_result(returncode=0)
        result = register_peer(Path("/tmp"), "newpeer", "/projects/new", "a new peer")
        self.assertTrue(result)
        call_args = mock_run.call_args[0][0]
        self.assertIn("--description", call_args)

    @patch("driftdriver.peer_registry.subprocess.run")
    def test_register_peer_failure(self, mock_run) -> None:
        mock_run.return_value = _make_subprocess_result(returncode=1)
        result = register_peer(Path("/tmp"), "bad", "/nonexistent")
        self.assertFalse(result)


class AutoDiscoverSiblingPeersTests(unittest.TestCase):
    def _make_project_tree(self, tmp: Path, names: list[str]) -> Path:
        """Create sibling project dirs, each with .workgraph/."""
        parent = tmp / "projects"
        parent.mkdir(parents=True, exist_ok=True)
        for name in names:
            (parent / name / ".workgraph").mkdir(parents=True, exist_ok=True)
        return parent

    @patch("driftdriver.peer_registry.register_peer")
    @patch("driftdriver.peer_registry.discover_peers")
    def test_discovers_siblings_with_workgraph(self, mock_discover, mock_register) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            parent = self._make_project_tree(Path(tmp), ["alpha", "beta", "gamma"])
            project_dir = parent / "alpha"
            mock_discover.return_value = []  # no existing peers
            mock_register.return_value = True
            registered = auto_discover_sibling_peers(project_dir)
            self.assertEqual(set(registered), {"beta", "gamma"})
            self.assertEqual(mock_register.call_count, 2)

    @patch("driftdriver.peer_registry.register_peer")
    @patch("driftdriver.peer_registry.discover_peers")
    def test_skips_self(self, mock_discover, mock_register) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            parent = self._make_project_tree(Path(tmp), ["me", "other"])
            project_dir = parent / "me"
            mock_discover.return_value = []
            mock_register.return_value = True
            registered = auto_discover_sibling_peers(project_dir)
            self.assertNotIn("me", registered)
            self.assertIn("other", registered)

    @patch("driftdriver.peer_registry.register_peer")
    @patch("driftdriver.peer_registry.discover_peers")
    def test_skips_already_registered(self, mock_discover, mock_register) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            parent = self._make_project_tree(Path(tmp), ["alpha", "beta", "gamma"])
            project_dir = parent / "alpha"
            mock_discover.return_value = [PeerInfo(name="beta", path=str(parent / "beta"))]
            mock_register.return_value = True
            registered = auto_discover_sibling_peers(project_dir)
            self.assertEqual(registered, ["gamma"])
            self.assertEqual(mock_register.call_count, 1)

    @patch("driftdriver.peer_registry.register_peer")
    @patch("driftdriver.peer_registry.discover_peers")
    def test_skips_dirs_without_workgraph(self, mock_discover, mock_register) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp) / "projects"
            parent.mkdir()
            (parent / "has_wg" / ".workgraph").mkdir(parents=True)
            (parent / "no_wg").mkdir()  # no .workgraph
            project_dir = parent / "has_wg"
            mock_discover.return_value = []
            registered = auto_discover_sibling_peers(project_dir)
            self.assertEqual(registered, [])

    @patch("driftdriver.peer_registry.register_peer")
    @patch("driftdriver.peer_registry.discover_peers")
    def test_handles_register_failure_gracefully(self, mock_discover, mock_register) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            parent = self._make_project_tree(Path(tmp), ["alpha", "beta"])
            project_dir = parent / "alpha"
            mock_discover.return_value = []
            mock_register.return_value = False  # registration fails
            registered = auto_discover_sibling_peers(project_dir)
            self.assertEqual(registered, [])  # nothing registered successfully

    @patch("driftdriver.peer_registry.register_peer")
    @patch("driftdriver.peer_registry.discover_peers")
    def test_empty_parent_returns_empty(self, mock_discover, mock_register) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "solo"
            project_dir.mkdir()
            (project_dir / ".workgraph").mkdir()
            mock_discover.return_value = []
            registered = auto_discover_sibling_peers(project_dir)
            self.assertEqual(registered, [])


if __name__ == "__main__":
    unittest.main()
