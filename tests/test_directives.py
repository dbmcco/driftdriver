# ABOUTME: Tests for Directive schema — the Speedrift/wg boundary contract.
# ABOUTME: Verifies round-trip serialization, deserialization, and Action enum invariants.

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone

from driftdriver.directives import Action, Directive


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
