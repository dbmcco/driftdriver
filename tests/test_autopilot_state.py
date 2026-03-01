# ABOUTME: Tests for autopilot state persistence module
# ABOUTME: Covers worker event logging, run state save/load, and cleanup

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.autopilot_state import (
    autopilot_dir,
    clear_run_state,
    ensure_dir,
    load_run_state,
    load_worker_events,
    save_run_state,
    save_worker_event,
)
from driftdriver.project_autopilot import (
    AutopilotConfig,
    AutopilotRun,
    WorkerContext,
)


class TestAutopilotDir(unittest.TestCase):
    def test_returns_correct_path(self):
        d = autopilot_dir(Path("/project"))
        self.assertEqual(d, Path("/project/.workgraph/.autopilot"))

    def test_ensure_dir_creates_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            d = ensure_dir(p)
            self.assertTrue(d.exists())
            self.assertTrue(d.is_dir())
            self.assertEqual(d, p / ".workgraph" / ".autopilot")


class TestWorkerEvents(unittest.TestCase):
    def test_save_and_load_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            ctx = WorkerContext(
                task_id="t1", task_title="Test task", worker_name="w1",
                status="running", drift_fail_count=1,
                drift_findings=["finding: scope violation"],
            )
            save_worker_event(p, ctx, "dispatched")
            save_worker_event(p, ctx, "completed")

            events = load_worker_events(p)
            self.assertEqual(len(events), 2)
            self.assertEqual(events[0]["event"], "dispatched")
            self.assertEqual(events[0]["task_id"], "t1")
            self.assertEqual(events[1]["event"], "completed")

    def test_load_events_empty_when_no_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = load_worker_events(Path(tmp))
            self.assertEqual(events, [])

    def test_event_contains_drift_info(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            ctx = WorkerContext(
                task_id="t1", task_title="Test", worker_name="w1",
                drift_findings=["finding: spec mismatch"],
                drift_fail_count=2,
            )
            save_worker_event(p, ctx, "drift_check")

            events = load_worker_events(p)
            self.assertEqual(events[0]["drift_fail_count"], 2)
            self.assertIn("finding: spec mismatch", events[0]["drift_findings"])


class TestRunState(unittest.TestCase):
    def test_save_and_load_run_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            config = AutopilotConfig(project_dir=p, goal="Build feature")
            run = AutopilotRun(
                config=config,
                started_at=100.0,
                loop_count=3,
                completed_tasks={"t1", "t2"},
                failed_tasks={"t3"},
                escalated_tasks=set(),
            )
            save_run_state(p, run)

            state = load_run_state(p)
            self.assertIsNotNone(state)
            self.assertEqual(state["goal"], "Build feature")
            self.assertEqual(state["loop_count"], 3)
            self.assertEqual(sorted(state["completed_tasks"]), ["t1", "t2"])
            self.assertEqual(state["failed_tasks"], ["t3"])
            self.assertEqual(state["escalated_tasks"], [])

    def test_load_run_state_none_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            state = load_run_state(Path(tmp))
            self.assertIsNone(state)

    def test_save_run_state_with_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            config = AutopilotConfig(project_dir=p, goal="Test")
            ctx = WorkerContext(
                task_id="t1", task_title="Task 1", worker_name="w1",
                status="completed", drift_fail_count=1,
            )
            run = AutopilotRun(
                config=config,
                started_at=100.0,
                workers={"t1": ctx},
                completed_tasks={"t1"},
            )
            save_run_state(p, run)

            state = load_run_state(p)
            self.assertIn("t1", state["workers"])
            self.assertEqual(state["workers"]["t1"]["status"], "completed")

    def test_clear_run_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            config = AutopilotConfig(project_dir=p, goal="Test")
            run = AutopilotRun(config=config, started_at=100.0)
            save_run_state(p, run)

            # Also write an event
            ctx = WorkerContext(task_id="t1", task_title="T", worker_name="w1")
            save_worker_event(p, ctx, "test")

            # Verify files exist
            d = autopilot_dir(p)
            self.assertTrue((d / "run-state.json").exists())
            self.assertTrue((d / "workers.jsonl").exists())

            clear_run_state(p)
            self.assertFalse((d / "run-state.json").exists())
            self.assertFalse((d / "workers.jsonl").exists())


class TestIntegrationThreeTaskWorkflow(unittest.TestCase):
    """Integration test simulating a 3-task autopilot workflow."""

    def test_three_task_dry_run_with_state(self):
        """Simulate 3 tasks flowing through dry-run with state persistence."""
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            from driftdriver.project_autopilot import run_autopilot_loop

            # Mock get_ready_tasks to return 3 tasks, then empty
            tasks = [
                {"id": "feat-1", "title": "Add auth", "description": "Auth feature"},
                {"id": "feat-2", "title": "Add API", "description": "API endpoints"},
                {"id": "feat-3", "title": "Add tests", "description": "Test suite"},
            ]

            with patch("driftdriver.project_autopilot.get_ready_tasks") as mock_ready:
                mock_ready.side_effect = [tasks, []]

                config = AutopilotConfig(
                    project_dir=p, goal="Build app", dry_run=True,
                )
                run = AutopilotRun(config=config)
                result = run_autopilot_loop(run)

                self.assertEqual(len(result.completed_tasks), 3)
                self.assertIn("feat-1", result.completed_tasks)
                self.assertIn("feat-2", result.completed_tasks)
                self.assertIn("feat-3", result.completed_tasks)
                self.assertEqual(len(result.failed_tasks), 0)
                self.assertEqual(len(result.escalated_tasks), 0)

            # Save state and verify persistence
            save_run_state(p, result)
            state = load_run_state(p)
            self.assertEqual(len(state["completed_tasks"]), 3)
            self.assertEqual(state["goal"], "Build app")


if __name__ == "__main__":
    unittest.main()
