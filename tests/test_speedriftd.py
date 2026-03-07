# ABOUTME: Tests for the repo-local speedriftd runtime shell
# ABOUTME: Verifies runtime snapshots, ledger files, and CLI-friendly behavior

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.project_autopilot import AutopilotConfig, AutopilotRun, WorkerContext
from driftdriver.speedriftd import (
    collect_runtime_snapshot,
    run_runtime_cycle,
    run_runtime_loop,
)
from driftdriver.speedriftd_state import (
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


if __name__ == "__main__":
    unittest.main()
