# ABOUTME: Tests for cross-repo dispatch extensions in pm_coordination.py
# ABOUTME: Covers peer annotation scanning, IPC dispatch, and task polling

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from driftdriver.pm_coordination import (
    PeerAssignment,
    dispatch_to_peer,
    plan_peer_dispatch,
    poll_peer_task,
)
from driftdriver.peer_registry import PeerInfo, PeerRegistry


class _FakePeerRegistry:
    """Minimal stand-in for PeerRegistry that avoids subprocess calls."""

    def __init__(self, peers: list[PeerInfo], sockets: dict[str, str] | None = None) -> None:
        self._peers = peers
        self._sockets = sockets or {}

    def peers(self) -> list[PeerInfo]:
        return list(self._peers)

    def socket(self, name: str) -> str | None:
        return self._sockets.get(name)


class _TestIpcServer:
    """Minimal Unix socket server for IPC testing."""

    def __init__(self, socket_path: str, responses: dict) -> None:
        self.socket_path = socket_path
        self.responses = responses
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.socket_path)
        self._sock.listen(5)
        self._sock.settimeout(2.0)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            try:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                if data:
                    request = json.loads(data.decode("utf-8").strip())
                    cmd = request.get("cmd", "")
                    response = self.responses.get(cmd, {"ok": False})
                    conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
            except Exception:
                pass
            finally:
                conn.close()

    def stop(self) -> None:
        self._running = False
        if self._sock:
            self._sock.close()
        if self._thread:
            self._thread.join(timeout=3)


class PlanPeerDispatchTests(unittest.TestCase):
    def test_no_peers_returns_empty(self) -> None:
        registry = _FakePeerRegistry([])
        tasks = [{"id": "t1", "title": "Do thing", "description": "@peer:workgraph build it"}]
        result = plan_peer_dispatch(registry, tasks)
        self.assertEqual(result, [])

    def test_matches_peer_annotation(self) -> None:
        peers = [PeerInfo(name="workgraph", path="/projects/workgraph")]
        registry = _FakePeerRegistry(peers)
        tasks = [
            {"id": "t1", "title": "Build module", "description": "Delegate this @peer:workgraph for engine work"},
            {"id": "t2", "title": "Local task", "description": "No peer annotation here"},
        ]
        result = plan_peer_dispatch(registry, tasks)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], PeerAssignment)
        self.assertEqual(result[0].peer_name, "workgraph")
        self.assertEqual(result[0].task_id, "t1")
        self.assertIn("Build module", result[0].prompt)

    def test_ignores_unknown_peer(self) -> None:
        peers = [PeerInfo(name="workgraph", path="/projects/wg")]
        registry = _FakePeerRegistry(peers)
        tasks = [{"id": "t1", "title": "Task", "description": "@peer:unknown-repo do stuff"}]
        result = plan_peer_dispatch(registry, tasks)
        self.assertEqual(result, [])


class DispatchToPeerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._socket_path = os.path.join(self._tmpdir, "test.sock")
        self._server = _TestIpcServer(self._socket_path, {
            "add_task": {"ok": True, "task_id": "remote-99"},
        })
        self._server.start()

    def tearDown(self) -> None:
        self._server.stop()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        os.rmdir(self._tmpdir)

    def test_dispatch_success(self) -> None:
        registry = _FakePeerRegistry([], sockets={"wg": self._socket_path})
        task = {"id": "local-1", "title": "Build thing", "description": "details"}
        result = dispatch_to_peer(Path("/projects/mine"), "wg", task, registry)
        self.assertEqual(result, "remote-99")

    def test_dispatch_no_socket(self) -> None:
        registry = _FakePeerRegistry([], sockets={})
        task = {"id": "local-1", "title": "Build thing", "description": "details"}
        result = dispatch_to_peer(Path("/projects/mine"), "wg", task, registry)
        self.assertIsNone(result)


class PollPeerTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._socket_path = os.path.join(self._tmpdir, "test.sock")
        self._server = _TestIpcServer(self._socket_path, {
            "query_task": {"ok": True, "task": {"id": "remote-99", "status": "done"}},
        })
        self._server.start()

    def tearDown(self) -> None:
        self._server.stop()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        os.rmdir(self._tmpdir)

    def test_poll_success(self) -> None:
        registry = _FakePeerRegistry([], sockets={"wg": self._socket_path})
        result = poll_peer_task(Path("/projects/mine"), "wg", "remote-99", registry)
        self.assertIsNotNone(result)
        self.assertTrue(result["ok"])
        self.assertEqual(result["task"]["status"], "done")

    def test_poll_no_socket(self) -> None:
        registry = _FakePeerRegistry([], sockets={})
        result = poll_peer_task(Path("/projects/mine"), "wg", "remote-99", registry)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
