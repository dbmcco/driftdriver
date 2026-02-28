# ABOUTME: Tests for PM coordination mode - workgraph-driven agent orchestration
# ABOUTME: Covers task dispatch planning, prompt formatting, and pipeline chaining

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.pm_coordination import (
    CoordinationPlan,
    WorkerAssignment,
    check_newly_ready,
    format_task_prompt,
    get_ready_tasks,
    plan_dispatch,
)


class WorkerAssignmentDefaultsTests(unittest.TestCase):
    def test_worker_assignment_defaults(self) -> None:
        assignment = WorkerAssignment(
            task_id="42",
            task_title="Build the thing",
            worker_name="wg-42",
        )
        self.assertIsNone(assignment.session_id)
        self.assertEqual(assignment.status, "pending")


class GetReadyTasksTests(unittest.TestCase):
    def test_get_ready_tasks_parses_output(self) -> None:
        fake_output = (
            "id: 1\ttitle: Fix the bug\tdescription: Some bug to fix\n"
            "id: 2\ttitle: Add feature\tdescription: New feature\n"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["wg", "ready"],
                returncode=0,
                stdout=fake_output,
                stderr="",
            )
            tasks = get_ready_tasks(Path("/fake/project"))

        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["id"], "1")
        self.assertEqual(tasks[0]["title"], "Fix the bug")
        self.assertEqual(tasks[1]["id"], "2")
        self.assertEqual(tasks[1]["title"], "Add feature")

    def test_get_ready_tasks_returns_empty_on_no_output(self) -> None:
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["wg", "ready"],
                returncode=0,
                stdout="",
                stderr="",
            )
            tasks = get_ready_tasks(Path("/fake/project"))

        self.assertEqual(tasks, [])


class PlanDispatchTests(unittest.TestCase):
    def test_plan_dispatch_respects_max_parallel(self) -> None:
        ready_tasks = [
            {"id": str(i), "title": f"Task {i}", "description": f"Do task {i}"}
            for i in range(1, 7)
        ]
        plan = plan_dispatch(ready_tasks, max_parallel=4)

        self.assertIsInstance(plan, CoordinationPlan)
        self.assertLessEqual(len(plan.assignments), 4)
        self.assertEqual(len(plan.assignments), 4)

    def test_plan_dispatch_names_workers_from_task_id(self) -> None:
        ready_tasks = [{"id": "99", "title": "Something", "description": "Do it"}]
        plan = plan_dispatch(ready_tasks, max_parallel=4)

        self.assertEqual(len(plan.assignments), 1)
        self.assertEqual(plan.assignments[0].worker_name, "wg-99")
        self.assertEqual(plan.assignments[0].task_id, "99")

    def test_plan_dispatch_fewer_tasks_than_max(self) -> None:
        ready_tasks = [{"id": "1", "title": "Only one", "description": "Solo task"}]
        plan = plan_dispatch(ready_tasks, max_parallel=4)

        self.assertEqual(len(plan.assignments), 1)


class FormatTaskPromptTests(unittest.TestCase):
    def test_format_task_prompt_includes_tdd_protocol(self) -> None:
        task = {"id": "7", "title": "Implement thing", "description": "Build the thing"}
        prompt = format_task_prompt(task)

        self.assertIn("TDD", prompt)
        self.assertIn("wg done", prompt)
        self.assertIn("7", prompt)
        self.assertIn("Implement thing", prompt)

    def test_format_task_prompt_includes_description(self) -> None:
        task = {"id": "3", "title": "My task", "description": "Detailed description here"}
        prompt = format_task_prompt(task)

        self.assertIn("Detailed description here", prompt)


class CheckNewlyReadyTests(unittest.TestCase):
    def test_check_newly_ready_filters_known(self) -> None:
        fake_output = (
            "id: 1\ttitle: Old task\tdescription: Already known\n"
            "id: 5\ttitle: New task\tdescription: Just became ready\n"
        )
        previously_known = {"1"}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["wg", "ready"],
                returncode=0,
                stdout=fake_output,
                stderr="",
            )
            new_tasks = check_newly_ready(Path("/fake/project"), previously_known)

        self.assertEqual(len(new_tasks), 1)
        self.assertEqual(new_tasks[0]["id"], "5")
        self.assertEqual(new_tasks[0]["title"], "New task")

    def test_check_newly_ready_returns_empty_when_all_known(self) -> None:
        fake_output = "id: 1\ttitle: Old task\tdescription: Already known\n"
        previously_known = {"1"}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["wg", "ready"],
                returncode=0,
                stdout=fake_output,
                stderr="",
            )
            new_tasks = check_newly_ready(Path("/fake/project"), previously_known)

        self.assertEqual(new_tasks, [])


if __name__ == "__main__":
    unittest.main()
