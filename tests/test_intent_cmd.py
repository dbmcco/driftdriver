# ABOUTME: Tests for the intent CLI subcommand (set and read).
# ABOUTME: Covers arg parsing, JSON output, error handling, and integration with continuation_intent.
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.cli.intent_cmd import handle_intent_read, handle_intent_set
from driftdriver.continuation_intent import read_intent, write_intent


def _setup_control(tmp: Path, extra: dict | None = None) -> Path:
    """Create minimal .workgraph/service/runtime/control.json."""
    control_dir = tmp / ".workgraph" / "service" / "runtime"
    control_dir.mkdir(parents=True)
    control = {"repo": "test-repo", "mode": "supervise"}
    if extra:
        control.update(extra)
    (control_dir / "control.json").write_text(json.dumps(control))
    return tmp


class _FakeArgs:
    """Minimal argparse.Namespace stand-in."""

    def __init__(self, **kwargs: object) -> None:
        self.dir = None
        self.json = False
        for k, v in kwargs.items():
            setattr(self, k, v)


class HandleIntentSetTests(unittest.TestCase):
    def test_set_continue_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            args = _FakeArgs(
                dir=str(project),
                intent="continue",
                set_by="agent",
                reason="task completed",
                decision_id=None,
                json=True,
            )
            rc = handle_intent_set(args)
            self.assertEqual(rc, 0)
            intent = read_intent(project)
            self.assertIsNotNone(intent)
            self.assertEqual(intent.intent, "continue")
            self.assertEqual(intent.set_by, "agent")
            self.assertEqual(intent.reason, "task completed")

    def test_set_parked_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            args = _FakeArgs(
                dir=str(project),
                intent="parked",
                set_by="human",
                reason="waiting for review",
                decision_id=None,
                json=True,
            )
            rc = handle_intent_set(args)
            self.assertEqual(rc, 0)
            intent = read_intent(project)
            self.assertEqual(intent.intent, "parked")
            self.assertEqual(intent.set_by, "human")

    def test_set_needs_human_with_decision_id(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            args = _FakeArgs(
                dir=str(project),
                intent="needs_human",
                set_by="brain",
                reason="aesthetic choice",
                decision_id="dec-001",
                json=True,
            )
            rc = handle_intent_set(args)
            self.assertEqual(rc, 0)
            intent = read_intent(project)
            self.assertEqual(intent.intent, "needs_human")
            self.assertEqual(intent.decision_id, "dec-001")

    def test_set_invalid_intent_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            args = _FakeArgs(
                dir=str(project),
                intent="bogus",
                set_by="agent",
                reason="bad",
                decision_id=None,
                json=True,
            )
            rc = handle_intent_set(args)
            self.assertEqual(rc, 1)

    def test_set_invalid_set_by_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            args = _FakeArgs(
                dir=str(project),
                intent="continue",
                set_by="unknown",
                reason="bad actor",
                decision_id=None,
                json=True,
            )
            rc = handle_intent_set(args)
            self.assertEqual(rc, 1)

    def test_set_json_output_contains_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            args = _FakeArgs(
                dir=str(project),
                intent="continue",
                set_by="agent",
                reason="done",
                decision_id=None,
                json=True,
            )
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                handle_intent_set(args)
            output = json.loads(buf.getvalue())
            self.assertEqual(output["intent"], "continue")
            self.assertEqual(output["set_by"], "agent")
            self.assertIn("set_at", output)


class HandleIntentReadTests(unittest.TestCase):
    def test_read_returns_none_when_no_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            args = _FakeArgs(dir=str(project), json=True)
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_intent_read(args)
            self.assertEqual(rc, 0)
            output = json.loads(buf.getvalue())
            self.assertIsNone(output)

    def test_read_returns_existing_intent(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            write_intent(project, intent="parked", set_by="human", reason="paused")
            args = _FakeArgs(dir=str(project), json=True)
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_intent_read(args)
            self.assertEqual(rc, 0)
            output = json.loads(buf.getvalue())
            self.assertEqual(output["intent"], "parked")
            self.assertEqual(output["set_by"], "human")

    def test_read_no_workgraph_returns_none(self) -> None:
        with TemporaryDirectory() as tmp:
            args = _FakeArgs(dir=tmp, json=True)
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_intent_read(args)
            self.assertEqual(rc, 0)
            output = json.loads(buf.getvalue())
            self.assertIsNone(output)

    def test_read_human_readable_output(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            write_intent(project, intent="continue", set_by="agent", reason="all good")
            args = _FakeArgs(dir=str(project), json=False)
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_intent_read(args)
            self.assertEqual(rc, 0)
            text = buf.getvalue()
            self.assertIn("continue", text)
            self.assertIn("agent", text)

    def test_read_no_intent_human_readable(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _setup_control(Path(tmp))
            args = _FakeArgs(dir=str(project), json=False)
            import io
            import contextlib

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = handle_intent_read(args)
            self.assertEqual(rc, 0)
            text = buf.getvalue()
            self.assertIn("No continuation intent set", text)


if __name__ == "__main__":
    unittest.main()
