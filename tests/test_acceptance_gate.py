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

    def test_load_corrupt_returns_none_and_quarantines(self) -> None:
        """CRIT-1: corrupt state must NOT silently reset the budget.

        load_degrade_state returns None (corrupt marker) and quarantines the
        file; the gate then fails closed (hard block) until an operator reset.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = repo / ".workgraph" / "service" / "acceptance-degrade.json"
            path.parent.mkdir(parents=True)
            path.write_text("not json")
            loaded = load_degrade_state(repo)
            self.assertIsNone(loaded)
            quarantined = list(path.parent.glob("acceptance-degrade.json.corrupt-*"))
            self.assertEqual(len(quarantined), 1)

    def test_corrupt_state_blocks_hard(self) -> None:
        """CRIT-1: after corruption the gate fails closed, not open."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = repo / ".workgraph" / "service" / "acceptance-degrade.json"
            path.parent.mkdir(parents=True)
            path.write_text("{")
            result = evaluate_acceptance("t1", ["false"], repo)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.is_blocking)
        self.assertIn("corrupt", result.reason.lower())

    def test_missing_state_file_is_fresh_budget(self) -> None:
        """A missing file is not corruption — fresh state, gate operates."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            loaded = load_degrade_state(repo)
            self.assertEqual(loaded, {})

    def test_reset_clears_quarantine(self) -> None:
        """Operator reset is the escape from the quarantined (fail-closed) state."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = repo / ".workgraph" / "service" / "acceptance-degrade.json"
            path.parent.mkdir(parents=True)
            path.write_text("not json")
            self.assertEqual(evaluate_acceptance("t1", ["false"], repo).status, "blocked")
            reset_degrade(repo)
            result = evaluate_acceptance("t1", ["false"], repo)
        self.assertEqual(result.status, "degraded")

    def test_save_leaves_no_temp_file(self) -> None:
        """CRIT-2: save is tmp+replace — no partial files remain."""
        from driftdriver.acceptance_gate import _degrade_state_path

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            save_degrade_state(repo, {"t1": 1})
            service = _degrade_state_path(repo).parent
            leftovers = [p for p in service.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class TestConcurrentDegrade(unittest.TestCase):
    """CRIT-2: concurrent degrades must neither lose updates nor exceed the ceiling."""

    def test_concurrent_degrades_increment_exactly(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            threads = []
            statuses = []

            def degrade_once() -> None:
                r = evaluate_acceptance("t1", ["false"], repo, degrade_ceiling=20)
                statuses.append(r.status)

            for _ in range(8):
                t = threading.Thread(target=degrade_once)
                threads.append(t)
                t.start()
            for t in threads:
                t.join()

            state = load_degrade_state(repo)
        self.assertEqual(state.get("t1"), 8, "lost updates: counter must record every granted degrade")
        self.assertEqual(statuses.count("degraded"), 8)

    def test_concurrent_degrades_cannot_exceed_ceiling(self) -> None:
        import threading

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            threads = []
            statuses = []

            def degrade_once() -> None:
                r = evaluate_acceptance("t1", ["false"], repo, degrade_ceiling=3)
                statuses.append(r.status)

            for _ in range(8):
                t = threading.Thread(target=degrade_once)
                threads.append(t)
                t.start()
            for t in threads:
                t.join()

            state = load_degrade_state(repo)
        self.assertLessEqual(state.get("t1", 0), 3, "ceiling exceedance: more degrades granted than budget")
        self.assertEqual(statuses.count("degraded"), 3)
        self.assertEqual(statuses.count("blocked"), 5)

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


class ConfiguredCeilingTests(unittest.TestCase):
    """The degrade ceiling is configurable via drift-policy.toml [acceptance]."""

    @staticmethod
    def _write_policy(repo: Path, body: str, wg_name: str = ".workgraph") -> None:
        wg = repo / wg_name
        wg.mkdir(parents=True, exist_ok=True)
        (wg / "drift-policy.toml").write_text(body, encoding="utf-8")

    def test_policy_ceiling_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._write_policy(repo, '[acceptance]\ndegrade_ceiling = 1\n')
            first = evaluate_acceptance("t1", ["false"], repo)
            second = evaluate_acceptance("t1", ["false"], repo)
        self.assertEqual(first.status, "degraded")
        self.assertEqual(first.degrade_ceiling, 1)
        self.assertEqual(second.status, "blocked")
        self.assertEqual(second.degrade_ceiling, 1)

    def test_policy_ceiling_overrides_default_wg_dir(self) -> None:
        """The .wg workgraph dir is honored like .workgraph."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._write_policy(repo, '[acceptance]\ndegrade_ceiling = 1\n', wg_name=".wg")
            first = evaluate_acceptance("t1", ["false"], repo)
            second = evaluate_acceptance("t1", ["false"], repo)
        self.assertEqual(first.status, "degraded")
        self.assertEqual(second.status, "blocked")

    def test_missing_policy_falls_back_to_three(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            statuses = [
                evaluate_acceptance("t1", ["false"], repo).status for _ in range(4)
            ]
        self.assertEqual(statuses, ["degraded", "degraded", "degraded", "blocked"])

    def test_invalid_ceiling_falls_back_to_three(self) -> None:
        """Non-integer values fall back safely to the default."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._write_policy(repo, '[acceptance]\ndegrade_ceiling = "bogus"\n')
            result = evaluate_acceptance("t1", ["false"], repo)
            self.assertEqual(result.degrade_ceiling, 3)

    def test_zero_ceiling_falls_back_to_three(self) -> None:
        """Values below 1 are invalid; the gate never runs with a zero ceiling."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._write_policy(repo, '[acceptance]\ndegrade_ceiling = 0\n')
            result = evaluate_acceptance("t1", ["false"], repo)
            self.assertEqual(result.degrade_ceiling, 3)

    def test_corrupt_policy_falls_back_to_three(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._write_policy(repo, 'not [ valid toml {{{')
            result = evaluate_acceptance("t1", ["false"], repo)
            self.assertEqual(result.degrade_ceiling, 3)

    def test_explicit_arg_overrides_policy(self) -> None:
        """An explicit degrade_ceiling argument wins over the policy file."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._write_policy(repo, '[acceptance]\ndegrade_ceiling = 2\n')
            first = evaluate_acceptance("t1", ["false"], repo, degrade_ceiling=1)
            second = evaluate_acceptance("t1", ["false"], repo, degrade_ceiling=1)
        self.assertEqual(first.status, "degraded")
        self.assertEqual(second.status, "blocked")

    def test_degrade_status_reports_policy_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._write_policy(repo, '[acceptance]\ndegrade_ceiling = 2\n')
            save_degrade_state(repo, {"t1": 2, "t2": 1})
            status = degrade_status(repo)
        self.assertEqual(status["ceiling"], 2)
        self.assertEqual(status["at_ceiling"], ["t1"])


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


class TestQuarantineVisibility(unittest.TestCase):
    """The quarantine state must be operator-visible in acceptance status."""

    def test_status_reports_quarantine(self) -> None:
        from driftdriver.acceptance_gate import degrade_status

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            path = repo / ".workgraph" / "service" / "acceptance-degrade.json"
            path.parent.mkdir(parents=True)
            path.write_text("{oops")
            status = degrade_status(repo)
        self.assertTrue(status["quarantined"])
        self.assertIn("reset", (status["note"] or ""))

    def test_status_clean_when_healthy(self) -> None:
        from driftdriver.acceptance_gate import degrade_status

        with tempfile.TemporaryDirectory() as tmp:
            status = degrade_status(Path(tmp))
        self.assertFalse(status["quarantined"])
        self.assertIsNone(status["note"])
