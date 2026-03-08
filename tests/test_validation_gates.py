# ABOUTME: Tests for validation gates — verifies task completion verification logic.
# ABOUTME: Covers tasks with/without verify criteria and result structure.

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.validation_gates import check_validation_gates
from driftdriver.directives import Action, DirectiveLog


class TestValidationGates(unittest.TestCase):
    @patch("driftdriver.executor_shim.subprocess.run")
    def test_task_with_verify_emits_create_validation_directive(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            task = {
                "id": "build-auth",
                "title": "Build auth system",
                "verify": "pytest tests/auth/ -v && curl localhost:3540/health",
                "status": "in-progress",
            }
            result = check_validation_gates(
                task=task,
                wg_dir=wg_dir,
                directive_log=log,
            )
            self.assertTrue(result["validation_required"])
            completed = log.read_completed()
            self.assertEqual(len(completed), 1)

    def test_task_without_verify_skips(self) -> None:
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            task = {
                "id": "quick-fix",
                "title": "Quick fix",
                "status": "in-progress",
            }
            result = check_validation_gates(
                task=task,
                wg_dir=wg_dir,
                directive_log=log,
            )
            self.assertFalse(result["validation_required"])
            pending = log.read_pending()
            self.assertEqual(len(pending), 0)

    @patch("driftdriver.executor_shim.subprocess.run")
    def test_result_includes_criteria(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp)
            log = DirectiveLog(wg_dir / "directives")
            task = {
                "id": "add-feature",
                "title": "Add feature",
                "verify": "npm test",
                "status": "in-progress",
            }
            result = check_validation_gates(
                task=task,
                wg_dir=wg_dir,
                directive_log=log,
            )
            self.assertEqual(result["criteria"], "npm test")
            self.assertEqual(result["task_id"], "add-feature")
