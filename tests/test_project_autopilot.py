# ABOUTME: Tests for project autopilot core module
# ABOUTME: Covers goal decomposition, dispatch, drift parsing, escalation, and reporting

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.project_autopilot import (
    _normalize_finding,
    _run_command,
    _assistant_text_message_count,
    _last_assistant_text,
    AutopilotConfig,
    AutopilotRun,
    WorkerContext,
    build_decompose_prompt,
    build_review_prompt,
    build_worker_prompt,
    detect_recurring_findings,
    discover_session_driver,
    generate_report,
    get_wg_eval_scores,
    run_autopilot_loop,
    should_escalate,
    trigger_evolve,
)


class TestDiscoverSessionDriver(unittest.TestCase):
    def test_returns_none_when_not_found(self):
        with patch("glob.glob", return_value=[]):
            result = discover_session_driver()
            self.assertIsNone(result)

    def test_returns_latest_match(self):
        paths = [
            "/home/user/.claude/plugins/cache/superpowers-marketplace/"
            "claude-session-driver/0.9.0/scripts",
            "/home/user/.claude/plugins/cache/superpowers-marketplace/"
            "claude-session-driver/1.0.1/scripts",
        ]
        with patch("glob.glob", return_value=paths):
            result = discover_session_driver()
            self.assertIsNotNone(result)
            self.assertIn("1.0.1", str(result))


class TestCommandResolution(unittest.TestCase):
    @patch("pathlib.Path.exists", autospec=True)
    @patch("driftdriver.project_autopilot._binary_candidates", return_value=["/tmp/fake-wg"])
    @patch("driftdriver.project_autopilot.subprocess.run")
    def test_run_command_resolves_wg_without_path(self, mock_run, _mock_candidates, mock_exists):
        mock_exists.side_effect = lambda path: str(path) == "/tmp/fake-wg"
        mock_run.return_value = type("R", (), {"returncode": 0, "stdout": "ok", "stderr": ""})()

        result = _run_command(["wg", "ready"], cwd=Path("/project"))

        self.assertEqual(result.returncode, 0)
        self.assertEqual(mock_run.call_args.args[0][0], "/tmp/fake-wg")

    @patch("pathlib.Path.exists", return_value=False)
    @patch("driftdriver.project_autopilot._binary_candidates", return_value=[])
    @patch("driftdriver.project_autopilot.subprocess.run", side_effect=FileNotFoundError("missing"))
    def test_run_command_returns_127_when_binary_missing(self, _mock_run, _mock_candidates, _mock_exists):
        result = _run_command(["wg", "ready"], cwd=Path("/project"))
        self.assertEqual(result.returncode, 127)
        self.assertIn("missing", result.stderr)


class TestBuildPrompts(unittest.TestCase):
    def test_decompose_prompt_includes_goal(self):
        prompt = build_decompose_prompt("Build auth system", Path("/project"))
        self.assertIn("Build auth system", prompt)
        self.assertIn("/project", prompt)
        self.assertIn("wg add", prompt)

    def test_worker_prompt_includes_task(self):
        task = {"id": "auth-1", "title": "Add login", "description": "Build login form"}
        prompt = build_worker_prompt(task, Path("/project"))
        self.assertIn("auth-1", prompt)
        self.assertIn("Add login", prompt)
        self.assertIn("Build login form", prompt)
        self.assertIn("drifts check", prompt)
        self.assertIn("wg done", prompt)


class TestSessionLogHelpers(unittest.TestCase):
    def test_counts_only_assistant_text_messages(self):
        with tempfile.TemporaryDirectory() as td:
            log_file = Path(td) / "session.jsonl"
            log_file.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "thinking", "text": "x"}]}}),
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}),
                        json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "ignore"}]}}),
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "second"}]}}),
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(_assistant_text_message_count(log_file), 2)

    def test_returns_last_assistant_text_block(self):
        with tempfile.TemporaryDirectory() as td:
            log_file = Path(td) / "session.jsonl"
            log_file.write_text(
                "\n".join(
                    [
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "first"}]}}),
                        json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "second"}, {"type": "text", "text": "line"}]}}),
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(_last_assistant_text(log_file), "second\nline")


class TestEscalation(unittest.TestCase):
    def test_no_escalation_below_threshold(self):
        worker = WorkerContext(
            task_id="t1", task_title="Test", worker_name="w1",
            drift_fail_count=2,
        )
        self.assertFalse(should_escalate(worker, threshold=3))

    def test_escalation_at_threshold(self):
        worker = WorkerContext(
            task_id="t1", task_title="Test", worker_name="w1",
            drift_fail_count=3,
        )
        self.assertTrue(should_escalate(worker, threshold=3))

    def test_escalation_above_threshold(self):
        worker = WorkerContext(
            task_id="t1", task_title="Test", worker_name="w1",
            drift_fail_count=5,
        )
        self.assertTrue(should_escalate(worker, threshold=3))


