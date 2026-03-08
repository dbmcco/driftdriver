# ABOUTME: Tests for Directive schema — the Speedrift/wg boundary contract.
# ABOUTME: Verifies round-trip serialization, deserialization, and Action enum invariants.

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.directives import Action, Directive, DirectiveLog


class TestDirective(unittest.TestCase):
    def test_create_task_directive_round_trips_to_json(self) -> None:
        d = Directive(
            source="drift_task_guard",
            repo="paia-shell",
            action=Action.CREATE_TASK,
            params={
                "task_id": "drift-harden-fix-auth",
                "title": "harden: fix-auth",
                "after": ["fix-auth"],
                "tags": ["drift", "harden"],
            },
            reason="Hardening signals detected",
        )
        blob = d.to_json()
        parsed = json.loads(blob)
        self.assertEqual(parsed["source"], "drift_task_guard")
        self.assertEqual(parsed["repo"], "paia-shell")
        self.assertEqual(parsed["action"], "create_task")
        self.assertEqual(parsed["params"]["task_id"], "drift-harden-fix-auth")
        self.assertIn("id", parsed)
        self.assertIn("timestamp", parsed)

    def test_directive_from_json(self) -> None:
        d = Directive(
            source="ecosystem_hub",
            repo="paia-os",
            action=Action.START_SERVICE,
            params={},
            reason="stalled",
        )
        blob = d.to_json()
        restored = Directive.from_json(blob)
        self.assertEqual(restored.source, "ecosystem_hub")
        self.assertEqual(restored.action, Action.START_SERVICE)
        self.assertEqual(restored.repo, "paia-os")

    def test_all_action_enum_values_are_lowercase_snake(self) -> None:
        for a in Action:
            self.assertEqual(a.value, a.value.lower())
            self.assertNotIn("-", a.value)


class TestDirectiveLog(unittest.TestCase):
    def test_append_and_read_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp))
            d = Directive(
                source="test",
                repo="repo-a",
                action=Action.CREATE_TASK,
                params={"task_id": "t1", "title": "test task"},
                reason="unit test",
            )
            log.append(d)
            pending = log.read_pending()
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0].params["task_id"], "t1")

    def test_mark_completed_moves_from_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp))
            d = Directive(
                source="test",
                repo="repo-a",
                action=Action.LOG_TO_TASK,
                params={"task_id": "t1", "message": "hello"},
                reason="test",
            )
            log.append(d)
            log.mark_completed(d.id, exit_code=0, output="ok")
            pending = log.read_pending()
            self.assertEqual(len(pending), 0)
            completed = log.read_completed()
            self.assertEqual(len(completed), 1)
            self.assertEqual(completed[0]["directive_id"], d.id)

    def test_mark_failed_records_error(self) -> None:
        with TemporaryDirectory() as tmp:
            log = DirectiveLog(Path(tmp))
            d = Directive(
                source="test",
                repo="repo-a",
                action=Action.FAIL_TASK,
                params={"task_id": "t1", "reason": "stuck"},
                reason="test",
            )
            log.append(d)
            log.mark_failed(d.id, exit_code=1, error="wg not found")
            failed = log.read_failed()
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0]["error"], "wg not found")
