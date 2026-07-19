# ABOUTME: Tests for the repo-local speedriftd runtime shell
# ABOUTME: Verifies runtime snapshots, ledger files, and CLI-friendly behavior

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from driftdriver.project_autopilot import AutopilotConfig, AutopilotRun, WorkerContext
from driftdriver.speedriftd import (
    collect_runtime_snapshot,
    handle_lease_expiry,
    run_runtime_cycle,
    run_runtime_loop,
)
from driftdriver.speedriftd_state import (
    load_control_state,
    load_lease_expiry_stop,
    load_runtime_snapshot,
    runtime_paths,
    write_control_state,
)


def _write_graph(repo: Path, tasks: list[dict]) -> None:
    wg_dir = repo / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    for task in tasks:
        row = {
            "kind": "task",
            "id": task["id"],
            "title": task.get("title", task["id"]),
            "status": task.get("status", "open"),
        }
        if "after" in task:
            row["after"] = task["after"]
        if "created_at" in task:
            row["created_at"] = task["created_at"]
        rows.append(json.dumps(row))
    (wg_dir / "graph.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")


def _write_run_state(repo: Path, run: AutopilotRun) -> None:
    """Write autopilot run state as JSON (inlined from deleted autopilot_state)."""
    import time
    d = repo / ".workgraph" / ".autopilot"
    d.mkdir(parents=True, exist_ok=True)
    state = {
        "ts": time.time(),
        "goal": run.config.goal,
        "loop_count": run.loop_count,
        "completed_tasks": sorted(run.completed_tasks),
        "failed_tasks": sorted(run.failed_tasks),
        "escalated_tasks": sorted(run.escalated_tasks),
        "started_at": run.started_at,
        "workers": {
            tid: {
                "task_id": ctx.task_id,
                "task_title": ctx.task_title,
                "worker_name": ctx.worker_name,
                "session_id": ctx.session_id,
                "started_at": ctx.started_at,
                "status": ctx.status,
                "drift_fail_count": ctx.drift_fail_count,
                "drift_findings": ctx.drift_findings,
            }
            for tid, ctx in run.workers.items()
        },
    }
    (d / "run-state.json").write_text(json.dumps(state, indent=2))


