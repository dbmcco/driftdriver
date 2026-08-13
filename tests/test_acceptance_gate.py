# ABOUTME: Tests for the deterministic acceptance-criteria gate.
# ABOUTME: Covers evaluate_acceptance (pass/blocked/degraded/no_criteria),
# ABOUTME: per-repo degrade ceiling, reset, and CLI status.

import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.acceptance_gate import (
    CriterionResult,
    GateResult,
    evaluate_acceptance,
    load_degrade_state,
    save_degrade_state,
    reset_degrade,
    degrade_status,
    check_task,
    _extract_verify_commands,
)


class EvaluateAcceptanceTests(unittest.TestCase):
    """Core evaluator: pass, blocked, degraded, no_criteria."""

    def test_no_verify_commands_passes_with_no_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = evaluate_acceptance("t1", [], Path(tmp))
        self.assertEqual(result.status, "no_criteria")
        self.assertFalse(result.is_blocking)

    def test_all_verify_commands_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = evaluate_acceptance(
                "t1", ["true", "echo hello"], Path(tmp)
            )
        self.assertEqual(result.status, "pass")
        self.assertEqual(result.passed_count, 2)
        self.assertFalse(result.is_blocking)

    def test_failing_command_blocks_after_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # Exhaust the degrade ceiling (3 degrades)
            for i in range(3):
                result = evaluate_acceptance("t1", ["false"], repo)
                self.assertEqual(result.status, "degraded")
            # 4th attempt: now blocked
            result = evaluate_acceptance("t1", ["false"], repo)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.is_blocking)
        self.assertIn("ceiling reached", result.reason)

    def test_failing_command_degrades_under_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = evaluate_acceptance("t1", ["false"], Path(tmp))
        self.assertEqual(result.status, "degraded")
        self.assertFalse(result.is_blocking)
        self.assertEqual(result.degrade_count, 1)
        self.assertIn("Degraded", result.reason)

    def test_mixed_pass_fail_degrades(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = evaluate_acceptance("t1", ["true", "false"], Path(tmp))
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.passed_count, 1)
        self.assertEqual(result.total_count, 2)

    def test_timeout_treated_as_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = evaluate_acceptance(
                "t1", ["sleep 10"], Path(tmp), timeout=1
            )
        self.assertIn(result.status, ("degraded", "blocked"))
        self.assertFalse(result.results[0].passed)
        self.assertIn("Timed out", result.results[0].stderr)

    def test_custom_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # ceiling=1: first fail degrades, second blocks
            r1 = evaluate_acceptance("t1", ["false"], repo, degrade_ceiling=1)
            self.assertEqual(r1.status, "degraded")
            r2 = evaluate_acceptance("t1", ["false"], repo, degrade_ceiling=1)
        self.assertEqual(r2.status, "blocked")


class DegradeStateTests(unittest.TestCase):
    """Per-repo degrade counter persistence."""

    def test_save_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / ".workgraph" / "service").mkdir(parents=True)
            save_degrade_state(repo, {"t1": 2, "t2": 1})
            loaded = load_degrade_state(repo)
        self.assertEqual(loaded, {"t1": 2, "t2": 1})

    def test_load_missing_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            loaded = load_degrade_state(Path(tmp))
        self.assertEqual(loaded, {})

    def test_load_corrupt_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = repo / ".workgraph" / "service" / "acceptance-degrade.json"
            path.parent.mkdir(parents=True)
            path.write_text("not json")
            loaded = load_degrade_state(repo)
        self.assertEqual(loaded, {})

    def test_reset_single_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            save_degrade_state(repo, {"t1": 3, "t2": 1})
            count = reset_degrade(repo, "t1")
            state = load_degrade_state(repo)
        self.assertEqual(count, 1)
        self.assertNotIn("t1", state)
        self.assertIn("t2", state)

    def test_reset_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            save_degrade_state(repo, {"t1": 3, "t2": 1})
            count = reset_degrade(repo)
            state = load_degrade_state(repo)
        self.assertEqual(count, 2)
        self.assertEqual(state, {})

    def test_degrade_status_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            save_degrade_state(repo, {"t1": 3, "t2": 1})
            status = degrade_status(repo)
        self.assertEqual(status["total_degraded_tasks"], 2)
        self.assertIn("t1", status["at_ceiling"])
        self.assertNotIn("t2", status["at_ceiling"])


class GateResultTests(unittest.TestCase):
    """GateResult properties and serialization."""

    def test_is_blocking(self) -> None:
        blocked = GateResult(status="blocked", task_id="t1")
        passed = GateResult(status="pass", task_id="t1")
        degraded = GateResult(status="degraded", task_id="t1")
        self.assertTrue(blocked.is_blocking)
        self.assertFalse(passed.is_blocking)
        self.assertFalse(degraded.is_blocking)

    def test_to_dict(self) -> None:
        result = GateResult(
            status="pass",
            task_id="t1",
            results=[
                CriterionResult(command="true", passed=True, exit_code=0),
                CriterionResult(command="false", passed=False, exit_code=1),
            ],
        )
        d = result.to_dict()
        self.assertEqual(d["status"], "pass")
        self.assertEqual(d["passed"], 1)
        self.assertEqual(d["total"], 2)
        self.assertEqual(len(d["results"]), 2)

    def test_criterion_summary(self) -> None:
        passed = CriterionResult(command="pytest", passed=True, exit_code=0)
        failed = CriterionResult(command="pytest", passed=False, exit_code=1)
        self.assertIn("PASS", passed.summary)
        self.assertIn("FAIL", failed.summary)
        self.assertIn("exit 1", failed.summary)


if __name__ == "__main__":
    unittest.main()


class CheckTaskTests(unittest.TestCase):
    """Integration: reading verify commands from graph.jsonl."""

    def test_extract_verify_commands_from_contract(self) -> None:
        desc = '''```wg-contract
schema = 1
verify = ["pytest tests/test_foo.py", "npm run build"]
acceptance = ["Tests pass"]
```'''
        cmds = _extract_verify_commands(desc)
        self.assertEqual(cmds, ["pytest tests/test_foo.py", "npm run build"])

    def test_extract_verify_no_verify_field(self) -> None:
        desc = "```wg-contract\nschema = 1\n```"
        self.assertEqual(_extract_verify_commands(desc), [])

    def test_check_task_with_verify_commands(self) -> None:
        """End-to-end: graph.jsonl with a task that has verify commands."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            wg = repo / ".workgraph"
            wg.mkdir()
            task = {"id": "t1", "description": 'verify = ["true"]'}
            (wg / "graph.jsonl").write_text(json.dumps(task) + "\n")
            result = check_task(wg, "t1")
        self.assertEqual(result.status, "pass")

    def test_check_task_without_verify_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            wg = repo / ".workgraph"
            wg.mkdir()
            task = {"id": "t1", "description": "no verify here"}
            (wg / "graph.jsonl").write_text(json.dumps(task) + "\n")
            result = check_task(wg, "t1")
        self.assertEqual(result.status, "no_criteria")

    def test_check_task_missing_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = check_task(Path(tmp) / ".workgraph", "t1")
        self.assertEqual(result.status, "no_criteria")
