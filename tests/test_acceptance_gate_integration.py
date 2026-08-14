# ABOUTME: Integration tests for the acceptance gate on the completion path.
# ABOUTME: Exercises check_task against a real (temp) workgraph: blocking,
# ABOUTME: degrade flow, ceiling trip, and reset — as the coordinator sees it.

import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.acceptance_gate import (
    check_task,
    degrade_status,
    load_degrade_state,
    reset_degrade,
)

TASK_BLOCKING = "integration.blocking"
TASK_DEGRADE = "integration.degrade"


def _make_workgraph(tmp: str) -> Path:
    """Create a minimal .workgraph with two tasks carrying verify commands."""
    wg_dir = Path(tmp) / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {
            "kind": "task",
            "id": TASK_BLOCKING,
            "title": "Blocking task",
            "description": (
                "```wg-contract\n"
                'verify = ["false", "true"]\n'
                "```\n"
            ),
        },
        {
            "kind": "task",
            "id": TASK_DEGRADE,
            "title": "Degrade task",
            "description": (
                "```wg-contract\n"
                'verify = ["exit 1"]\n'
                "```\n"
            ),
        },
        {
            "kind": "task",
            "id": "integration.nocriteria",
            "title": "No criteria task",
            "description": "Just a plain description, no contract fence.",
        },
    ]
    with open(wg_dir / "graph.jsonl", "w") as fh:
        for task in tasks:
            fh.write(json.dumps(task) + "\n")
    return wg_dir


class TestCompletionPathBlocking(unittest.TestCase):
    """A task with failing verify commands cannot complete."""

    def test_failing_verify_commands_report_each_criterion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            result = check_task(wg_dir, TASK_BLOCKING, record_degrade=True)
        self.assertEqual(result.total_count, 2)
        self.assertEqual(result.passed_count, 1)
        statuses = {r.command: r.passed for r in result.results}
        self.assertFalse(statuses["false"])
        self.assertTrue(statuses["true"])

    def test_gate_blocks_after_ceiling_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            # Consume the degrade budget through completion attempts.
            for _ in range(3):
                result = check_task(wg_dir, TASK_BLOCKING, record_degrade=True)
                self.assertEqual(result.status, "degraded")
            # The next completion attempt is hard-blocked.
            result = check_task(wg_dir, TASK_BLOCKING, record_degrade=True)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.is_blocking)
        self.assertIn("ceiling reached", result.reason)

    def test_read_only_check_does_not_consume_degrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            # Inspection (record_degrade=False) must not consume the budget.
            for _ in range(5):
                result = check_task(wg_dir, TASK_BLOCKING, record_degrade=False)
            self.assertEqual(result.status, "degraded")
            state = load_degrade_state(Path(tmp))
        self.assertEqual(state.get(TASK_BLOCKING, 0), 0)


class TestDegradeFlow(unittest.TestCase):
    """The escape hatch completes the task once, then blocks at the ceiling."""

    def test_degrade_increments_counter_and_reports_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            result = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            state = load_degrade_state(Path(tmp))
        self.assertEqual(result.status, "degraded")
        self.assertFalse(result.is_blocking)
        self.assertEqual(result.degrade_count, 1)
        self.assertIn("override 1/3", result.reason)
        self.assertIn("Task completes with a warning", result.reason)
        self.assertEqual(state.get(TASK_DEGRADE), 1)

    def test_beyond_ceiling_blocked_without_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            for _ in range(3):
                check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            result = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.is_blocking)
        self.assertEqual(result.degrade_count, 3)

    def test_reset_restores_degrade_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            for _ in range(3):
                check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            self.assertEqual(
                check_task(wg_dir, TASK_DEGRADE, record_degrade=True).status,
                "blocked",
            )
            cleared = reset_degrade(Path(tmp), task_id=TASK_DEGRADE)
            result = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
        self.assertEqual(cleared, 1)
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.degrade_count, 1)

    def test_degrade_status_reflects_counter_and_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            status = degrade_status(Path(tmp))
        self.assertEqual(status["per_task"][TASK_DEGRADE], 1)
        self.assertEqual(status["ceiling"], 3)

    def test_task_without_criteria_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            result = check_task(wg_dir, "integration.nocriteria", record_degrade=True)
        self.assertEqual(result.status, "no_criteria")
        self.assertFalse(result.is_blocking)

    def test_check_task_uses_policy_ceiling(self) -> None:
        """The wired completion path honors drift-policy.toml [acceptance]."""
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            (wg_dir / "drift-policy.toml").write_text(
                "[acceptance]\ndegrade_ceiling = 1\n", encoding="utf-8"
            )
            first = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            second = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
        self.assertEqual(first.status, "degraded")
        self.assertEqual(first.degrade_ceiling, 1)
        self.assertEqual(second.status, "blocked")
        self.assertIn("ceiling reached", second.reason)


if __name__ == "__main__":
    unittest.main()
