# ABOUTME: Tests for factory brain directive schema, validator, parser, and executor.
# ABOUTME: Covers schema completeness, parse/validate logic, and dry-run execution paths.

from __future__ import annotations

import unittest
from pathlib import Path

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
            "create_decision",
            "enforce_compliance",
            "apply_skill_fix",
            "propose_agent_fix",
            "restart_paia_service",
        }
        self.assertEqual(set(DIRECTIVE_SCHEMA.keys()), expected_actions)

    def test_create_decision_schema(self) -> None:
        self.assertEqual(DIRECTIVE_SCHEMA["create_decision"], ["repo", "question", "category"])

    def test_enforce_compliance_schema(self) -> None:
        self.assertEqual(DIRECTIVE_SCHEMA["enforce_compliance"], ["repo"])


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


class TestCreateDecisionDirective(unittest.TestCase):
    def test_create_decision_dry_run(self) -> None:
        d = Directive(action="create_decision", params={
            "repo": "test-repo",
            "question": "Should we upgrade?",
            "category": "feature",
        })
        result = execute_directive(d, dry_run=True, repo_paths={"test-repo": "/tmp/test-repo"})
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["action"], "create_decision")
        self.assertEqual(result["repo"], "test-repo")

    def test_create_decision_unknown_repo(self) -> None:
        d = Directive(action="create_decision", params={
            "repo": "missing-repo",
            "question": "Should we upgrade?",
            "category": "feature",
        })
        result = execute_directive(d, dry_run=False, repo_paths={})
        self.assertEqual(result["status"], "error")
        self.assertIn("unknown repo", result["error"])

    def test_create_decision_executes(self, tmp_path: Path = None) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo_dir = Path(td) / "test-repo"
            repo_dir.mkdir()
            d = Directive(action="create_decision", params={
                "repo": "test-repo",
                "question": "Should we upgrade deps?",
                "category": "external_dep",
            })
            result = execute_directive(d, dry_run=False, repo_paths={"test-repo": str(repo_dir)})
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["action"], "create_decision")
            self.assertIn("decision_id", result)
            self.assertTrue(result["decision_id"].startswith("dec-"))

    def test_validate_create_decision(self) -> None:
        valid = Directive(action="create_decision", params={
            "repo": "r", "question": "q?", "category": "feature",
        })
        self.assertTrue(validate_directive(valid))

        missing = Directive(action="create_decision", params={"repo": "r"})
        self.assertFalse(validate_directive(missing))


class TestEnforceComplianceDirective(unittest.TestCase):
    def test_enforce_compliance_dry_run(self) -> None:
        d = Directive(action="enforce_compliance", params={"repo": "test-repo"})
        result = execute_directive(d, dry_run=True, repo_paths={"test-repo": "/tmp/test-repo"})
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["action"], "enforce_compliance")

    def test_enforce_compliance_unknown_repo(self) -> None:
        d = Directive(action="enforce_compliance", params={"repo": "gone"})
        result = execute_directive(d, dry_run=False, repo_paths={})
        self.assertEqual(result["status"], "error")

    def test_enforce_compliance_compliant_repo(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo_dir = Path(td) / "compliant-repo"
            repo_dir.mkdir()
            wg = repo_dir / ".workgraph" / "drifts" / "check"
            wg.parent.mkdir(parents=True)
            wg.write_text("#!/bin/sh\necho ok\n")
            d = Directive(action="enforce_compliance", params={"repo": "compliant-repo"})
            result = execute_directive(d, dry_run=False, repo_paths={"compliant-repo": str(repo_dir)})
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["compliant"])
            self.assertEqual(result["violations"], [])

    def test_enforce_compliance_noncompliant_repo(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            repo_dir = Path(td) / "bad-repo"
            repo_dir.mkdir()
            # No .workgraph at all
            d = Directive(action="enforce_compliance", params={"repo": "bad-repo"})
            result = execute_directive(d, dry_run=False, repo_paths={"bad-repo": str(repo_dir)})
            self.assertEqual(result["status"], "ok")
            self.assertFalse(result["compliant"])
            self.assertTrue(len(result["violations"]) > 0)

    def test_validate_enforce_compliance(self) -> None:
        valid = Directive(action="enforce_compliance", params={"repo": "r"})
        self.assertTrue(validate_directive(valid))

        missing = Directive(action="enforce_compliance", params={})
        self.assertFalse(validate_directive(missing))


if __name__ == "__main__":
    unittest.main()
