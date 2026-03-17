# ABOUTME: Tests for driftdriver decisions CLI command.
# ABOUTME: Covers handle_decisions_pending and CLI wiring.
from __future__ import annotations

import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from driftdriver.decision_queue import create_decision, answer_decision
from driftdriver.cli.decisions_cmd import handle_decisions_answer, handle_decisions_pending


class HandleDecisionsPendingTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path) -> Path:
        runtime = tmp / ".workgraph" / "service" / "runtime"
        runtime.mkdir(parents=True)
        return tmp

    def test_no_pending_returns_zero(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            result = handle_decisions_pending(project)
            self.assertEqual(result["count"], 0)
            self.assertEqual(result["decisions"], [])

    def test_returns_pending_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            create_decision(project, repo="repo-a", question="Use new API?", category="feature")
            create_decision(project, repo="repo-b", question="Bump version?", category="business")
            result = handle_decisions_pending(project)
            self.assertEqual(result["count"], 2)
            self.assertEqual(len(result["decisions"]), 2)
            questions = [d["question"] for d in result["decisions"]]
            self.assertIn("Use new API?", questions)
            self.assertIn("Bump version?", questions)

    def test_excludes_answered_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            d1 = create_decision(project, repo="r1", question="Q1?", category="aesthetic")
            create_decision(project, repo="r2", question="Q2?", category="feature")
            answer_decision(project, decision_id=d1.id, answer="Yes", answered_via="cli")
            result = handle_decisions_pending(project)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["decisions"][0]["question"], "Q2?")

    def test_decision_fields_present(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            create_decision(
                project,
                repo="myrepo",
                question="Deploy now?",
                category="business",
                context={"urgency": "high"},
            )
            result = handle_decisions_pending(project)
            dec = result["decisions"][0]
            self.assertEqual(dec["repo"], "myrepo")
            self.assertEqual(dec["question"], "Deploy now?")
            self.assertEqual(dec["category"], "business")
            self.assertIn("id", dec)
            self.assertIn("created_at", dec)

    def test_format_text_output(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            create_decision(project, repo="r1", question="Ship it?", category="feature")
            result = handle_decisions_pending(project)
            text = format_text(result)
            self.assertIn("Ship it?", text)
            self.assertIn("r1", text)

    def test_format_text_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            result = handle_decisions_pending(project)
            text = format_text(result)
            self.assertIn("No pending decisions", text)


class HandleDecisionsAnswerTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path) -> Path:
        runtime = tmp / ".workgraph" / "service" / "runtime"
        runtime.mkdir(parents=True)
        return tmp

    def test_answer_flips_intent_to_continue(self) -> None:
        from driftdriver.continuation_intent import read_intent, write_intent
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            # Set intent to needs_human
            write_intent(project, intent="needs_human", set_by="brain", reason="stuck")
            dec = create_decision(project, repo="myrepo", question="Fix?", category="feature")
            result = handle_decisions_answer(
                project, decision_id=dec.id, answer="Yes fix it", answered_via="telegram"
            )
            self.assertNotIn("error", result)
            self.assertEqual(result["intent_flipped"], "continue")
            self.assertEqual(result["answer"], "Yes fix it")
            self.assertEqual(result["answered_via"], "telegram")
            # Verify intent was actually flipped
            intent = read_intent(project)
            self.assertIsNotNone(intent)
            self.assertEqual(intent.intent, "continue")
            self.assertEqual(intent.set_by, "human")

    def test_answer_not_found_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            result = handle_decisions_answer(
                project, decision_id="dec-fake-123", answer="whatever"
            )
            self.assertIn("error", result)
            self.assertEqual(result["error"], "decision_not_found")

    def test_answer_already_answered_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            dec = create_decision(project, repo="r1", question="Q?", category="feature")
            # Answer it once
            handle_decisions_answer(project, decision_id=dec.id, answer="first")
            # Answer again — should fail (already answered)
            result = handle_decisions_answer(project, decision_id=dec.id, answer="second")
            self.assertIn("error", result)

    def test_answer_cli_json_output(self) -> None:
        import argparse
        from driftdriver.cli.decisions_cmd import cmd_decisions
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            dec = create_decision(project, repo="r1", question="Ship?", category="business")
            args = argparse.Namespace(
                dir=str(project), json=True, action="answer",
                decision_id=dec.id, answer_text="Ship it", answered_via="cli"
            )
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_decisions(args)
            self.assertEqual(rc, 0)
            output = json.loads(buf.getvalue())
            self.assertEqual(output["answer"], "Ship it")
            self.assertEqual(output["intent_flipped"], "continue")


def format_text(result: dict) -> str:
    """Import the formatter and call it."""
    from driftdriver.cli.decisions_cmd import format_decisions_text
    return format_decisions_text(result)


class CmdDecisionsPendingCLITests(unittest.TestCase):
    """Test the CLI arg handler wiring."""

    def test_cmd_decisions_pending_json(self) -> None:
        import argparse
        from driftdriver.cli.decisions_cmd import cmd_decisions

        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            runtime = project / ".workgraph" / "service" / "runtime"
            runtime.mkdir(parents=True)
            create_decision(project, repo="r1", question="Go?", category="feature")

            args = argparse.Namespace(dir=str(project), json=True, action="pending")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_decisions(args)
            self.assertEqual(rc, 0)
            output = json.loads(buf.getvalue())
            self.assertEqual(output["count"], 1)

    def test_cmd_decisions_pending_text(self) -> None:
        import argparse
        from driftdriver.cli.decisions_cmd import cmd_decisions

        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            runtime = project / ".workgraph" / "service" / "runtime"
            runtime.mkdir(parents=True)

            args = argparse.Namespace(dir=str(project), json=False, action="pending")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_decisions(args)
            self.assertEqual(rc, 0)
            self.assertIn("No pending decisions", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
