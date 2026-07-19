# ABOUTME: Integration tests for autopilot loop with peer federation and health monitoring
# ABOUTME: Tests peer dispatch planning, dead worker detection mid-loop, and --no-peer-dispatch flag

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from driftdriver.peer_registry import PeerInfo
from driftdriver.project_autopilot import (
    AutopilotConfig,
    AutopilotRun,
    WorkerContext,
    _init_peer_registry,
    _run_health_check,
    _run_peer_dispatch,
    run_autopilot_loop,
)


class _FakePeerRegistry:
    """Stand-in for PeerRegistry for integration tests."""

    def __init__(self, peers: list[PeerInfo], sockets: dict[str, str] | None = None) -> None:
        self._peers = peers
        self._sockets = sockets or {}

    def peers(self) -> list[PeerInfo]:
        return list(self._peers)

    def socket(self, name: str) -> str | None:
        return self._sockets.get(name)


class InitPeerRegistryTests(unittest.TestCase):
    @patch("driftdriver.peer_registry.subprocess.run")
    def test_returns_registry_when_peers_exist(self, mock_run) -> None:
        data = [{"name": "wg", "path": "/wg"}]
        mock_run.return_value = MagicMock(returncode=0, stdout=json.dumps(data))
        result = _init_peer_registry(Path("/tmp"))
        self.assertIsNotNone(result)

    @patch("driftdriver.peer_registry.subprocess.run")
    def test_returns_none_when_no_peers(self, mock_run) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        result = _init_peer_registry(Path("/tmp"))
        self.assertIsNone(result)


class RunPeerDispatchTests(unittest.TestCase):
    @patch("driftdriver.project_autopilot._run_peer_dispatch")
    @patch("driftdriver.project_autopilot._init_peer_registry")
    @patch("driftdriver.project_autopilot.get_ready_tasks")
    @patch("driftdriver.project_autopilot.load_control_state")
    def test_run_loop_denies_before_peer_dispatch_or_ready_selection(
        self, mock_control, mock_ready, mock_registry, mock_peer_dispatch
    ) -> None:
        mock_control.return_value = {
            "mode": "autonomous",
            "lease_owner": "agent-a",
            "lease_active": False,
        }
        run = self._make_run()

        result = run_autopilot_loop(run)

        self.assertEqual(result.completed_tasks, set())
        mock_ready.assert_not_called()
        mock_registry.assert_not_called()
        mock_peer_dispatch.assert_not_called()

    def _make_run(self, dry_run: bool = False) -> AutopilotRun:
        config = AutopilotConfig(project_dir=Path("/tmp/proj"), dry_run=dry_run)
        return AutopilotRun(config=config)

    @patch("driftdriver.project_autopilot.discover_session_driver", return_value=None)
    def test_run_loop_denies_before_peer_dispatch_when_lease_revoked(self, _session_driver) -> None:
        active = {
            "mode": "autonomous",
            "lease_owner": "agent-a",
            "lease_active": True,
        }
        denied = {
            "mode": "autonomous",
            "lease_owner": "agent-a",
            "lease_active": False,
        }
        run = self._make_run()
        registry = _FakePeerRegistry([PeerInfo(name="wg", path="/wg")])
        tasks = [{"id": "t1", "title": "Task", "description": "@peer:wg do stuff"}]

        with (
            patch(
                "driftdriver.project_autopilot.load_control_state",
                side_effect=[active, active, denied],
            ) as mock_control,
            patch(
                "driftdriver.project_autopilot._init_peer_registry",
                return_value=registry,
            ),
            patch(
                "driftdriver.project_autopilot.get_ready_tasks",
                return_value=tasks,
            ),
            patch("driftdriver.project_autopilot._run_peer_dispatch") as mock_peer_dispatch,
        ):
            result = run_autopilot_loop(run)

        self.assertEqual(mock_control.call_count, 3)
        mock_peer_dispatch.assert_not_called()
        self.assertEqual(result.completed_tasks, set())
        self.assertEqual(result.workers, {})

    def test_no_peer_annotations_returns_empty(self) -> None:
        run = self._make_run()
        registry = _FakePeerRegistry([PeerInfo(name="wg", path="/wg")])
        tasks = [{"id": "t1", "title": "Local task", "description": "no peer reference"}]
        dispatched = _run_peer_dispatch(run, tasks, registry)
        self.assertEqual(dispatched, [])

    def test_peer_annotation_dispatches(self) -> None:
        run = self._make_run()
        peers = [PeerInfo(name="workgraph", path="/projects/workgraph")]
        registry = _FakePeerRegistry(peers, sockets={"workgraph": "/tmp/fake.sock"})
        tasks = [{"id": "t1", "title": "Engine task", "description": "@peer:workgraph build engine"}]

        with (
            patch(
                "driftdriver.project_autopilot._dispatch_authority",
                return_value={"enabled": True, "reason": "active lease permits dispatch"},
            ),
            patch("driftdriver.wg_ipc.send_ipc", return_value={"ok": True, "task_id": "remote-1"}),
        ):
            dispatched = _run_peer_dispatch(run, tasks, registry)
            self.assertEqual(dispatched, ["t1"])
            self.assertIn("t1", run.completed_tasks)
            self.assertIn("t1", run.workers)
            self.assertEqual(run.workers["t1"].worker_name, "peer-workgraph-t1")

    def test_peer_dispatch_stops_when_authority_expires_between_assignments(self) -> None:
        run = self._make_run()
        peers = [PeerInfo(name="workgraph", path="/projects/workgraph")]
        registry = _FakePeerRegistry(
            peers,
            sockets={"workgraph": "/tmp/fake.sock"},
        )
        tasks = [
            {"id": "t1", "title": "First", "description": "@peer:workgraph first"},
            {"id": "t2", "title": "Second", "description": "@peer:workgraph second"},
        ]
        permitted = {"enabled": True, "reason": "active lease permits dispatch"}
        denied = {"enabled": False, "reason": "lease is not active"}

        with (
            patch(
                "driftdriver.project_autopilot._dispatch_authority",
                side_effect=[permitted, denied],
            ) as mock_authority,
            patch(
                "driftdriver.project_autopilot._dispatch_to_peer",
                return_value="remote-1",
            ) as mock_dispatch,
        ):
            dispatched = _run_peer_dispatch(run, tasks, registry)

        self.assertEqual(dispatched, ["t1"])
        self.assertEqual(mock_authority.call_count, 2)
        mock_dispatch.assert_called_once()
        self.assertEqual(run.completed_tasks, {"t1"})
        self.assertNotIn("t2", run.workers)

    def test_dry_run_peer_dispatch(self) -> None:
        run = self._make_run(dry_run=True)
        peers = [PeerInfo(name="wg", path="/wg")]
        registry = _FakePeerRegistry(peers)
        tasks = [{"id": "t1", "title": "Task", "description": "@peer:wg do stuff"}]
        dispatched = _run_peer_dispatch(run, tasks, registry)
        self.assertEqual(dispatched, ["t1"])

    def test_failed_dispatch_returns_empty(self) -> None:
        run = self._make_run()
        peers = [PeerInfo(name="wg", path="/wg")]
        registry = _FakePeerRegistry(peers, sockets={"wg": "/tmp/bad.sock"})
        tasks = [{"id": "t1", "title": "Task", "description": "@peer:wg do stuff"}]

        from driftdriver.wg_ipc import IpcError
        with (
            patch(
                "driftdriver.project_autopilot._dispatch_authority",
                return_value={"enabled": True, "reason": "active lease permits dispatch"},
            ),
            patch("driftdriver.wg_ipc.send_ipc", side_effect=IpcError("conn refused")),
        ):
            dispatched = _run_peer_dispatch(run, tasks, registry)
            self.assertEqual(dispatched, [])


