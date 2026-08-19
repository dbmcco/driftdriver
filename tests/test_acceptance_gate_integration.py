# ABOUTME: Integration tests for the acceptance gate on the completion path.
# ABOUTME: Exercises check_task against a real (temp) workgraph: blocking,
# ABOUTME: degrade flow, ceiling trip, and reset — as the coordinator sees it.

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from driftdriver.acceptance_gate import (
    check_task,
    degrade_status,
    load_degrade_state,
    reset_degrade,
)

TASK_BLOCKING = "integration.blocking"
TASK_DEGRADE = "integration.degrade"


def _make_workgraph(tmp: str) -> Path:
    """Create a minimal .workgraph with two tasks carrying verify commands."""
    wg_dir = Path(tmp) / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    tasks = [
        {
            "kind": "task",
            "id": TASK_BLOCKING,
            "title": "Blocking task",
            "description": (
                "```wg-contract\n"
                'verify = ["false", "true"]\n'
                "```\n"
            ),
        },
        {
            "kind": "task",
            "id": TASK_DEGRADE,
            "title": "Degrade task",
            "description": (
                "```wg-contract\n"
                'verify = ["exit 1"]\n'
                "```\n"
            ),
        },
        {
            "kind": "task",
            "id": "integration.nocriteria",
            "title": "No criteria task",
            "description": "Just a plain description, no contract fence.",
        },
    ]
    with open(wg_dir / "graph.jsonl", "w") as fh:
        for task in tasks:
            fh.write(json.dumps(task) + "\n")
    return wg_dir


class TestCompletionPathBlocking(unittest.TestCase):
    """A task with failing verify commands cannot complete."""

    def test_failing_verify_commands_report_each_criterion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            result = check_task(wg_dir, TASK_BLOCKING, record_degrade=True)
        self.assertEqual(result.total_count, 2)
        self.assertEqual(result.passed_count, 1)
        statuses = {r.command: r.passed for r in result.results}
        self.assertFalse(statuses["false"])
        self.assertTrue(statuses["true"])

    def test_gate_blocks_after_ceiling_exhausted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            # Consume the degrade budget through completion attempts.
            for _ in range(3):
                result = check_task(wg_dir, TASK_BLOCKING, record_degrade=True)
                self.assertEqual(result.status, "degraded")
            # The next completion attempt is hard-blocked.
            result = check_task(wg_dir, TASK_BLOCKING, record_degrade=True)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.is_blocking)
        self.assertIn("ceiling reached", result.reason)

    def test_read_only_check_does_not_consume_degrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            # Inspection (record_degrade=False) must not consume the budget.
            for _ in range(5):
                result = check_task(wg_dir, TASK_BLOCKING, record_degrade=False)
            self.assertEqual(result.status, "degraded")
            state = load_degrade_state(Path(tmp))
        self.assertEqual(state.get(TASK_BLOCKING, 0), 0)


