# ABOUTME: Tests for ExecutorShim — verifies directive-to-wg-CLI translation.
# ABOUTME: Covers create/start/fail/complete actions and log recording.

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim


class TestExecutorShim(unittest.TestCase):
    def _make_directive(self, action: Action, params: dict) -> Directive:
        return Directive(
            source="test",
            repo="test-repo",
            action=action,
            params=params,
            reason="unit test",
        )

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_create_task_calls_wg_add(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            d = self._make_directive(Action.CREATE_TASK, {
                "task_id": "drift-scope-t1",
                "title": "scope: t1",
                "after": ["t1"],
                "tags": ["drift", "scope"],
                "description": "Fix scope drift",
            })
            result = shim.execute(d)
            self.assertEqual(result, "completed")
            cmd = mock_run.call_args[0][0]
            self.assertIn("add", cmd)
            self.assertIn("--id", cmd)
            self.assertIn("drift-scope-t1", cmd)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_start_service_calls_wg_service_start(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            d = self._make_directive(Action.START_SERVICE, {"repo": "/tmp/repo"})
            result = shim.execute(d)
            self.assertEqual(result, "completed")
            cmd = mock_run.call_args[0][0]
            self.assertIn("service", cmd)
            self.assertIn("start", cmd)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_failed_command_records_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            d = self._make_directive(Action.LOG_TO_TASK, {
                "task_id": "t1",
                "message": "hello",
            })
            result = shim.execute(d)
            self.assertEqual(result, "failed")
            failed = log.read_failed()
            self.assertEqual(len(failed), 1)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_completed_directive_recorded_in_log(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            shim = ExecutorShim(wg_dir=wg_dir, log=log)
            d = self._make_directive(Action.COMPLETE_TASK, {
                "task_id": "t1",
                "artifacts": ["out.txt"],
            })
            shim.execute(d)
            completed = log.read_completed()
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]["directive_id"], d.id)
