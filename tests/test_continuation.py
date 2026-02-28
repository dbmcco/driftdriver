# ABOUTME: Tests for the continuation evaluator module.
# ABOUTME: Covers decision logic and throttle behaviour for agent stop events.
from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.continuation import (
    ContinuationDecision,
    check_throttle,
    evaluate_continuation,
    record_continuation,
)


class EvaluateContinuationTests(unittest.TestCase):
    def test_continue_when_task_incomplete(self) -> None:
        contract = {
            "status": "in_progress",
            "checklist": ["item1", "item2"],
            "checklist_done": ["item1"],
        }
        decision = evaluate_continuation(contract, recent_actions=["wrote file"], stop_reason="agent_stop")
        self.assertEqual(decision.action, "CONTINUE")
        self.assertFalse(decision.throttled)

    def test_stop_when_no_active_task(self) -> None:
        decision = evaluate_continuation({}, recent_actions=[], stop_reason="agent_stop")
        self.assertEqual(decision.action, "STOP")
        self.assertFalse(decision.throttled)

    def test_stop_when_task_done(self) -> None:
        contract = {
            "status": "done",
            "checklist": ["item1"],
            "checklist_done": ["item1"],
        }
        decision = evaluate_continuation(contract, recent_actions=["wrote file"], stop_reason="agent_stop")
        self.assertEqual(decision.action, "STOP")
        self.assertFalse(decision.throttled)

    def test_escalate_when_blocked(self) -> None:
        contract = {
            "status": "blocked",
            "checklist": ["item1"],
            "checklist_done": [],
        }
        decision = evaluate_continuation(contract, recent_actions=[], stop_reason="agent_stop")
        self.assertEqual(decision.action, "ESCALATE")
        self.assertFalse(decision.throttled)

    def test_throttled_decision_returns_stop(self) -> None:
        contract = {
            "status": "in_progress",
            "checklist": ["item1"],
            "checklist_done": [],
        }
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            # Record 4 continuations to exceed the default limit of 3
            now = time.time()
            for _ in range(4):
                record_continuation(state_dir)
            decision = evaluate_continuation(
                contract,
                recent_actions=["wrote file"],
                stop_reason="agent_stop",
                state_dir=state_dir,
            )
        self.assertEqual(decision.action, "STOP")
        self.assertTrue(decision.throttled)


class ThrottleTests(unittest.TestCase):
    def test_throttle_allows_under_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            record_continuation(state_dir)
            record_continuation(state_dir)
            throttled = check_throttle(state_dir, window_seconds=300, max_continuations=3)
        self.assertFalse(throttled)

    def test_throttle_blocks_over_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            for _ in range(4):
                record_continuation(state_dir)
            throttled = check_throttle(state_dir, window_seconds=300, max_continuations=3)
        self.assertTrue(throttled)


if __name__ == "__main__":
    unittest.main()