class RunHealthCheckTests(unittest.TestCase):
    def test_no_running_workers_is_noop(self) -> None:
        config = AutopilotConfig(project_dir=Path("/tmp"))
        run = AutopilotRun(config=config)
        run.workers["t1"] = WorkerContext(
            task_id="t1", task_title="Done", worker_name="w1", status="completed"
        )
        # Should not raise
        _run_health_check(run)
        self.assertEqual(run.workers["t1"].status, "completed")

    def test_dead_worker_gets_escalated(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            config = AutopilotConfig(project_dir=Path("/tmp"))
            run = AutopilotRun(config=config)

            # Create a running worker with a dead events file
            events_file = Path(tmpdir) / "sess-dead.events.jsonl"
            events_file.write_text(
                json.dumps({"event": "pre_tool_use", "ts": time.time() - 700}) + "\n"
            )

            run.workers["t1"] = WorkerContext(
                task_id="t1",
                task_title="Stalled",
                worker_name="w1",
                session_id="sess-dead",
                status="running",
            )

            with patch("driftdriver.worker_monitor.WORKER_EVENTS_DIR", Path(tmpdir)):
                _run_health_check(run)

            self.assertEqual(run.workers["t1"].status, "escalated")
            self.assertIn("t1", run.escalated_tasks)
        finally:
            for f in Path(tmpdir).iterdir():
                f.unlink()
            os.rmdir(tmpdir)

    def test_alive_worker_not_touched(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            config = AutopilotConfig(project_dir=Path("/tmp"))
            run = AutopilotRun(config=config)

            events_file = Path(tmpdir) / "sess-ok.events.jsonl"
            events_file.write_text(
                json.dumps({"event": "pre_tool_use", "ts": time.time()}) + "\n"
            )

            run.workers["t1"] = WorkerContext(
                task_id="t1",
                task_title="Active",
                worker_name="w1",
                session_id="sess-ok",
                status="running",
            )

            with patch("driftdriver.worker_monitor.WORKER_EVENTS_DIR", Path(tmpdir)):
                _run_health_check(run)

            self.assertEqual(run.workers["t1"].status, "running")
            self.assertNotIn("t1", run.escalated_tasks)
        finally:
            for f in Path(tmpdir).iterdir():
                f.unlink()
            os.rmdir(tmpdir)


class NoPeerDispatchFlagTests(unittest.TestCase):
    def test_config_flag_defaults_false(self) -> None:
        config = AutopilotConfig(project_dir=Path("/tmp"))
        self.assertFalse(config.no_peer_dispatch)

    def test_config_flag_set_true(self) -> None:
        config = AutopilotConfig(project_dir=Path("/tmp"), no_peer_dispatch=True)
        self.assertTrue(config.no_peer_dispatch)

    @patch("driftdriver.project_autopilot._init_peer_registry")
    @patch("driftdriver.project_autopilot.get_ready_tasks", return_value=[])
    @patch("driftdriver.project_autopilot.discover_session_driver", return_value=None)
    @patch(
        "driftdriver.project_autopilot.load_control_state",
        return_value={"mode": "autonomous", "lease_owner": "agent-a", "lease_active": True},
    )
    def test_no_peer_dispatch_skips_registry_init(self, _control, _sd, _ready, mock_init) -> None:
        config = AutopilotConfig(
            project_dir=Path("/tmp"),
            no_peer_dispatch=True,
        )
        from driftdriver.project_autopilot import run_autopilot_loop
        run = AutopilotRun(config=config)
        run_autopilot_loop(run)
        mock_init.assert_not_called()


if __name__ == "__main__":
    unittest.main()
