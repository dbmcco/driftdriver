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
    AutopilotConfig,
    AutopilotRun,
    WorkerContext,
    build_decompose_prompt,
    build_review_prompt,
    build_worker_prompt,
    discover_session_driver,
    generate_report,
    run_autopilot_loop,
    should_escalate,
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


class TestDryRun(unittest.TestCase):
    @patch("driftdriver.project_autopilot.get_ready_tasks")
    def test_dry_run_does_not_dispatch(self, mock_ready):
        mock_ready.side_effect = [
            [{"id": "t1", "title": "Test task", "description": ""}],
            [],  # second call returns empty
        ]
        config = AutopilotConfig(
            project_dir=Path("/project"),
            goal="Test",
            dry_run=True,
        )
        run = AutopilotRun(config=config)
        result = run_autopilot_loop(run)
        self.assertIn("t1", result.completed_tasks)
        self.assertEqual(len(result.workers), 0)


if __name__ == "__main__":
    unittest.main()
