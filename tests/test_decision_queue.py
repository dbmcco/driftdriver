# ABOUTME: Tests for centralized decision queue CRUD operations.
# ABOUTME: Covers create, read, answer, expire, and filtering.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.decision_queue import (
    DecisionRecord,
    answer_decision,
    create_decision,
    read_decisions,
    read_pending_decisions,
)


class DecisionQueueTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path) -> Path:
        runtime = tmp / ".workgraph" / "service" / "runtime"
        runtime.mkdir(parents=True)
        return tmp

    def test_create_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            dec = create_decision(
                project,
                repo="test-repo",
                question="Should we weight technical depth higher?",
                category="feature",
                context={"task_id": "scoring", "options": ["A: Yes", "B: No"]},
            )
            self.assertTrue(dec.id.startswith("dec-"))
            self.assertEqual(dec.status, "pending")
            self.assertEqual(dec.repo, "test-repo")
            self.assertEqual(dec.category, "feature")

    def test_read_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            create_decision(project, repo="r1", question="Q1", category="aesthetic")
            create_decision(project, repo="r1", question="Q2", category="business")
            decisions = read_decisions(project)
            self.assertEqual(len(decisions), 2)

    def test_read_pending_only(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            d1 = create_decision(project, repo="r1", question="Q1", category="aesthetic")
            create_decision(project, repo="r1", question="Q2", category="feature")
            answer_decision(project, decision_id=d1.id, answer="Option A", answered_via="telegram")
            pending = read_pending_decisions(project)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].question, "Q2")

    def test_answer_decision(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            d = create_decision(project, repo="r1", question="Q?", category="aesthetic")
            result = answer_decision(project, decision_id=d.id, answer="Go with A", answered_via="terminal")
            self.assertEqual(result.status, "answered")
            self.assertEqual(result.answer, "Go with A")
            self.assertEqual(result.answered_via, "terminal")
            self.assertIsNotNone(result.answered_at)

    def test_answer_nonexistent_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            result = answer_decision(project, decision_id="dec-fake", answer="x", answered_via="cli")
            self.assertIsNone(result)

    def test_decisions_file_created_on_first_write(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            decisions_file = project / ".workgraph" / "service" / "runtime" / "decisions.jsonl"
            self.assertFalse(decisions_file.exists())
            create_decision(project, repo="r1", question="Q?", category="feature")
            self.assertTrue(decisions_file.exists())

    def test_read_empty_returns_empty_list(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            self.assertEqual(read_decisions(project), [])


if __name__ == "__main__":
    unittest.main()
