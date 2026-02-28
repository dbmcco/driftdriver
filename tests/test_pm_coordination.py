# ABOUTME: Tests for PM coordination mode - workgraph-driven agent orchestration
# ABOUTME: Covers task dispatch planning, prompt formatting, and pipeline chaining

from __future__ import annotations

import unittest

from driftdriver.pm_coordination import (
    CoordinationPlan,
    WorkerAssignment,
    check_newly_ready,
    format_task_prompt,
    parse_ready_output,
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


class ParseReadyOutputTests(unittest.TestCase):
    def test_parse_ready_output_extracts_tasks(self) -> None:
        output = "Ready tasks:\n  fix-bug - Fix the login bug\n  add-feature - Add dark mode\n"
        tasks = parse_ready_output(output)
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0]["id"], "fix-bug")
        self.assertEqual(tasks[1]["title"], "Add dark mode")

    def test_parse_ready_output_empty(self) -> None:
        self.assertEqual(parse_ready_output(""), [])
        self.assertEqual(parse_ready_output("No tasks ready"), [])

    def test_parse_ready_output_single_task(self) -> None:
        output = "Ready tasks:\n  my-task - Do the thing\n"
        tasks = parse_ready_output(output)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], "my-task")
        self.assertEqual(tasks[0]["title"], "Do the thing")
        self.assertEqual(tasks[0]["description"], "")


class CheckNewlyReadyFilterTests(unittest.TestCase):
    def test_check_newly_ready_filters_known(self) -> None:
        all_tasks = [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]
        known = {"a"}
        result = [t for t in all_tasks if t["id"] not in known]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "b")

    def test_check_newly_ready_returns_empty_when_all_known(self) -> None:
        all_tasks = [{"id": "a", "title": "A"}]
        known = {"a"}
        result = [t for t in all_tasks if t["id"] not in known]
        self.assertEqual(result, [])


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


if __name__ == "__main__":
    unittest.main()
