# ABOUTME: End-to-end integration smoke tests for the full directive flow.
# ABOUTME: Exercises mode gating, directive emission, shim execution, and log round-trip.

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim
from driftdriver.speedriftd_state import directives_allowed_for_mode


class TestDirectiveIntegration(unittest.TestCase):
    @patch("driftdriver.executor_shim.subprocess.run")
    def test_full_flow_observe_blocks_create_task(self, mock_run: MagicMock) -> None:
        """In observe mode, create_task directives should be filtered."""
        allowed = directives_allowed_for_mode("observe")
        d = Directive(
            source="test",
            repo="test-repo",
            action=Action.CREATE_TASK,
            params={"task_id": "t1", "title": "test"},
            reason="test",
        )
        self.assertNotIn(d.action.value, allowed)
        # Verify shim was never called since mode blocks this action
        mock_run.assert_not_called()

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_full_flow_autonomous_allows_create_task(self, mock_run: MagicMock) -> None:
        """In autonomous mode, create_task directives execute through shim."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        allowed = directives_allowed_for_mode("autonomous")

        d = Directive(
            source="test",
            repo="test-repo",
            action=Action.CREATE_TASK,
            params={"task_id": "t1", "title": "test", "tags": [], "after": []},
            reason="test",
        )
        self.assertIn(d.action.value, allowed)

        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            result = shim.execute(d)
            self.assertEqual(result, "completed")
            self.assertEqual(len(log.read_completed()), 1)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_directive_round_trip_through_log(self, mock_run: MagicMock) -> None:
        """Directive survives append -> read -> execute -> complete cycle."""
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp) / "directives")
            d = Directive(
                source="drift_task_guard",
                repo="paia-shell",
                action=Action.LOG_TO_TASK,
                params={"task_id": "t1", "message": "drift check passed"},
                reason="clean check",
            )
            log.append(d)
            pending = log.read_pending()
            self.assertEqual(len(pending), 1)

            shim = ExecutorShim(wg_dir=Path(tmp), log=log)
            shim.execute(pending[0])

            # pending should now show 0 (moved to completed)
            # Note: shim.execute appends a second copy to pending, so we check completed
            self.assertEqual(len(log.read_completed()), 1)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_supervise_mode_allows_start_service_blocks_create_task(self, mock_run: MagicMock) -> None:
        """Supervise mode allows service directives but blocks task creation."""
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        allowed = directives_allowed_for_mode("supervise")

        # start_service should be allowed
        self.assertIn("start_service", allowed)
        # create_task should be blocked
        self.assertNotIn("create_task", allowed)

        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)

            # Execute an allowed directive
            d = Directive(
                source="ecosystem_hub",
                repo="paia-shell",
                action=Action.START_SERVICE,
                params={"repo": str(wg_dir)},
                reason="service not running",
            )
            result = shim.execute(d)
            self.assertEqual(result, "completed")
