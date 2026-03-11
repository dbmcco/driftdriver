# ABOUTME: Tests for factory brain directive schema, validator, parser, and executor.
# ABOUTME: Covers schema completeness, parse/validate logic, and dry-run execution paths.

from __future__ import annotations

import unittest

from driftdriver.factory_brain.directives import (
    DIRECTIVE_SCHEMA,
    BrainResponse,
    Directive,
    execute_directive,
    execute_directives,
    parse_brain_response,
    validate_directive,
)


class TestDirectiveSchema(unittest.TestCase):
    def test_directive_schema_has_all_actions(self) -> None:
        expected_actions = {
            "kill_process",
            "kill_daemon",
            "clear_locks",
            "start_dispatch_loop",
            "stop_dispatch_loop",
            "spawn_agent",
            "set_mode",
            "adjust_concurrency",
            "enroll",
            "unenroll",
            "set_attractor_target",
            "send_telegram",
            "escalate",
            "noop",
        }
        self.assertEqual(set(DIRECTIVE_SCHEMA.keys()), expected_actions)


class TestParseBrainResponse(unittest.TestCase):
    def test_parse_brain_response_valid(self) -> None:
        raw = {
            "reasoning": "Repo is stalled, need to restart daemon.",
            "directives": [
                {"action": "kill_daemon", "params": {"repo": "paia-shell"}},
                {"action": "start_dispatch_loop", "params": {"repo": "paia-shell"}},
            ],
        }
        resp = parse_brain_response(raw)
        self.assertIsInstance(resp, BrainResponse)
        self.assertEqual(resp.reasoning, "Repo is stalled, need to restart daemon.")
        self.assertEqual(len(resp.directives), 2)
        self.assertEqual(resp.directives[0].action, "kill_daemon")
        self.assertEqual(resp.directives[0].params["repo"], "paia-shell")
        self.assertIsNone(resp.telegram)
        self.assertFalse(resp.escalate)

    def test_parse_brain_response_with_telegram(self) -> None:
        raw = {
            "reasoning": "Critical situation detected.",
            "directives": [
                {"action": "noop", "params": {"reason": "waiting for human"}},
            ],
            "telegram": "ALERT: paia-shell is down, manual intervention needed.",
            "escalate": True,
        }
        resp = parse_brain_response(raw)
        self.assertEqual(resp.telegram, "ALERT: paia-shell is down, manual intervention needed.")
        self.assertTrue(resp.escalate)
        self.assertEqual(len(resp.directives), 1)


class TestValidateDirective(unittest.TestCase):
    def test_validate_directive_valid(self) -> None:
        d = Directive(action="kill_process", params={"pid": 12345})
        self.assertTrue(validate_directive(d))

    def test_validate_directive_unknown_action(self) -> None:
        d = Directive(action="destroy_everything", params={})
        self.assertFalse(validate_directive(d))

    def test_validate_directive_missing_required_param(self) -> None:
        d = Directive(action="spawn_agent", params={"repo": "paia-shell"})
        self.assertFalse(validate_directive(d))


class TestExecuteDirective(unittest.TestCase):
    def test_execute_directive_noop(self) -> None:
        d = Directive(action="noop", params={"reason": "all good"})
        result = execute_directive(d, dry_run=False)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reason"], "all good")

    def test_execute_directive_dry_run(self) -> None:
        d = Directive(action="kill_process", params={"pid": 99999})
        result = execute_directive(d, dry_run=True)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["action"], "kill_process")
        self.assertEqual(result["pid"], 99999)

    def test_execute_directives_batch(self) -> None:
        directives = [
            Directive(action="noop", params={"reason": "first"}),
            Directive(action="noop", params={"reason": "second"}),
            Directive(action="escalate", params={"reason": "heads up"}),
        ]
        results = execute_directives(directives, dry_run=False)
        self.assertEqual(len(results), 3)
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(results[0]["reason"], "first")
        self.assertEqual(results[1]["reason"], "second")
        self.assertEqual(results[2]["status"], "ok")
        self.assertEqual(results[2]["action"], "escalate")


if __name__ == "__main__":
    unittest.main()