class TestAutopilotRun(unittest.TestCase):
    def test_empty_run_report(self):
        config = AutopilotConfig(
            project_dir=Path("/project"),
            goal="Test goal",
        )
        run = AutopilotRun(config=config, started_at=100.0)
        report = generate_report(run)
        self.assertIn("Test goal", report)
        self.assertIn("**Completed**: 0", report)

    def test_report_with_completed_tasks(self):
        config = AutopilotConfig(
            project_dir=Path("/project"),
            goal="Build feature",
        )
        run = AutopilotRun(
            config=config,
            started_at=100.0,
            completed_tasks={"task-1", "task-2"},
        )
        report = generate_report(run)
        self.assertIn("task-1", report)
        self.assertIn("task-2", report)
        self.assertIn("**Completed**: 2", report)

    def test_report_with_escalated_tasks(self):
        config = AutopilotConfig(
            project_dir=Path("/project"),
            goal="Build feature",
        )
        ctx = WorkerContext(
            task_id="stuck-1",
            task_title="Stuck task",
            worker_name="w1",
            drift_findings=["finding: scope violation"],
            drift_fail_count=3,
        )
        run = AutopilotRun(
            config=config,
            started_at=100.0,
            escalated_tasks={"stuck-1"},
            workers={"stuck-1": ctx},
        )
        report = generate_report(run)
        self.assertIn("Escalated", report)
        self.assertIn("stuck-1", report)
        self.assertIn("scope violation", report)


class TestWorkerContext(unittest.TestCase):
    def test_default_status(self):
        ctx = WorkerContext(task_id="t1", task_title="Test", worker_name="w1")
        self.assertEqual(ctx.status, "pending")
        self.assertEqual(ctx.drift_fail_count, 0)
        self.assertEqual(ctx.drift_findings, [])

    def test_status_transitions(self):
        ctx = WorkerContext(task_id="t1", task_title="Test", worker_name="w1")
        ctx.status = "running"
        self.assertEqual(ctx.status, "running")
        ctx.status = "completed"
        self.assertEqual(ctx.status, "completed")


class TestReviewPrompt(unittest.TestCase):
    def test_review_prompt_includes_goal_and_tasks(self):
        config = AutopilotConfig(
            project_dir=Path("/project"),
            goal="Build auth system",
        )
        run = AutopilotRun(
            config=config,
            started_at=100.0,
            completed_tasks={"auth-1", "auth-2"},
        )
        prompt = build_review_prompt(run)
        self.assertIn("Build auth system", prompt)
        self.assertIn("auth-1", prompt)
        self.assertIn("auth-2", prompt)
        self.assertIn("Trace claims through code", prompt)
        self.assertIn("Distinguish delegation from absence", prompt)

    def test_review_prompt_includes_escalated_tasks(self):
        config = AutopilotConfig(
            project_dir=Path("/project"),
            goal="Build feature",
        )
        ctx = WorkerContext(
            task_id="stuck-1", task_title="Stuck", worker_name="w1",
            drift_findings=["finding: scope drift"],
            drift_fail_count=3,
        )
        run = AutopilotRun(
            config=config,
            started_at=100.0,
            escalated_tasks={"stuck-1"},
            workers={"stuck-1": ctx},
        )
        prompt = build_review_prompt(run)
        self.assertIn("stuck-1 (escalated)", prompt)
        self.assertIn("scope drift", prompt)

    def test_review_prompt_empty_run(self):
        config = AutopilotConfig(
            project_dir=Path("/project"),
            goal="Test",
        )
        run = AutopilotRun(config=config, started_at=100.0)
        prompt = build_review_prompt(run)
        self.assertIn("Test", prompt)
        self.assertIn("none", prompt)


