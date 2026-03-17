# ABOUTME: Tests for continuation intent read/write and lifecycle transitions.
# ABOUTME: Covers intent defaults, explicit park, brain pause, and resume.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.continuation_intent import (
    ContinuationIntent,
    read_intent,
    write_intent,
)


class ReadWriteIntentTests(unittest.TestCase):
    def _setup_control(self, tmp: Path, extra: dict | None = None) -> Path:
        """Create minimal .workgraph/service/runtime/control.json."""
        control_dir = tmp / ".workgraph" / "service" / "runtime"
        control_dir.mkdir(parents=True)
        control = {"repo": "test-repo", "mode": "supervise"}
        if extra:
            control.update(extra)
        (control_dir / "control.json").write_text(json.dumps(control))
        return tmp

    def test_read_returns_none_when_no_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            intent = read_intent(project)
            self.assertIsNone(intent)

    def test_write_continue_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            write_intent(project, intent="continue", set_by="agent", reason="session ended")
            intent = read_intent(project)
            self.assertIsNotNone(intent)
            self.assertEqual(intent.intent, "continue")
            self.assertEqual(intent.set_by, "agent")
            self.assertIsNone(intent.decision_id)

    def test_write_parked_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            write_intent(project, intent="parked", set_by="human", reason="user said hold off")
            intent = read_intent(project)
            self.assertEqual(intent.intent, "parked")
            self.assertEqual(intent.set_by, "human")

    def test_write_needs_human_with_decision_id(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            write_intent(
                project,
                intent="needs_human",
                set_by="brain",
                reason="aesthetic decision required",
                decision_id="dec-20260313-001",
            )
            intent = read_intent(project)
            self.assertEqual(intent.intent, "needs_human")
            self.assertEqual(intent.decision_id, "dec-20260313-001")

    def test_write_overwrites_previous_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            write_intent(project, intent="parked", set_by="human", reason="parking")
            write_intent(project, intent="continue", set_by="brain", reason="answer received")
            intent = read_intent(project)
            self.assertEqual(intent.intent, "continue")

    def test_invalid_intent_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_control(Path(tmp))
            with self.assertRaises(ValueError):
                write_intent(project, intent="invalid", set_by="agent", reason="bad")

    def test_read_intent_missing_workgraph_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            intent = read_intent(Path(tmp))
            self.assertIsNone(intent)


if __name__ == "__main__":
    unittest.main()
