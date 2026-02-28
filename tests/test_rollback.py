# ABOUTME: Tests for checkpoint-based rollback decision logic.
# ABOUTME: Covers NONE/PARTIAL/RECOVER/ESCALATE outcomes and checkpoint discovery.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pytest

from driftdriver.rollback import (
    RollbackDecision,
    evaluate_rollback,
    execute_rollback,
    find_checkpoint,
)


class TestEvaluateRollback(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _make_checkpoint(self, task_id: str) -> None:
        checkpoint_dir = self.project_dir / ".agentjj" / "checkpoints" / f"pre-task-{task_id}"
        checkpoint_dir.mkdir(parents=True)

    def test_no_rollback_for_low_drift(self) -> None:
        decision = evaluate_rollback(0.1, "task-abc", self.project_dir)
        self.assertIsInstance(decision, RollbackDecision)
        self.assertEqual(decision.action, "NONE")
        self.assertIsNone(decision.checkpoint_id)

    def test_partial_for_medium_drift(self) -> None:
        decision = evaluate_rollback(0.5, "task-abc", self.project_dir)
        self.assertEqual(decision.action, "PARTIAL")
        # Medium drift doesn't require a checkpoint
        self.assertIsNone(decision.checkpoint_id)

    def test_recover_for_high_drift(self) -> None:
        self._make_checkpoint("task-abc")
        decision = evaluate_rollback(0.8, "task-abc", self.project_dir)
        self.assertEqual(decision.action, "RECOVER")
        self.assertEqual(decision.checkpoint_id, "pre-task-task-abc")

    def test_escalate_when_no_checkpoint(self) -> None:
        # High drift but no checkpoint exists
        decision = evaluate_rollback(0.9, "task-abc", self.project_dir)
        self.assertEqual(decision.action, "ESCALATE")
        self.assertIsNone(decision.checkpoint_id)

    def test_boundary_at_0_3_is_partial(self) -> None:
        decision = evaluate_rollback(0.3, "task-abc", self.project_dir)
        self.assertEqual(decision.action, "PARTIAL")

    def test_boundary_at_0_7_is_partial(self) -> None:
        decision = evaluate_rollback(0.7, "task-abc", self.project_dir)
        self.assertEqual(decision.action, "PARTIAL")


class TestFindCheckpoint(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_find_checkpoint_returns_correct_id(self) -> None:
        task_id = "task-xyz"
        checkpoint_dir = self.project_dir / ".agentjj" / "checkpoints" / f"pre-task-{task_id}"
        checkpoint_dir.mkdir(parents=True)

        result = find_checkpoint(task_id, self.project_dir)
        self.assertEqual(result, f"pre-task-{task_id}")

    def test_find_checkpoint_returns_none_when_missing(self) -> None:
        result = find_checkpoint("task-xyz", self.project_dir)
        self.assertIsNone(result)


class TestExecuteRollback(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    @pytest.mark.skip(reason="execute_rollback not yet implemented — stub always returns True")
    def test_execute_rollback_is_stub(self) -> None:
        """execute_rollback is a stub — verify it returns True and document this."""
        result = execute_rollback("pre-task-task-abc", self.project_dir)
        self.assertIs(result, True, "Stub should return True; update test when implemented")


if __name__ == "__main__":
    unittest.main()
