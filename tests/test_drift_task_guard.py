# ABOUTME: Tests for drift_task_guard — dedup, cap enforcement, and --immediate flag.
# ABOUTME: Verifies the shared guard prevents feedback loop task explosion.

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.drift_task_guard import (
    DEFAULT_CAP_PER_LANE,
    count_active_drift_tasks,
    guarded_add_drift_task,
)


def _fake_run_wg(responses: dict[str, tuple[int, str, str]]):
    """Return a mock _run_wg that maps command fingerprints to responses."""
    def _run(cmd, *, cwd=None, timeout=40.0):
        key = " ".join(cmd[:5])
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
        with patch("driftdriver.drift_task_guard._run_wg") as mock:
            mock.return_value = (0, json.dumps(tasks), "")
            count = count_active_drift_tasks(Path(".workgraph"), "qadrift")
        self.assertEqual(count, 2)

    def test_returns_zero_on_error(self) -> None:
        with patch("driftdriver.drift_task_guard._run_wg") as mock:
            mock.return_value = (1, "", "error")
            count = count_active_drift_tasks(Path(".workgraph"), "qadrift")
        self.assertEqual(count, 0)

    def test_returns_zero_on_invalid_json(self) -> None:
        with patch("driftdriver.drift_task_guard._run_wg") as mock:
            mock.return_value = (0, "not json", "")
            count = count_active_drift_tasks(Path(".workgraph"), "qadrift")
        self.assertEqual(count, 0)

    def test_terminal_statuses_excluded(self) -> None:
        tasks = [
            {"status": "done", "tags": ["drift", "qadrift"]},
            {"status": "abandoned", "tags": ["drift", "qadrift"]},
            {"status": "failed", "tags": ["drift", "qadrift"]},
        ]
        with patch("driftdriver.drift_task_guard._run_wg") as mock:
            mock.return_value = (0, json.dumps(tasks), "")
            count = count_active_drift_tasks(Path(".workgraph"), "qadrift")
        self.assertEqual(count, 0)


class TestGuardedAddDriftTask(unittest.TestCase):
    """Tests for guarded_add_drift_task."""

    def test_existing_task_returns_existing(self) -> None:
        """If wg show finds the task, return 'existing' without creating."""
        with patch("driftdriver.drift_task_guard._run_wg") as mock:
            mock.return_value = (0, '{"id": "qadrift-abc"}', "")
            result = guarded_add_drift_task(
                wg_dir=Path(".workgraph"),
                task_id="qadrift-abc",
                title="test",
                description="test desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "existing")
        # Only one call (show), no add
        self.assertEqual(mock.call_count, 1)

    def test_capped_returns_capped(self) -> None:
        """If active drift tasks >= cap, return 'capped'."""
        tasks = [
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
            {"status": "open", "tags": ["drift", "qadrift"]},
        ]
        call_count = 0

        def mock_run(cmd, *, cwd=None, timeout=40.0):
            nonlocal call_count
            call_count += 1
            if "show" in cmd:
                return (1, "", "not found")  # task doesn't exist
            if "list" in cmd:
                return (0, json.dumps(tasks), "")
            return (1, "", "")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run):
            result = guarded_add_drift_task(
                wg_dir=Path(".workgraph"),
                task_id="qadrift-new",
                title="new finding",
                description="desc",
                lane_tag="qadrift",
                cap=3,
            )
        self.assertEqual(result, "capped")

    def test_creates_with_immediate_flag(self) -> None:
        """When task doesn't exist and under cap, creates with --immediate."""
        captured_cmd: list[str] = []

        def mock_run(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "not found")
            if "list" in cmd:
                return (0, "[]", "")
            if "add" in cmd:
                captured_cmd.extend(cmd)
                return (0, "", "")
            return (1, "", "")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run):
            result = guarded_add_drift_task(
                wg_dir=Path(".workgraph"),
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
        self.assertIn("review", captured_cmd)

    def test_after_flag_used_for_dependency(self) -> None:
        """When after= is specified, --after is included in wg add."""
        captured_cmd: list[str] = []

        def mock_run(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "not found")
            if "list" in cmd:
                return (0, "[]", "")
            if "add" in cmd:
                captured_cmd.extend(cmd)
                return (0, "", "")
            return (1, "", "")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run):
            result = guarded_add_drift_task(
                wg_dir=Path(".workgraph"),
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
        """If wg add fails, return 'error'."""
        def mock_run(cmd, *, cwd=None, timeout=40.0):
            if "show" in cmd:
                return (1, "", "not found")
            if "list" in cmd:
                return (0, "[]", "")
            if "add" in cmd:
                return (1, "", "already exists")
            return (1, "", "")

        with patch("driftdriver.drift_task_guard._run_wg", side_effect=mock_run):
            result = guarded_add_drift_task(
                wg_dir=Path(".workgraph"),
                task_id="qadrift-fail",
                title="will fail",
                description="desc",
                lane_tag="qadrift",
            )
        self.assertEqual(result, "error")

    def test_default_cap_is_three(self) -> None:
        self.assertEqual(DEFAULT_CAP_PER_LANE, 3)


if __name__ == "__main__":
    unittest.main()
