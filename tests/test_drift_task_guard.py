# ABOUTME: Tests for drift_task_guard — dedup, authority budgets, global ceiling.
# ABOUTME: Verifies the shared guard prevents feedback loop task explosion.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.drift_task_guard import (
    DEFAULT_GLOBAL_CEILING,
    count_active_drift_tasks,
    count_all_active_drift_tasks,
    guarded_add_drift_task,
)


def _fake_run_wg(responses: dict[str, tuple[int, str, str]]):
    """Return a mock _run_wg that maps command fingerprints to responses."""
    def _run(cmd, *, cwd=None, timeout=40.0):
        for pattern, response in responses.items():
            if pattern in " ".join(cmd):
                return response
        return (1, "", "no match")
    return _run


class TestCountActiveDriftTasks(unittest.TestCase):
    """Tests for count_active_drift_tasks."""

    def test_counts_matching_tags(self) -> None:
        tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "in_progress", "tags": ["drift", "qadrift"]},
            {"status": "done", "tags": ["drift", "qadrift"]},  # terminal
            {"status": "open", "tags": ["drift", "secdrift"]},  # wrong lane
            {"status": "open", "tags": ["qadrift"]},  # no drift tag
        ]
        count = count_active_drift_tasks(Path("."), "qadrift", _tasks=tasks)
        self.assertEqual(count, 2)

    def test_terminal_statuses_excluded(self) -> None:
        tasks = [
            {"status": "done", "tags": ["drift", "qadrift"]},
            {"status": "abandoned", "tags": ["drift", "qadrift"]},
            {"status": "failed", "tags": ["drift", "qadrift"]},
        ]
        count = count_active_drift_tasks(Path("."), "qadrift", _tasks=tasks)
        self.assertEqual(count, 0)

    def test_returns_zero_on_wg_error(self) -> None:
        with patch("driftdriver.drift_task_guard._run_wg") as mock:
            mock.return_value = (1, "", "error")
            count = count_active_drift_tasks(Path("."), "qadrift")
        self.assertEqual(count, 0)

    def test_returns_zero_on_invalid_json(self) -> None:
        with patch("driftdriver.drift_task_guard._run_wg") as mock:
            mock.return_value = (0, "not json", "")
            count = count_active_drift_tasks(Path("."), "qadrift")
        self.assertEqual(count, 0)


class TestCountAllActiveDriftTasks(unittest.TestCase):
    """Tests for count_all_active_drift_tasks."""

    def test_counts_all_lanes(self) -> None:
        tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "secdrift"]},
            {"status": "open", "tags": ["drift", "coredrift"]},
            {"status": "done", "tags": ["drift", "qadrift"]},  # terminal
            {"status": "open", "tags": ["feature"]},  # not drift
        ]
        count = count_all_active_drift_tasks(Path("."), _tasks=tasks)
        self.assertEqual(count, 3)


class TestGuardedAddDriftTask(unittest.TestCase):
    """Tests for the unified guarded_add_drift_task."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.wg_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_existing_task_returns_existing(self) -> None:
        """If wg show finds the task, return 'existing' without creating."""
        with patch("driftdriver.drift_task_guard._run_wg") as mock:
            mock.return_value = (0, '{"id": "qadrift-abc"}', "")
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-abc",
                title="test",
                description="test desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "existing")

    def test_capped_returns_capped(self) -> None:
        """If active drift tasks >= budget max_active_tasks, return 'capped'."""
        tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]

        def mock_run(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "not found")
            if "list" in cmd:
                return (0, json.dumps(tasks), "")
            return (1, "", "")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-new",
                title="new finding",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "capped")

    def test_creates_with_immediate_flag(self) -> None:
        """When under budget, creates via directive with --immediate and records to ledger."""
        captured_cmd: list[str] = []

        def mock_run(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "not found")
            if "list" in cmd:
                return (0, "[]", "")
            return (1, "", "")

        def mock_subprocess(cmd, **kwargs):
            captured_cmd.extend(cmd)
            from unittest.mock import MagicMock
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run), \
             patch("driftdriver.executor_shim.subprocess.run", side_effect=mock_subprocess):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-new123",
                title="new finding",
                description="desc",
                lane_tag="qadrift",
                extra_tags=["quality", "review"],
            )
        self.assertEqual(result, "created")
        self.assertIn("--immediate", captured_cmd)
        self.assertIn("qadrift", captured_cmd)
        self.assertIn("quality", captured_cmd)
        # Verify budget ledger was written
        ledger = self.wg_dir / "budget-ledger.jsonl"
        self.assertTrue(ledger.exists())

    def test_after_flag_used_for_dependency(self) -> None:
        captured_cmd: list[str] = []

        def mock_run(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "not found")
            if "list" in cmd:
                return (0, "[]", "")
            return (1, "", "")

        def mock_subprocess(cmd, **kwargs):
            captured_cmd.extend(cmd)
            from unittest.mock import MagicMock
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run), \
             patch("driftdriver.executor_shim.subprocess.run", side_effect=mock_subprocess):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="drift-breaker-task1",
                title="breaker: task1",
                description="desc",
                lane_tag="breaker",
                after="task1",
            )
        self.assertEqual(result, "created")
        self.assertIn("--after", captured_cmd)
        idx = captured_cmd.index("--after")
        self.assertEqual(captured_cmd[idx + 1], "task1")

    def test_add_failure_returns_error(self) -> None:
        def mock_run(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "not found")
            if "list" in cmd:
                return (0, "[]", "")
            return (1, "", "")

        def mock_subprocess(cmd, **kwargs):
            from unittest.mock import MagicMock
            return MagicMock(returncode=1, stdout="", stderr="already exists")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run), \
             patch("driftdriver.executor_shim.subprocess.run", side_effect=mock_subprocess):
            result = guarded_add_drift_task(
                wg_dir=self.wg_dir,
                task_id="qadrift-fail",
                title="will fail",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "error")

    def test_default_global_ceiling(self) -> None:
        self.assertEqual(DEFAULT_GLOBAL_CEILING, 50)


if __name__ == "__main__":
    unittest.main()
