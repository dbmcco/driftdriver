# ABOUTME: Tests for the repo-local speedriftd runtime shell
# ABOUTME: Verifies runtime snapshots, ledger files, and CLI-friendly behavior

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.autopilot_state import save_run_state
from driftdriver.project_autopilot import AutopilotConfig, AutopilotRun, WorkerContext
from driftdriver.speedriftd import (
    collect_runtime_snapshot,
    load_runtime_snapshot,
    run_runtime_cycle,
    run_runtime_loop,
    runtime_paths,
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
            save_run_state(repo, run)

            with patch("driftdriver.speedriftd.check_worker_liveness") as fake_health:
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
            self.assertEqual(loaded["next_action"], "dispatch ready task ready-1")

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
            save_run_state(repo, run)

            with patch("driftdriver.speedriftd.check_worker_liveness") as fake_health:
                fake_health.return_value.status = "dead"
                fake_health.return_value.last_event_ts = 150.0
                fake_health.return_value.last_event_type = "pre_tool_use"
                fake_health.return_value.event_count = 2
                snapshot = run_runtime_cycle(repo)

            self.assertEqual(snapshot["daemon_state"], "stalled")
            self.assertEqual(snapshot["stalled_task_ids"], ["impl"])
            stalls = runtime_paths(repo)["stalls"].read_text(encoding="utf-8")
            self.assertIn("worker_stalled_or_missing", stalls)

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
