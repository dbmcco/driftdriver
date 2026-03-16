# ABOUTME: Integration test for the full Speedrift quality cycle.
# ABOUTME: Planner output → drift check → bridge writes evaluations.

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest import TestCase

from driftdriver.quality_planner import (
    PlannedTask,
    PlannerOutput,
    _parse_plan_output,
    load_repertoire,
    build_planner_prompt,
)
from driftdriver.wg_eval_bridge import (
    bridge_findings_to_evaluations,
    build_evaluation,
    severity_to_score,
    attribute_finding,
)
from driftdriver.evolverdrift import (
    check_graph_corruption,
    check_orphaned_tasks,
    run_as_lane,
)

from speedrift_lane_sdk.lane_contract import LaneFinding, LaneResult


class FullCycleIntegrationTests(TestCase):
    """Test the full planner → drift → bridge → evaluation cycle."""

    def setUp(self) -> None:
        self.td = tempfile.mkdtemp()
        self.repo = Path(self.td)
        # Create workgraph structure
        (self.repo / ".workgraph").mkdir()
        (self.repo / ".workgraph" / "agency" / "assignments").mkdir(parents=True)
        (self.repo / ".workgraph" / "agency" / "evaluations").mkdir(parents=True)

    def test_planner_output_has_contract_instruction_in_prompt(self) -> None:
        """The planner prompt instructs the LLM to emit wg-contract blocks."""
        prompt = build_planner_prompt(
            spec_content="Build a widget",
            north_star="Ship quality software",
            repertoire=load_repertoire(),
        )
        self.assertIn("wg-contract", prompt)
        self.assertIn("schema = 1", prompt)
        self.assertIn("objective", prompt)
        self.assertIn("acceptance", prompt)

    def test_planner_output_parses_verify_and_touch(self) -> None:
        """Planner correctly parses verify, touch, and acceptance from LLM output."""
        raw = json.dumps({
            "tasks": [
                {
                    "id": "impl-widget",
                    "title": "Implement widget",
                    "type": "code",
                    "risk": "medium",
                    "description": "Build the widget component",
                    "verify": "npm run typecheck",
                    "touch": ["src/widget.ts"],
                    "acceptance": ["Build passes", "Widget renders"],
                }
            ]
        })
        output = _parse_plan_output(raw)
        self.assertEqual(len(output.tasks), 1)
        task = output.tasks[0]
        self.assertEqual(task.verify, "npm run typecheck")
        self.assertEqual(task.touch, ["src/widget.ts"])
        self.assertEqual(task.acceptance, ["Build passes", "Widget renders"])

    def test_bridge_writes_evaluation_from_drift_finding(self) -> None:
        """Bridge converts a drift finding with task tag into a WG evaluation file."""
        # Create an assignment file for the task
        assignment = self.repo / ".workgraph" / "agency" / "assignments" / "impl-widget.yaml"
        assignment.write_text("task_id: impl-widget\ncomposition_id: role-abc\nagent_id: agent-1\n")

        # Simulate a coredrift finding tagged to the task
        finding = LaneFinding(
            message="Task exceeded touch set: added 3 files not in contract",
            severity="warning",
            tags=["task:impl-widget", "scope-drift"],
        )
        lane_result = LaneResult(
            lane="coredrift",
            findings=[finding],
            exit_code=1,
            summary="coredrift: 1 warning",
        )

        report = bridge_findings_to_evaluations(self.repo, [lane_result])
        self.assertEqual(report.evaluations_written, 1)
        self.assertEqual(report.unattributable_findings, 0)

        # Verify the evaluation file was written
        evals_dir = self.repo / ".workgraph" / "agency" / "evaluations"
        eval_files = list(evals_dir.glob("eval-drift-*.json"))
        self.assertEqual(len(eval_files), 1)

        eval_data = json.loads(eval_files[0].read_text())
        self.assertEqual(eval_data["evaluator"], "speedrift:coredrift")
        self.assertEqual(eval_data["source"], "drift")
        self.assertEqual(eval_data["task_id"], "impl-widget")
        self.assertEqual(eval_data["role_id"], "role-abc")
        self.assertIn("correctness", eval_data["dimensions"])
        self.assertAlmostEqual(eval_data["dimensions"]["correctness"], 0.5)  # warning = 0.5

    def test_unattributable_findings_are_counted(self) -> None:
        """Findings without task tags are counted but not bridged."""
        finding = LaneFinding(
            message="Repo has no North Star declaration",
            severity="info",
            tags=["repo-level"],
        )
        result = LaneResult(lane="northstardrift", findings=[finding], exit_code=1, summary="")
        report = bridge_findings_to_evaluations(self.repo, [result])
        self.assertEqual(report.evaluations_written, 0)
        self.assertEqual(report.unattributable_findings, 1)

    def test_evolverdrift_detects_graph_corruption(self) -> None:
        """evolverdrift detects duplicate IDs and orphan deps in graph.jsonl."""
        graph = self.repo / ".workgraph" / "graph.jsonl"
        graph.write_text(
            json.dumps({"id": "task-a", "status": "done"}) + "\n"
            + json.dumps({"id": "task-a", "status": "open"}) + "\n"  # duplicate
            + json.dumps({"id": "task-b", "status": "open", "after": ["task-missing"]}) + "\n"  # orphan dep
        )
        findings = check_graph_corruption(self.repo)
        messages = [f.message for f in findings]
        self.assertTrue(any("Duplicate" in m for m in messages))
        self.assertTrue(any("Orphan" in m or "task-missing" in m for m in messages))

    def test_full_cycle_planner_to_evaluations(self) -> None:
        """Simulate the full cycle: plan output → drift findings → bridge → evaluations."""
        # 1. Simulate planner output (what the LLM would return)
        plan_data = {
            "tasks": [
                {"id": "feat-auth", "title": "Add authentication", "type": "code", "risk": "high",
                 "description": "Implement JWT auth with login and token refresh",
                 "verify": "npm test", "touch": ["src/auth.ts"], "acceptance": ["Tests pass"]},
                {"id": "test-auth", "title": "Test authentication", "type": "quality-gate", "risk": "medium",
                 "after": ["feat-auth"], "pattern": "e2e-breakfix", "max_iterations": 3,
                 "description": "Run auth E2E tests"},
                {"id": "checkpoint-1", "title": "NorthStar check", "type": "northstar-checkpoint",
                 "after": ["test-auth"], "description": "Verify alignment"},
            ]
        }
        raw_plan = json.dumps(plan_data)
        plan = _parse_plan_output(raw_plan)
        self.assertEqual(len(plan.tasks), 3)
        self.assertEqual(plan.tasks[0].verify, "npm test")
        self.assertIn("Implement JWT", plan.tasks[0].description)

        # 2. Simulate drift findings after agent completes feat-auth
        assignment = self.repo / ".workgraph" / "agency" / "assignments" / "feat-auth.yaml"
        assignment.write_text("task_id: feat-auth\ncomposition_id: role-dev\nagent_id: agent-2\n")

        findings = [
            LaneFinding(message="Added OAuth not in contract", severity="warning", tags=["task:feat-auth", "scope"]),
            LaneFinding(message="Repo health OK", severity="info", tags=["repo-level"]),  # unattributable
        ]
        lane_results = [
            LaneResult(lane="coredrift", findings=findings, exit_code=1, summary="coredrift: 1 warning"),
        ]

        # 3. Bridge writes evaluations
        report = bridge_findings_to_evaluations(self.repo, lane_results)
        self.assertEqual(report.evaluations_written, 1)
        self.assertEqual(report.unattributable_findings, 1)

        # 4. Verify evaluation file is consumable by evolver
        evals_dir = self.repo / ".workgraph" / "agency" / "evaluations"
        eval_files = list(evals_dir.glob("*.json"))
        self.assertEqual(len(eval_files), 1)

        eval_data = json.loads(eval_files[0].read_text())
        # Evolver expects these fields
        for required_field in ("id", "task_id", "role_id", "score", "dimensions", "evaluator", "source"):
            self.assertIn(required_field, eval_data, f"Missing required field: {required_field}")
        self.assertTrue(eval_data["evaluator"].startswith("speedrift:"))
        self.assertEqual(eval_data["source"], "drift")