class SpeedriftdTests(unittest.TestCase):
    def test_collect_runtime_snapshot_uses_saved_session_id_and_ready_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(
                repo,
                [
                    {"id": "done", "title": "Done", "status": "done"},
                    {"id": "ready-1", "title": "Ready", "status": "open", "after": ["done"]},
                    {"id": "impl", "title": "Implement", "status": "in-progress"},
                ],
            )
            config = AutopilotConfig(project_dir=repo, goal="Ship runtime")
            run = AutopilotRun(
                config=config,
                started_at=100.0,
                workers={
                    "impl": WorkerContext(
                        task_id="impl",
                        task_title="Implement",
                        worker_name="ap-impl",
                        session_id="sess-123",
                        started_at=100.0,
                        status="running",
                    )
                },
            )
            _write_run_state(repo, run)

            with patch("driftdriver.dispatch.check_worker_liveness") as fake_health:
                fake_health.return_value.status = "alive"
                fake_health.return_value.last_event_ts = 200.0
                fake_health.return_value.last_event_type = "pre_tool_use"
                fake_health.return_value.event_count = 4
                snapshot = collect_runtime_snapshot(repo)

            self.assertEqual(snapshot["repo"], repo.name)
            self.assertEqual(snapshot["daemon_state"], "running")
            self.assertEqual(snapshot["ready_tasks"][0]["id"], "ready-1")
            self.assertEqual(snapshot["active_workers"][0]["session_id"], "sess-123")
            self.assertEqual(snapshot["active_workers"][0]["runtime"], "claude")
            self.assertEqual(snapshot["active_task_ids"], ["impl"])
            self.assertEqual(snapshot["next_action"], "continue supervision")
            self.assertEqual(snapshot["control"]["mode"], "observe")

    def test_run_runtime_cycle_writes_runtime_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [{"id": "ready-1", "title": "Ready", "status": "open"}])

            snapshot = run_runtime_cycle(repo)
            paths = runtime_paths(repo)

            self.assertEqual(snapshot["daemon_state"], "idle")
            self.assertTrue(paths["current"].exists())
            self.assertTrue(paths["leases"].exists())
            self.assertTrue(paths["events_dir"].exists())
            self.assertTrue(paths["workers"].exists())

            loaded = load_runtime_snapshot(repo)
            self.assertEqual(loaded["repo"], repo.name)
            self.assertEqual(loaded["control"]["mode"], "observe")
            self.assertEqual(loaded["next_action"], "observe mode: ready task ready-1 waiting for explicit supervisor")

    def test_run_runtime_cycle_marks_missing_active_worker_as_stalled(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [{"id": "impl", "title": "Implement", "status": "in-progress"}])
            config = AutopilotConfig(project_dir=repo, goal="Ship runtime")
            run = AutopilotRun(
                config=config,
                workers={
                    "impl": WorkerContext(
                        task_id="impl",
                        task_title="Implement",
                        worker_name="ap-impl",
                        session_id="sess-dead",
                        started_at=100.0,
                        status="running",
                    )
                },
            )
            _write_run_state(repo, run)

            with patch("driftdriver.dispatch.check_worker_liveness") as fake_health:
                fake_health.return_value.status = "dead"
                fake_health.return_value.last_event_ts = 150.0
                fake_health.return_value.last_event_type = "pre_tool_use"
                fake_health.return_value.event_count = 2
                snapshot = run_runtime_cycle(repo)

            self.assertEqual(snapshot["daemon_state"], "stalled")
            self.assertEqual(snapshot["stalled_task_ids"], ["impl"])
            stalls = runtime_paths(repo)["stalls"].read_text(encoding="utf-8")
            self.assertIn("worker_stalled_or_missing", stalls)

    def test_write_control_state_arms_repo_for_autonomous_dispatch(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [{"id": "ready-1", "title": "Ready", "status": "open"}])

            control = write_control_state(
                repo,
                mode="autonomous",
                lease_owner="speedriftd",
                lease_ttl_seconds=120,
                reason="central supervisor armed repo",
            )
            snapshot = run_runtime_cycle(repo)

            self.assertEqual(control["mode"], "autonomous")
            self.assertEqual(control["lease_owner"], "speedriftd")
            self.assertTrue(control["lease_active"])
            self.assertEqual(snapshot["control"]["mode"], "autonomous")
            self.assertEqual(snapshot["next_action"], "dispatch ready task ready-1")

    def test_dispatch_blocked_when_manual_claim_exists(self) -> None:
        """When respect_manual_claims=true and a task is in-progress without a
        matching active worker, dispatch should be suppressed."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(
                repo,
                [
                    {"id": "manual-task", "title": "Human working", "status": "in-progress"},
                    {"id": "ready-task", "title": "Waiting", "status": "open"},
                ],
            )
            # Arm repo for autonomous dispatch (would normally dispatch ready-task)
            write_control_state(
                repo,
                mode="autonomous",
                lease_owner="speedriftd",
                lease_ttl_seconds=120,
                reason="test",
            )
            snapshot = collect_runtime_snapshot(repo)

            # manual-task is in-progress but has no active worker -> manual claim
            self.assertEqual(snapshot["manual_claim_ids"], ["manual-task"])
            self.assertTrue(snapshot["dispatch_blocked_by_manual"])
            self.assertIn("manual claim", snapshot["next_action"])
            self.assertIn("respect_manual_claims=true", snapshot["next_action"])

    def test_dispatch_allowed_when_respect_manual_claims_disabled(self) -> None:
        """When respect_manual_claims=false, manual claims don't block dispatch."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(
                repo,
                [
                    {"id": "manual-task", "title": "Human working", "status": "in-progress"},
                    {"id": "ready-task", "title": "Waiting", "status": "open"},
                ],
            )
            write_control_state(
                repo,
                mode="autonomous",
                lease_owner="speedriftd",
                lease_ttl_seconds=120,
                reason="test",
            )
            # Override policy to disable respect_manual_claims
            from driftdriver.policy import load_drift_policy
            policy = load_drift_policy(repo / ".workgraph")
            policy.speedriftd["respect_manual_claims"] = False

            snapshot = collect_runtime_snapshot(repo, policy=policy)

            self.assertFalse(snapshot["dispatch_blocked_by_manual"])
            self.assertEqual(snapshot["next_action"], "dispatch ready task ready-task")

    def test_no_manual_claims_when_worker_matches_in_progress(self) -> None:
        """In-progress tasks with matching active workers are NOT manual claims."""
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(
                repo,
                [
                    {"id": "impl", "title": "Implement", "status": "in-progress"},
                    {"id": "ready-task", "title": "Waiting", "status": "open"},
                ],
            )
            config = AutopilotConfig(project_dir=repo, goal="test")
            run = AutopilotRun(
                config=config,
                workers={
                    "impl": WorkerContext(
                        task_id="impl",
                        task_title="Implement",
                        worker_name="ap-impl",
                        session_id="sess-456",
                        started_at=100.0,
                        status="running",
                    )
                },
            )
            _write_run_state(repo, run)
            write_control_state(
                repo,
                mode="autonomous",
                lease_owner="speedriftd",
                lease_ttl_seconds=120,
                reason="test",
            )

            with patch("driftdriver.dispatch.check_worker_liveness") as fake_health:
                fake_health.return_value.status = "alive"
                fake_health.return_value.last_event_ts = 200.0
                fake_health.return_value.last_event_type = "pre_tool_use"
                fake_health.return_value.event_count = 4
                snapshot = collect_runtime_snapshot(repo)

            # impl has a matching active worker, so it's NOT a manual claim
            self.assertEqual(snapshot["manual_claim_ids"], [])
            self.assertFalse(snapshot["dispatch_blocked_by_manual"])
            self.assertEqual(snapshot["next_action"], "continue supervision")

    def test_run_runtime_loop_respects_max_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])

            with patch("driftdriver.speedriftd.time.sleep") as fake_sleep:
                result = run_runtime_loop(repo, interval_seconds=1, max_cycles=2)

            self.assertEqual(result["cycles_completed"], 2)
            self.assertEqual(fake_sleep.call_count, 1)