class TestWgEvalScores(unittest.TestCase):
    def test_returns_none_message_when_no_eval_data(self):
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            (project_dir / ".workgraph" / "output").mkdir(parents=True)
            result = get_wg_eval_scores(project_dir, {"task-1"})
            self.assertIn("none", result)

    @patch("driftdriver.project_autopilot.subprocess.run")
    def test_picks_up_eval_score_from_output_file(self, mock_run):
        mock_run.return_value = type("R", (), {"returncode": 1, "stdout": ""})()
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            output_dir = project_dir / ".workgraph" / "output"
            output_dir.mkdir(parents=True)
            (output_dir / "task-1").write_text("avg_score: 0.85\nother stuff\n")
            result = get_wg_eval_scores(project_dir, {"task-1"})
            self.assertIn("task-1", result)
            self.assertIn("avg_score", result)

    @patch("driftdriver.project_autopilot.subprocess.run")
    def test_picks_up_eval_from_wg_show(self, mock_run):
        mock_run.return_value = type(
            "R", (), {"returncode": 0, "stdout": "Log:\n  evaluation score: 0.9 quality\n"}
        )()
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            (project_dir / ".workgraph" / "output").mkdir(parents=True)
            result = get_wg_eval_scores(project_dir, {"task-2"})
            self.assertIn("task-2", result)
            self.assertIn("evaluation score", result)

    def test_review_prompt_includes_wg_eval_section(self):
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            (project_dir / ".workgraph" / "output").mkdir(parents=True)
            config = AutopilotConfig(project_dir=project_dir, goal="Test")
            run = AutopilotRun(
                config=config, started_at=100.0,
                completed_tasks={"t1"},
            )
            with patch("driftdriver.project_autopilot.subprocess.run") as mock_run:
                mock_run.return_value = type("R", (), {"returncode": 1, "stdout": ""})()
                prompt = build_review_prompt(run)
            self.assertIn("Workgraph Evaluation Evidence", prompt)
            self.assertIn("Incorporate wg evaluation scores", prompt)


class TestDryRun(unittest.TestCase):
    @patch("driftdriver.peer_registry.subprocess.run")
    @patch("driftdriver.project_autopilot.get_ready_tasks")
    def test_dry_run_does_not_dispatch(self, mock_ready, mock_peer_run):
        mock_ready.side_effect = [
            [{"id": "t1", "title": "Test task", "description": ""}],
            [],  # second call returns empty
        ]
        mock_peer_run.return_value = type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        config = AutopilotConfig(
            project_dir=Path("/project"),
            goal="Test",
            dry_run=True,
        )
        run = AutopilotRun(config=config)
        result = run_autopilot_loop(run)
        self.assertIn("t1", result.completed_tasks)
        self.assertEqual(len(result.workers), 0)


class TestNormalizeFinding(unittest.TestCase):
    def test_strips_finding_prefix(self):
        result = _normalize_finding("finding: scope violation detected")
        self.assertEqual(result, "scope violation detected")

    def test_strips_colored_prefix(self):
        result = _normalize_finding("finding (yellow): missing contract block")
        self.assertEqual(result, "missing contract block")

    def test_strips_file_paths(self):
        result = _normalize_finding("finding: issue in src/auth/login.py:42")
        self.assertNotIn("src/auth/login.py:42", result)
        self.assertIn("issue in", result)

    def test_collapses_whitespace(self):
        result = _normalize_finding("finding:   too   many   spaces  ")
        self.assertEqual(result, "too many spaces")

    def test_empty_string(self):
        self.assertEqual(_normalize_finding(""), "")

    def test_case_insensitive(self):
        r1 = _normalize_finding("Finding: Scope Violation")
        r2 = _normalize_finding("finding: scope violation")
        self.assertEqual(r1, r2)


