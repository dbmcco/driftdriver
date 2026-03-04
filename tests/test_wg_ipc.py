# ABOUTME: Tests for wg_ipc.py - Unix socket IPC client for workgraph daemon
# ABOUTME: Uses real Unix sockets via socketserver in test thread, no mocks

from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import unittest

from driftdriver.wg_ipc import (
    IpcError,
    IpcResponse,
    add_task,
    get_service_status,
    notify_graph_changed,
    query_task,
    send_heartbeat,
    send_ipc,
)


class _TestServer:
    """Minimal Unix socket server that echoes JSON responses for testing."""

    def __init__(self, socket_path: str, responses: dict | None = None) -> None:
        self.socket_path = socket_path
        self.responses = responses or {}
        self._server_sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self.socket_path)
        self._server_sock.listen(5)
        self._server_sock.settimeout(2.0)
        self._running = True
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while self._running:
            try:
                conn, _ = self._server_sock.accept()
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
                    response = self.responses.get(cmd, {"ok": True, "cmd": cmd})
                    conn.sendall((json.dumps(response) + "\n").encode("utf-8"))
            except Exception:
                pass
            finally:
                conn.close()

    def stop(self) -> None:
        self._running = False
        if self._server_sock:
            self._server_sock.close()
        if self._thread:
            self._thread.join(timeout=3)


class SendIpcTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._socket_path = os.path.join(self._tmpdir, "test.sock")

    def tearDown(self) -> None:
        if hasattr(self, "_server"):
            self._server.stop()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        os.rmdir(self._tmpdir)

    def test_send_ipc_connection_refused(self) -> None:
        with self.assertRaises(IpcError) as ctx:
            send_ipc("/tmp/nonexistent-test-socket.sock", {"cmd": "status"})
        self.assertIn("connection failed", str(ctx.exception))

    def test_send_ipc_timeout(self) -> None:
        # Create a server that accepts but never responds
        server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server_sock.bind(self._socket_path)
        server_sock.listen(1)

        def accept_and_hang():
            conn, _ = server_sock.accept()
            import time
            time.sleep(5)
            conn.close()

        t = threading.Thread(target=accept_and_hang, daemon=True)
        t.start()

        with self.assertRaises(IpcError) as ctx:
            send_ipc(self._socket_path, {"cmd": "status"}, timeout=0.5)
        self.assertIn("timeout", str(ctx.exception))

        server_sock.close()
        t.join(timeout=1)


class QueryTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._socket_path = os.path.join(self._tmpdir, "test.sock")
        self._server = _TestServer(self._socket_path, {
            "query_task": {"ok": True, "task": {"id": "task-1", "title": "Test Task", "status": "open"}},
        })
        self._server.start()

    def tearDown(self) -> None:
        self._server.stop()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        os.rmdir(self._tmpdir)

    def test_query_task_success(self) -> None:
        result = query_task(self._socket_path, "task-1")
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["task"]["id"], "task-1")


class AddTaskTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._socket_path = os.path.join(self._tmpdir, "test.sock")
        self._server = _TestServer(self._socket_path, {
            "add_task": {"ok": True, "task_id": "new-42"},
        })
        self._server.start()

    def tearDown(self) -> None:
        self._server.stop()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        os.rmdir(self._tmpdir)

    def test_add_task_returns_id(self) -> None:
        task_id = add_task(
            self._socket_path,
            title="New Task",
            description="Do the thing",
            tags=["federation"],
            origin="peer:workgraph",
        )
        self.assertEqual(task_id, "new-42")


class HeartbeatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._socket_path = os.path.join(self._tmpdir, "test.sock")
        self._server = _TestServer(self._socket_path, {
            "heartbeat": {"ok": True},
        })
        self._server.start()

    def tearDown(self) -> None:
        self._server.stop()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        os.rmdir(self._tmpdir)

    def test_heartbeat_success(self) -> None:
        result = send_heartbeat(self._socket_path, "agent-1")
        self.assertTrue(result)


class GraphChangedTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._socket_path = os.path.join(self._tmpdir, "test.sock")
        self._server = _TestServer(self._socket_path, {
            "graph_changed": {"ok": True},
        })
        self._server.start()

    def tearDown(self) -> None:
        self._server.stop()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        os.rmdir(self._tmpdir)

    def test_graph_changed_success(self) -> None:
        result = notify_graph_changed(self._socket_path)
        self.assertTrue(result)


class ServiceStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp()
        self._socket_path = os.path.join(self._tmpdir, "test.sock")
        self._server = _TestServer(self._socket_path, {
            "status": {"ok": True, "uptime": 3600, "tasks_dispatched": 12},
        })
        self._server.start()

    def tearDown(self) -> None:
        self._server.stop()
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)
        os.rmdir(self._tmpdir)

    def test_service_status(self) -> None:
        result = get_service_status(self._socket_path)
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["uptime"], 3600)


class IpcResponseDataclassTests(unittest.TestCase):
    def test_ipc_response_defaults(self) -> None:
        resp = IpcResponse(ok=True)
        self.assertTrue(resp.ok)
        self.assertEqual(resp.data, {})
        self.assertEqual(resp.error, "")

    def test_ipc_response_with_error(self) -> None:
        resp = IpcResponse(ok=False, error="something broke")
        self.assertFalse(resp.ok)
        self.assertEqual(resp.error, "something broke")


if __name__ == "__main__":
    unittest.main()
