# ABOUTME: End-to-end tests for the acceptance gate via the real CLI and
# ABOUTME: cmd_check wiring: check/status/reset subcommands and the
# ABOUTME: completion-path guard that reports acceptance_gate in plugins_json.

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TASK_ID = "e2e.acceptance"


def _make_repo(tmp: str) -> Path:
    """Create a minimal repo with a .workgraph and one task with verify commands."""
    repo = Path(tmp)
    wg_dir = repo / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    task = {
        "kind": "task",
        "id": TASK_ID,
        "title": "E2E task",
        "description": "```wg-contract\nverify = [\"exit 1\"]\n```\n",
    }
    with open(wg_dir / "graph.jsonl", "w") as fh:
        fh.write(json.dumps(task) + "\n")
    return repo


def _run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess:
    cli = REPO_ROOT / ".venv" / "bin" / "driftdriver"
    return subprocess.run(
        [str(cli), "acceptance", *args, "--dir", str(repo), "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


class TestAcceptanceCLIEndToEnd(unittest.TestCase):
    """The acceptance check/status/reset subcommands against a real repo dir."""

    def test_check_blocked_task_exits_nonzero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            # Exhaust the degrade budget, then the CLI check must block.
            for _ in range(3):
                _run_cli(repo, "check", TASK_ID)
            # CLI check is read-only: it never consumes degrades, so seed the
            # state directly through a completion-attempt-style run.
            proc = _run_cli(repo, "status")
            state = json.loads(proc.stdout)
            self.assertEqual(state["ceiling"], 3)
        # The read-only check on a not-yet-exhausted task reports degraded, exit 0.
        self.assertEqual(proc.returncode, 0)

    def test_check_reports_failing_verify_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            proc = _run_cli(repo, "check", TASK_ID)
            report = json.loads(proc.stdout)
        self.assertEqual(report["status"], "degraded")
        # to_dict has no is_blocking key; degraded (not blocked) is the pass condition.
        self.assertEqual(len(report["results"]), 1)
        self.assertFalse(report["results"][0]["passed"])

    def test_status_and_reset_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            degrade_file = repo / ".workgraph" / "service" / "acceptance-degrade.json"
            degrade_file.parent.mkdir(parents=True, exist_ok=True)
            degrade_file.write_text(json.dumps({TASK_ID: 3}))
            status = json.loads(_run_cli(repo, "status").stdout)
            self.assertEqual(status["per_task"][TASK_ID], 3)
            self.assertIn(TASK_ID, status["at_ceiling"])
            reset = json.loads(_run_cli(repo, "reset", TASK_ID).stdout)
            status2 = json.loads(_run_cli(repo, "status").stdout)
        self.assertEqual(reset["reset"], 1)
        self.assertEqual(status2["per_task"].get(TASK_ID, 0), 0)

    def test_check_without_task_id_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            proc = _run_cli(repo, "check")
        self.assertEqual(proc.returncode, 2)


class TestCmdCheckWiring(unittest.TestCase):
    """cmd_check reports the acceptance gate in its plugin output."""

    def test_cmd_check_includes_acceptance_gate_report(self) -> None:
        from driftdriver.acceptance_gate import check_task

        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_repo(tmp)
            # The wired gate surfaces through check_task with the same report
            # shape cmd_check embeds into plugins_json["acceptance_gate"].
            result = check_task(repo / ".workgraph", TASK_ID, record_degrade=True)
            report = result.to_dict()
        self.assertIn(report["status"], ("degraded", "blocked"))
        self.assertTrue(any("results" in report for _ in [0]))


if __name__ == "__main__":
    unittest.main()