class TestDetectRecurringFindings(unittest.TestCase):
    def test_no_findings_returns_empty(self):
        config = AutopilotConfig(project_dir=Path("/project"), goal="Test")
        run = AutopilotRun(
            config=config,
            workers={
                "t1": WorkerContext(task_id="t1", task_title="A", worker_name="w1"),
            },
        )
        self.assertEqual(detect_recurring_findings(run, threshold=3), [])

    def test_below_threshold_returns_empty(self):
        config = AutopilotConfig(project_dir=Path("/project"), goal="Test")
        run = AutopilotRun(
            config=config,
            workers={
                "t1": WorkerContext(
                    task_id="t1", task_title="A", worker_name="w1",
                    drift_findings=["finding: scope violation"],
                ),
                "t2": WorkerContext(
                    task_id="t2", task_title="B", worker_name="w2",
                    drift_findings=["finding: scope violation"],
                ),
            },
        )
        self.assertEqual(detect_recurring_findings(run, threshold=3), [])

    def test_at_threshold_returns_key(self):
        config = AutopilotConfig(project_dir=Path("/project"), goal="Test")
        run = AutopilotRun(
            config=config,
            workers={
                "t1": WorkerContext(
                    task_id="t1", task_title="A", worker_name="w1",
                    drift_findings=["finding: scope violation"],
                ),
                "t2": WorkerContext(
                    task_id="t2", task_title="B", worker_name="w2",
                    drift_findings=["finding: scope violation"],
                ),
                "t3": WorkerContext(
                    task_id="t3", task_title="C", worker_name="w3",
                    drift_findings=["finding: scope violation"],
                ),
            },
        )
        result = detect_recurring_findings(run, threshold=3)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "scope violation")

    def test_normalizes_across_files(self):
        """Same finding from different files should collapse to one key."""
        config = AutopilotConfig(project_dir=Path("/project"), goal="Test")
        run = AutopilotRun(
            config=config,
            workers={
                "t1": WorkerContext(
                    task_id="t1", task_title="A", worker_name="w1",
                    drift_findings=["finding: missing tests in src/a.py:10"],
                ),
                "t2": WorkerContext(
                    task_id="t2", task_title="B", worker_name="w2",
                    drift_findings=["finding: missing tests in src/b.py:20"],
                ),
                "t3": WorkerContext(
                    task_id="t3", task_title="C", worker_name="w3",
                    drift_findings=["finding: missing tests in src/c.py:30"],
                ),
            },
        )
        result = detect_recurring_findings(run, threshold=3)
        self.assertEqual(len(result), 1)
        self.assertIn("missing tests in", result[0])

    def test_same_task_multiple_findings_counts_once(self):
        """Multiple identical findings in one task should not inflate the count."""
        config = AutopilotConfig(project_dir=Path("/project"), goal="Test")
        run = AutopilotRun(
            config=config,
            workers={
                "t1": WorkerContext(
                    task_id="t1", task_title="A", worker_name="w1",
                    drift_findings=[
                        "finding: scope violation",
                        "finding: scope violation",
                        "finding: scope violation",
                    ],
                ),
            },
        )
        # Only 1 task, so threshold=3 should not be met
        self.assertEqual(detect_recurring_findings(run, threshold=3), [])


class TestTriggerEvolve(unittest.TestCase):
    @patch("driftdriver.project_autopilot.subprocess.run")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("driftdriver.project_autopilot._binary_candidates", return_value=["/tmp/fake-wg"])
    def test_success(self, _mock_candidates, _mock_exists, mock_run):
        mock_run.return_value = type(
            "R", (), {"returncode": 0, "stdout": "Evolution applied", "stderr": "", "args": []}
        )()
        result = trigger_evolve(Path("/project"))
        self.assertTrue(result["triggered"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("Evolution applied", result["output"])

    @patch("driftdriver.project_autopilot.subprocess.run")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("driftdriver.project_autopilot._binary_candidates", return_value=["/tmp/fake-wg"])
    def test_failure(self, _mock_candidates, _mock_exists, mock_run):
        mock_run.return_value = type(
            "R", (), {"returncode": 1, "stdout": "", "stderr": "no workgraph", "args": []}
        )()
        result = trigger_evolve(Path("/project"))
        self.assertFalse(result["triggered"])
        self.assertEqual(result["exit_code"], 1)

    @patch("driftdriver.project_autopilot.subprocess.run")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("driftdriver.project_autopilot._binary_candidates", return_value=["/tmp/fake-wg"])
    def test_dry_run_flag(self, _mock_candidates, _mock_exists, mock_run):
        mock_run.return_value = type(
            "R", (), {"returncode": 0, "stdout": "dry run ok", "stderr": "", "args": []}
        )()
        trigger_evolve(Path("/project"), dry_run=True)
        cmd = mock_run.call_args.args[0]
        self.assertIn("--dry-run", cmd)

    @patch("driftdriver.project_autopilot.subprocess.run")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("driftdriver.project_autopilot._binary_candidates", return_value=["/tmp/fake-wg"])
    def test_strategy_flag(self, _mock_candidates, _mock_exists, mock_run):
        mock_run.return_value = type(
            "R", (), {"returncode": 0, "stdout": "ok", "stderr": "", "args": []}
        )()
        trigger_evolve(Path("/project"), strategy="mutation")
        cmd = mock_run.call_args.args[0]
        self.assertIn("--strategy", cmd)
        self.assertIn("mutation", cmd)

    @patch("driftdriver.project_autopilot.subprocess.run")
    @patch("pathlib.Path.exists", return_value=False)
    @patch("driftdriver.project_autopilot._binary_candidates", return_value=["/tmp/fake-wg"])
    def test_default_strategy_omits_flag(self, _mock_candidates, _mock_exists, mock_run):
        mock_run.return_value = type(
            "R", (), {"returncode": 0, "stdout": "ok", "stderr": "", "args": []}
        )()
        trigger_evolve(Path("/project"), strategy="all")
        cmd = mock_run.call_args.args[0]
        self.assertNotIn("--strategy", cmd)


if __name__ == "__main__":
    unittest.main()