class TestDegradeFlow(unittest.TestCase):
    """The escape hatch completes the task once, then blocks at the ceiling."""

    def test_degrade_increments_counter_and_reports_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            result = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            state = load_degrade_state(Path(tmp))
        self.assertEqual(result.status, "degraded")
        self.assertFalse(result.is_blocking)
        self.assertEqual(result.degrade_count, 1)
        self.assertIn("override 1/3", result.reason)
        self.assertIn("Task completes with a warning", result.reason)
        self.assertEqual(state.get(TASK_DEGRADE), 1)

    def test_beyond_ceiling_blocked_without_reset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            for _ in range(3):
                check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            result = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.is_blocking)
        self.assertEqual(result.degrade_count, 3)

    def test_reset_restores_degrade_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            for _ in range(3):
                check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            self.assertEqual(
                check_task(wg_dir, TASK_DEGRADE, record_degrade=True).status,
                "blocked",
            )
            cleared = reset_degrade(Path(tmp), task_id=TASK_DEGRADE)
            result = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
        self.assertEqual(cleared, 1)
        self.assertEqual(result.status, "degraded")
        self.assertEqual(result.degrade_count, 1)

    def test_degrade_status_reflects_counter_and_ceiling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            status = degrade_status(Path(tmp))
        self.assertEqual(status["per_task"][TASK_DEGRADE], 1)
        self.assertEqual(status["ceiling"], 3)

    def test_task_without_criteria_passes_through(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            result = check_task(wg_dir, "integration.nocriteria", record_degrade=True)
        self.assertEqual(result.status, "no_criteria")
        self.assertFalse(result.is_blocking)

    def test_check_task_uses_policy_ceiling(self) -> None:
        """The wired completion path honors drift-policy.toml [acceptance]."""
        with tempfile.TemporaryDirectory() as tmp:
            wg_dir = _make_workgraph(tmp)
            (wg_dir / "drift-policy.toml").write_text(
                "[acceptance]\ndegrade_ceiling = 1\n", encoding="utf-8"
            )
            first = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
            second = check_task(wg_dir, TASK_DEGRADE, record_degrade=True)
        self.assertEqual(first.status, "degraded")
        self.assertEqual(first.degrade_ceiling, 1)
        self.assertEqual(second.status, "blocked")
        self.assertIn("ceiling reached", second.reason)


class TestMalformedContractCompletion(unittest.TestCase):
    """A contract that advertises verification but cannot be parsed blocks
    completion hard — it never degrades and never reports no_criteria."""

    def _gate(self, tmp: str, task_id: str, description: str) -> object:
        wg_dir = Path(tmp) / ".workgraph"
        wg_dir.mkdir(parents=True, exist_ok=True)
        task = {"kind": "task", "id": task_id, "title": task_id, "description": description}
        with open(wg_dir / "graph.jsonl", "a") as fh:
            fh.write(json.dumps(task) + "\n")
        return check_task(wg_dir, task_id, record_degrade=True)

    def test_malformed_verify_blocks_and_never_consumes_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for _ in range(5):
                result = self._gate(
                    tmp,
                    "integration.malformed",
                    '```wg-contract\nverify = "not-a-list"\n```',
                )
                self.assertEqual(result.status, "malformed_contract")
                self.assertTrue(result.is_blocking)
                self.assertNotEqual(result.status, "no_criteria")
            state = load_degrade_state(Path(tmp))
        self.assertEqual(state, {})

    def test_contradictory_verify_declarations_block(self) -> None:
        description = (
            "```wg-contract\n"
            'verify = ["true"]\n'
            "```\n\n"
            "## Validation\n\n"
            "```wg-contract\n"
            'verify = ["false"]\n'
            "```"
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = self._gate(tmp, "integration.contradictory", description)
        self.assertEqual(result.status, "malformed_contract")
        self.assertTrue(result.is_blocking)
        self.assertIn("contradictory", result.reason)

    def test_canonical_validation_section_gates_cleanly(self) -> None:
        """Descriptions materialized by render_validation_contract gate on
        their verify command — the command is not lost in prose."""
        from driftdriver.planner_core import render_validation_contract

        description = render_validation_contract(
            "Implement the feature", "true", ["Feature works"]
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = self._gate(tmp, "integration.canonical", description)
        self.assertEqual(result.status, "pass")


class TestRouterCompletionBoundary(unittest.TestCase):
    """Ordinary router completion cannot silently bypass the acceptance gate.

    ``check_agent_completions`` polls remote HTTP agents and completes their
    tasks with ``wg done --skip-verify``. These tests pin the boundary:
    the acceptance check runs before ``wg done``, any ``--skip-verify`` use
    carries a recorded non-empty internal reason, and a completion that does
    not apply is reported as an explicit coordination wait — never as a
    silent success that would re-spin ``wg done`` on the next poll.
    """

    TASK_ID = "router.gated"

    def _make_repo(self, tmp: str, description: str) -> Path:
        repo = Path(tmp)
        wg_dir = repo / ".workgraph"
        wg_dir.mkdir(parents=True, exist_ok=True)
        task = {
            "kind": "task",
            "id": self.TASK_ID,
            "status": "in-progress",
            "title": "Routed task",
            "tags": ["agent:samantha"],
            "description": description,
        }
        (wg_dir / "graph.jsonl").write_text(json.dumps(task) + "\n", encoding="utf-8")
        return repo

    def _make_config(self):
        from driftdriver.task_router import ExecutorConfig, RoutingConfig

        return RoutingConfig(
            enabled=True,
            default_executor="wg-daemon",
            executors={
                "samantha": ExecutorConfig(
                    name="samantha",
                    type="http",
                    endpoint="http://localhost:3530/api/agent/task",
                    tag_match="agent:samantha",
                ),
            },
        )

    def _fake_agent(self, mock_urlopen: MagicMock) -> None:
        response = MagicMock()
        response.read.return_value = json.dumps(
            {"status": "done", "summary": "Task completed"}
        ).encode()
        response.__enter__ = lambda s: s
        response.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = response

    def _run(self, repo: Path):
        from driftdriver.task_router import check_agent_completions

        return check_agent_completions(repo, self._make_config())

    @patch("driftdriver.task_router.subprocess.run")
    @patch("driftdriver.task_router.urlopen")
    def test_ordinary_completion_gates_before_wg_done_with_recorded_reason(
        self, mock_urlopen: MagicMock, mock_run: MagicMock
    ) -> None:
        """The acceptance check runs before wg done; --skip-verify use is
        recorded with its non-empty internal reason in the task log."""
        from driftdriver.task_router import _ROUTER_SKIP_VERIFY_REASON

        order: list[tuple[str, object]] = []
        real_check_task = check_task

        def recording_gate(wg_dir, task_id, *, record_degrade=True):
            order.append(("gate", task_id))
            return real_check_task(wg_dir, task_id, record_degrade=record_degrade)

        def recording_run(cmd, **_kwargs):
            order.append(("wg", list(cmd)))
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = recording_run
        self._fake_agent(mock_urlopen)

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp, '```wg-contract\nverify = ["true"]\n```')
            with patch(
                "driftdriver.task_router.check_task", side_effect=recording_gate
            ):
                results = self._run(repo)

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].completed)
        gate_positions = [i for i, entry in enumerate(order) if entry[0] == "gate"]
        done_positions = [
            i
            for i, entry in enumerate(order)
            if entry[0] == "wg" and "done" in entry[1]
        ]
        self.assertEqual(len(gate_positions), 1, order)
        self.assertEqual(len(done_positions), 1, order)
        self.assertLess(gate_positions[0], done_positions[0])
        done_cmd = order[done_positions[0]][1]
        self.assertIn("--skip-verify", done_cmd)
        # The bypass reason is recorded on the task log that precedes done.
        log_calls = [
            entry[1]
            for entry in order
            if entry[0] == "wg" and "log" in entry[1]
        ]
        self.assertTrue(
            any(_ROUTER_SKIP_VERIFY_REASON[:40] in " ".join(cmd) for cmd in log_calls),
            log_calls,
        )

    @patch("driftdriver.task_router.subprocess.run")
    @patch("driftdriver.task_router.urlopen")
    def test_blocking_gate_prevents_wg_done(
        self, mock_urlopen: MagicMock, mock_run: MagicMock
    ) -> None:
        """A blocking gate (malformed contract) keeps the task uncompleted;
        wg done never fires."""
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        self._fake_agent(mock_urlopen)

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp, '```wg-contract\nverify = "not-a-list"\n```')
            results = self._run(repo)

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].completed)
        self.assertIn("acceptance gate blocked", results[0].error or "")
        done_calls = [
            c.args[0] for c in mock_run.call_args_list if "done" in (c.args[0] or [])
        ]
        self.assertEqual(done_calls, [])

    @patch("driftdriver.task_router.subprocess.run")
    @patch("driftdriver.task_router.urlopen")
    def test_done_contention_is_coordination_wait_not_silent_success(
        self, mock_urlopen: MagicMock, mock_run: MagicMock
    ) -> None:
        """A failed wg done (e.g. shared-root contention) is reported as an
        explicit coordination wait, never as completed=True."""

        def failing_done(cmd, **_kwargs):
            if "done" in cmd:
                return MagicMock(returncode=1, stderr="lock contention on shared root")
            return MagicMock(returncode=0, stderr="")

        mock_run.side_effect = failing_done
        self._fake_agent(mock_urlopen)

        with tempfile.TemporaryDirectory() as tmp:
            repo = self._make_repo(tmp, '```wg-contract\nverify = ["true"]\n```')
            results = self._run(repo)

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0].completed)
        self.assertIn("coordination", (results[0].error or ""))
        self.assertIn("lock contention", (results[0].error or ""))
        # Exactly one completion attempt per poll: no in-poll retry spin.
        done_calls = [
            c.args[0] for c in mock_run.call_args_list if "done" in (c.args[0] or [])
        ]
        self.assertEqual(len(done_calls), 1)
        # The wait is recorded on the task log, not just the return value.
        log_calls = [
            " ".join(c.args[0])
            for c in mock_run.call_args_list
            if "log" in (c.args[0] or [])
        ]
        self.assertTrue(
            any("waiting" in msg and "lock contention" in msg for msg in log_calls),
            log_calls,
        )

    def test_skip_verify_requires_non_empty_internal_reason(self) -> None:
        """The --skip-verify guard refuses blank reasons; only a non-empty
        recorded reason can authorize the verify-gate bypass."""
        from driftdriver.task_router import _skip_verify_args

        self.assertEqual(_skip_verify_args(None), [])
        for blank in ("", "   \t "):
            with self.assertRaises(ValueError):
                _skip_verify_args(blank)
        self.assertEqual(_skip_verify_args("named recovery path"), ["--skip-verify"])


if __name__ == "__main__":
    unittest.main()