def _expire_active_lease(repo: Path) -> None:
    """Rewrite control.json so a previously-active lease is now expired."""
    control_path = runtime_paths(repo)["control"]
    data = json.loads(control_path.read_text())
    past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    data["lease_expires_at"] = past
    data["lease_acquired_at"] = past
    data["lease_active"] = False
    control_path.write_text(json.dumps(data), encoding="utf-8")


class LeaseExpiryStopTests(unittest.TestCase):
    """An already-running coordinator must be stopped exactly once when a
    previously-active elevated lease expires; repeated cycles must not
    duplicate the stop/revoke event."""

    def setUp(self) -> None:
        # Isolate from the real ``wg ready`` subprocess so tests assert only on
        # the lease-expiry stop path (and stay fast / hermetic).
        patcher = patch("driftdriver.speedriftd.get_ready_tasks", return_value=[])
        patcher.start()
        self.addCleanup(patcher.stop)

    def _arm_then_expire(self, repo: Path, *, mode: str = "autonomous") -> None:
        write_control_state(
            repo,
            mode=mode,
            lease_owner="speedriftd",
            lease_ttl_seconds=120,
            reason="armed for dispatch",
        )
        # Persist the active runtime snapshot before expiring the lease.
        run_runtime_cycle(repo)
        _expire_active_lease(repo)

    def test_no_prior_snapshot_does_not_stop_preexisting_expired_lease(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)
            # Remove the active snapshot: this is an expired lease on the
            # first observed runtime cycle, not an active-to-expired transition.
            runtime_paths(repo)["current"].unlink()

            with patch("driftdriver.speedriftd._stop_workgraph_service") as stop:
                snapshot = run_runtime_cycle(repo)

            self.assertNotIn("last_lease_expiry_stop", snapshot)
            stop.assert_not_called()

    def test_natural_ttl_expiry_stops_without_control_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            write_control_state(
                repo,
                mode="autonomous",
                lease_owner="speedriftd",
                lease_ttl_seconds=1,
                reason="natural expiry regression",
            )

            control_path = runtime_paths(repo)["control"]
            with patch("driftdriver.speedriftd.get_ready_tasks", return_value=[]):
                first = run_runtime_cycle(repo)
                self.assertTrue(first["control"]["lease_active"])
                control_before = control_path.read_bytes()
                future = datetime.now(timezone.utc).timestamp() + 2
                with (
                    patch("driftdriver.speedriftd_state.time.time", return_value=future),
                    patch("driftdriver.speedriftd._stop_workgraph_service") as stop,
                ):
                    second = run_runtime_cycle(repo)

            self.assertIn("last_lease_expiry_stop", second)
            stop.assert_called_once_with(repo)
            control_after = json.loads(control_path.read_text(encoding="utf-8"))
            control_before_data = json.loads(control_before)
            self.assertEqual(control_after["lease_expires_at"], control_before_data["lease_expires_at"])

    def test_cycle_stops_coordinator_once_on_lease_expiry(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)

            with patch("driftdriver.speedriftd.subprocess.run") as fake_run:
                fake_run.return_value = SimpleNamespace(
                    returncode=0, stdout="stopped", stderr=""
                )
                snapshot = run_runtime_cycle(repo)

            # Exactly one service stop issued.
            stop_calls = [
                c for c in fake_run.call_args_list
                if "service" in (c.args[0] if c.args else [None]) and "stop" in (c.args[0] if c.args else [])
            ]
            self.assertEqual(len(stop_calls), 1)
            # Terminal evidence persisted and surfaced on the snapshot.
            self.assertIn("last_lease_expiry_stop", snapshot)
            marker = load_lease_expiry_stop(repo)
            self.assertEqual(marker["reason"], "expired_lease")
            self.assertEqual(marker["mode"], "autonomous")
            self.assertEqual(marker["stop_exit_code"], 0)

    def test_repeated_cycles_do_not_duplicate_stop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)

            with patch("driftdriver.speedriftd.subprocess.run") as fake_run:
                fake_run.return_value = SimpleNamespace(
                    returncode=0, stdout="stopped", stderr=""
                )
                first = run_runtime_cycle(repo)
                second = run_runtime_cycle(repo)
                third = run_runtime_cycle(repo)

            self.assertIn("last_lease_expiry_stop", first)
            # Subsequent cycles detect no new expiry transition.
            self.assertNotIn("last_lease_expiry_stop", second)
            self.assertNotIn("last_lease_expiry_stop", third)
            stop_calls = [
                c for c in fake_run.call_args_list
                if "stop" in (c.args[0] if c.args else [])
            ]
            self.assertEqual(len(stop_calls), 1)

    def test_concurrent_runtime_cycles_stop_coordinator_once(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)
            active = {"mode": "autonomous", "lease_owner": "speedriftd", "lease_active": True}
            expired = {
                "mode": "autonomous",
                "lease_owner": "speedriftd",
                "lease_active": False,
                "lease_ttl_seconds": 120,
                "lease_acquired_at": "2024-01-01T00:00:00+00:00",
                "lease_expires_at": "2024-01-01T00:01:00+00:00",
            }
            entered = threading.Barrier(2)
            stop_started = threading.Event()
            release_stop = threading.Event()
            calls: list[Path] = []

            def stop(repo_path: Path) -> dict[str, object]:
                calls.append(repo_path)
                stop_started.set()
                self.assertTrue(release_stop.wait(timeout=2))
                return {"exit_code": 0, "stdout": "stopped", "stderr": ""}

            def cycle() -> None:
                entered.wait(timeout=2)
                run_runtime_cycle(repo)

            with (
                patch("driftdriver.speedriftd.load_runtime_snapshot", return_value={"control": active}),
                patch("driftdriver.speedriftd.collect_runtime_snapshot", return_value={"control": expired}),
                patch("driftdriver.speedriftd.load_control_state", return_value=expired),
                patch("driftdriver.speedriftd.write_runtime_snapshot", side_effect=lambda _repo, snapshot: snapshot),
                patch("driftdriver.speedriftd._stop_workgraph_service", side_effect=stop),
            ):
                threads = [threading.Thread(target=cycle) for _ in range(2)]
                for thread in threads:
                    thread.start()
                self.assertTrue(stop_started.wait(timeout=2))
                # Both cycles have started, but the second cannot pass the
                # inter-process critical section while the first stop is held.
                self.assertEqual(len(calls), 1)
                release_stop.set()
                for thread in threads:
                    thread.join(timeout=2)
                    self.assertFalse(thread.is_alive())

            self.assertEqual(calls, [repo])
            events = [
                json.loads(line)
                for event_file in runtime_paths(repo)["events_dir"].glob("*.jsonl")
                for line in event_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(
                sum(event.get("event_type") == "coordinator_stopped_on_lease_expiry" for event in events),
                1,
            )

    def test_rearmed_lease_between_collection_and_locked_decision_is_not_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)

            def rearm() -> None:
                write_control_state(
                    repo,
                    mode="autonomous",
                    lease_owner="speedriftd-rearmed",
                    lease_ttl_seconds=120,
                    reason="re-armed during expiry race",
                )

            with (
                patch("driftdriver.speedriftd._lease_expiry_lock") as expiry_lock,
                patch("driftdriver.speedriftd._stop_workgraph_service") as stop,
                patch(
                    "driftdriver.speedriftd.write_runtime_snapshot",
                    side_effect=lambda _repo, snapshot: snapshot,
                ),
            ):
                expiry_lock.return_value.__enter__.side_effect = rearm
                snapshot = run_runtime_cycle(repo)

            self.assertNotIn("last_lease_expiry_stop", snapshot)
            stop.assert_not_called()
            self.assertTrue(load_control_state(repo)["lease_active"])

    def test_rearm_waits_until_expiry_stop_decision_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)
            stop_started = threading.Event()
            rearm_completed = threading.Event()

            def rearm() -> None:
                stop_started.wait(timeout=2)
                write_control_state(
                    repo,
                    mode="autonomous",
                    lease_owner="speedriftd-rearmed",
                    lease_ttl_seconds=120,
                    reason="re-armed during stop decision",
                )
                rearm_completed.set()

            rearm_thread = threading.Thread(target=rearm)
            rearm_thread.start()

            def stop(_repo: Path) -> dict[str, object]:
                stop_started.set()
                self.assertFalse(rearm_completed.wait(timeout=0.2))
                return {"exit_code": 0, "stdout": "stopped", "stderr": ""}

            try:
                with patch("driftdriver.speedriftd._stop_workgraph_service", side_effect=stop):
                    run_runtime_cycle(repo)
            finally:
                rearm_thread.join(timeout=2)

            self.assertFalse(rearm_thread.is_alive())
            self.assertTrue(rearm_completed.is_set())
            self.assertEqual(load_control_state(repo)["lease_owner"], "speedriftd-rearmed")

    def test_interrupted_stop_reservation_is_reconciled_without_duplicate_stop(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)
            stop_result = {"exit_code": 0, "stdout": "stopped", "stderr": ""}

            with patch(
                "driftdriver.speedriftd._stop_workgraph_service",
                return_value=stop_result,
            ) as stop:
                with patch(
                    "driftdriver.speedriftd_state.record_lease_expiry_stop",
                    side_effect=RuntimeError("crash after stop"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "crash after stop"):
                        run_runtime_cycle(repo)

                second = run_runtime_cycle(repo)

            self.assertEqual(stop.call_count, 2)
            self.assertEqual(second["last_lease_expiry_stop"]["stop_exit_code"], 0)
            self.assertTrue(load_lease_expiry_stop(repo)["reconciled"])

    def test_interrupted_stop_reconciles_stopped_status_without_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)
            stop_result = {"exit_code": 0, "stdout": "stopped", "stderr": ""}

            with patch(
                "driftdriver.speedriftd._stop_workgraph_service",
                return_value=stop_result,
            ) as stop:
                with patch(
                    "driftdriver.speedriftd_state.record_lease_expiry_stop",
                    side_effect=RuntimeError("crash after stop"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "crash after stop"):
                        run_runtime_cycle(repo)

                with patch(
                    "driftdriver.speedriftd._workgraph_service_running",
                    return_value=False,
                    create=True,
                ):
                    second = run_runtime_cycle(repo)

            self.assertEqual(stop.call_count, 1)
            self.assertEqual(second["last_lease_expiry_stop"]["stop_exit_code"], 0)
            self.assertTrue(load_lease_expiry_stop(repo)["reconciled"])

    def test_stop_failure_is_recorded_as_terminal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)

            with patch(
                "driftdriver.speedriftd._stop_workgraph_service",
                return_value={"exit_code": 7, "stdout": "", "stderr": "stop failed"},
            ) as stop:
                snapshot = run_runtime_cycle(repo)

            stop.assert_called_once_with(repo)
            self.assertEqual(snapshot["last_lease_expiry_stop"]["stop_exit_code"], 7)
            # Failed stops retain evidence in the runtime snapshot/event but
            # must not reserve the lease identity for future retries.
            self.assertEqual(load_lease_expiry_stop(repo), {})
            events = [
                json.loads(line)
                for event_file in runtime_paths(repo)["events_dir"].glob("*.jsonl")
                for line in event_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            failures = [
                event for event in events
                if event.get("event_type") == "coordinator_stop_failed_on_lease_expiry"
            ]
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["payload"]["stop_stderr"], "stop failed")
            self.assertFalse(failures[0]["payload"]["stop_succeeded"])

    def test_failed_stop_is_retried_until_success(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)

            with patch(
                "driftdriver.speedriftd._stop_workgraph_service",
                side_effect=[
                    {"exit_code": 7, "stdout": "", "stderr": "stop failed"},
                    {"exit_code": 0, "stdout": "stopped", "stderr": ""},
                ],
            ) as stop:
                first = run_runtime_cycle(repo)
                second = run_runtime_cycle(repo)

            self.assertEqual(stop.call_count, 2)
            self.assertEqual(first["last_lease_expiry_stop"]["stop_exit_code"], 7)
            self.assertEqual(second["last_lease_expiry_stop"]["stop_exit_code"], 0)
            self.assertEqual(load_lease_expiry_stop(repo)["stop_exit_code"], 0)

    def test_stop_exception_is_recorded_as_terminal_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)

            with patch(
                "driftdriver.speedriftd.subprocess.run",
                side_effect=OSError("wg unavailable"),
            ):
                snapshot = run_runtime_cycle(repo)

            self.assertIsNone(snapshot["last_lease_expiry_stop"]["stop_exit_code"])
            self.assertEqual(load_lease_expiry_stop(repo), {})
            events = [
                json.loads(line)
                for event_file in runtime_paths(repo)["events_dir"].glob("*.jsonl")
                for line in event_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            failure = next(
                event for event in events
                if event.get("event_type") == "coordinator_stop_failed_on_lease_expiry"
            )
            self.assertIsNone(failure["payload"]["stop_exit_code"])
            self.assertEqual(failure["payload"]["stop_stderr"], "wg unavailable")
            self.assertFalse(failure["payload"]["stop_succeeded"])

    def test_active_to_released_lease_stops_coordinator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo)
            write_control_state(
                repo,
                mode="autonomous",
                release_lease=True,
                reason="operator released lease",
            )

            with patch(
                "driftdriver.speedriftd._stop_workgraph_service",
                return_value={"exit_code": 0, "stdout": "stopped", "stderr": ""},
            ) as stop:
                snapshot = run_runtime_cycle(repo)

            stop.assert_called_once_with(repo)
            self.assertEqual(snapshot["last_lease_expiry_stop"]["reason"], "released_lease")
            self.assertEqual(snapshot["last_lease_expiry_stop"]["lease_owner"], "speedriftd")

    def test_released_lease_stop_failure_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            write_control_state(
                repo,
                mode="autonomous",
                lease_owner="speedriftd",
                lease_ttl_seconds=120,
                reason="armed",
            )
            run_runtime_cycle(repo)
            write_control_state(repo, mode="autonomous", release_lease=True, reason="released")

            with patch(
                "driftdriver.speedriftd._stop_workgraph_service",
                side_effect=[
                    {"exit_code": 7, "stdout": "", "stderr": "stop failed"},
                    {"exit_code": 0, "stdout": "stopped", "stderr": ""},
                ],
            ) as stop:
                first = run_runtime_cycle(repo)
                second = run_runtime_cycle(repo)

            self.assertEqual(stop.call_count, 2)
            self.assertEqual(first["last_lease_expiry_stop"]["stop_exit_code"], 7)
            self.assertEqual(second["last_lease_expiry_stop"]["stop_exit_code"], 0)

    def test_active_to_manual_or_observe_does_not_stop_coordinator(self) -> None:
        for mode in ("manual", "observe"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as td:
                repo = Path(td)
                _write_graph(repo, [])
                write_control_state(
                    repo,
                    mode="autonomous",
                    lease_owner="speedriftd",
                    lease_ttl_seconds=120,
                    reason="active",
                )
                run_runtime_cycle(repo)
                write_control_state(repo, mode=mode, reason=f"switched to {mode}")

                with patch("driftdriver.speedriftd._stop_workgraph_service") as stop:
                    snapshot = run_runtime_cycle(repo)

                self.assertNotIn("last_lease_expiry_stop", snapshot)
                stop.assert_not_called()

    def test_active_lease_does_not_stop_coordinator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            write_control_state(
                repo,
                mode="autonomous",
                lease_owner="speedriftd",
                lease_ttl_seconds=3600,
                reason="active",
            )

            with patch("driftdriver.speedriftd.subprocess.run") as fake_run:
                snapshot = run_runtime_cycle(repo)

            self.assertNotIn("last_lease_expiry_stop", snapshot)
            fake_run.assert_not_called()

    def test_observe_mode_does_not_stop_coordinator(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            # No lease at all -> observe default.

            with patch("driftdriver.speedriftd.subprocess.run") as fake_run:
                snapshot = run_runtime_cycle(repo)

            self.assertNotIn("last_lease_expiry_stop", snapshot)
            fake_run.assert_not_called()

    def test_new_expired_lease_stops_again_after_renewal(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            self._arm_then_expire(repo, mode="supervise")

            with patch("driftdriver.speedriftd.subprocess.run") as fake_run:
                fake_run.return_value = SimpleNamespace(
                    returncode=0, stdout="stopped", stderr=""
                )
                first = run_runtime_cycle(repo)
                # A fresh lease is acquired and then expires again.
                write_control_state(
                    repo,
                    mode="supervise",
                    lease_owner="speedriftd-2",
                    lease_ttl_seconds=120,
                    reason="re-armed",
                )
                run_runtime_cycle(repo)
                _expire_active_lease(repo)
                second = run_runtime_cycle(repo)

            self.assertIn("last_lease_expiry_stop", first)
            self.assertIn("last_lease_expiry_stop", second)
            stop_calls = [
                c for c in fake_run.call_args_list
                if "stop" in (c.args[0] if c.args else [])
            ]
            self.assertEqual(len(stop_calls), 2)

    def test_handle_lease_expiry_uses_persisted_previous_lease_state(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            expired = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
            previous = {
                "mode": "autonomous",
                "lease_owner": "supervisor",
                "lease_ttl_seconds": 60,
                "lease_expires_at": expired,
                "lease_active": True,
            }
            current = {
                "mode": "autonomous",
                "lease_owner": "supervisor",
                "lease_ttl_seconds": 60,
                "lease_expires_at": expired,
                "lease_active": False,
            }
            with (
                patch("driftdriver.speedriftd.load_control_state", return_value=current),
                patch("driftdriver.speedriftd._stop_workgraph_service") as stop,
            ):
                handle_lease_expiry(repo, control=current, previous_control=previous)

            stop.assert_called_once_with(repo)

    def test_handle_lease_expiry_returns_none_without_decision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _write_graph(repo, [])
            result = handle_lease_expiry(repo, control={"mode": "observe"})
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
