from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.plandrift import emit_plan_review_tasks, run_as_lane, run_workgraph_plan_review


class PlanDriftTests(unittest.TestCase):
    def test_run_workgraph_plan_review_detects_testing_loopback_and_continuation_gaps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            wg = repo / ".workgraph"
            wg.mkdir(parents=True, exist_ok=True)
            graph = wg / "graph.jsonl"
            rows = [
                {
                    "type": "task",
                    "id": "impl-auth",
                    "title": "Implement auth API",
                    "status": "in-progress",
                    "after": [],
                    "tags": ["feature"],
                },
                {
                    "type": "task",
                    "id": "test-auth",
                    "title": "Integration test auth API",
                    "status": "ready",
                    "after": ["impl-auth"],
                    "tags": ["test", "integration"],
                },
            ]
            graph.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            report = run_workgraph_plan_review(
                repo_name="demo",
                repo_path=repo,
                repo_snapshot={"blocked_open": 0, "missing_dependencies": 0},
                policy_cfg={
                    "require_integration_tests": True,
                    "require_e2e_tests": False,
                    "require_failure_loopbacks": True,
                    "require_continuation_edges": True,
                },
            )

            summary = report.get("summary") or {}
            self.assertGreaterEqual(int(summary.get("findings_total") or 0), 2)
            categories = {str(row.get("category") or "") for row in report.get("top_findings") or []}
            self.assertIn("missing-failure-loopback", categories)
            self.assertIn("continuation-bridge-gap", categories)
            model_contract = report.get("model_contract") or {}
            self.assertEqual(model_contract.get("review_loop_mode"), "trycycle-inspired")
            self.assertTrue(model_contract.get("fresh_reviewer_required"))
            self.assertEqual(model_contract.get("review_rounds"), 2)
            self.assertIn("fresh reviewer perspective", str(model_contract.get("prompt_seed") or ""))

    def test_emit_plan_review_tasks_creates_and_reuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph").mkdir(parents=True, exist_ok=True)
            report = {
                "recommended_reviews": [
                    {
                        "fingerprint": "abc1234567890def",
                        "severity": "high",
                        "category": "missing-intervening-tests",
                        "title": "Missing test gates",
                        "evidence": "task=impl-auth",
                        "recommendation": "add tests",
                        "model_prompt": "prompt one",
                    },
                    {
                        "fingerprint": "def1234567890abc",
                        "severity": "medium",
                        "category": "continuation-bridge-gap",
                        "title": "Missing continuation",
                        "evidence": "in_progress=2",
                        "recommendation": "add continuation",
                        "model_prompt": "prompt two",
                    },
                ]
            }
            responses = [
                subprocess.CompletedProcess(["wg"], 1, "", "not found"),
                subprocess.CompletedProcess(["wg"], 0, "", ""),
                subprocess.CompletedProcess(["wg"], 0, "{}", ""),
            ]

            def _fake_run(_cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
                return responses.pop(0)

            with patch("driftdriver.plandrift.subprocess.run", side_effect=_fake_run):
                out = emit_plan_review_tasks(repo_path=repo, report=report, max_tasks=2)

            self.assertEqual(out["attempted"], 2)
            self.assertEqual(out["created"], 1)
            self.assertEqual(out["existing"], 1)
            self.assertEqual(len(out["errors"]), 0)


    def test_run_as_lane_returns_lane_result(self) -> None:
        """run_as_lane returns a valid LaneResult that passes contract validation."""
        from driftdriver.lane_contract import LaneResult, validate_lane_output

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            wg = repo / ".workgraph"
            wg.mkdir(parents=True, exist_ok=True)
            graph = wg / "graph.jsonl"
            rows = [
                {
                    "type": "task",
                    "id": "impl-login",
                    "title": "Implement login feature",
                    "status": "in-progress",
                    "after": [],
                    "tags": ["feature"],
                },
            ]
            graph.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

            result = run_as_lane(repo)

            self.assertIsInstance(result, LaneResult)
            self.assertEqual(result.lane, "plandrift")
            self.assertIsInstance(result.findings, list)
            self.assertIsInstance(result.exit_code, int)
            self.assertGreater(len(result.findings), 0)
            self.assertEqual(result.exit_code, 1)

            # Verify it validates through the contract
            raw = json.dumps({
                "lane": result.lane,
                "findings": [
                    {"message": f.message, "severity": f.severity, "file": f.file, "line": f.line, "tags": f.tags}
                    for f in result.findings
                ],
                "exit_code": result.exit_code,
                "summary": result.summary,
            })
            validated = validate_lane_output(raw)
            self.assertIsNotNone(validated)
            self.assertEqual(validated.lane, "plandrift")

    def test_run_as_lane_empty_workgraph(self) -> None:
        """run_as_lane returns a clean result when no .workgraph exists."""
        from driftdriver.lane_contract import LaneResult

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            # No .workgraph directory — should get no findings (graph missing is an error, not a finding)

            result = run_as_lane(repo)

            self.assertIsInstance(result, LaneResult)
            self.assertEqual(result.lane, "plandrift")
            self.assertEqual(len(result.findings), 0)
            self.assertEqual(result.exit_code, 0)

    def test_run_as_lane_handles_exception(self) -> None:
        """run_as_lane returns an error LaneResult if run_workgraph_plan_review raises."""
        with patch("driftdriver.plandrift.run_workgraph_plan_review", side_effect=RuntimeError("boom")):
            result = run_as_lane(Path("/nonexistent"))

        self.assertEqual(result.lane, "plandrift")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].severity, "error")
        self.assertIn("boom", result.findings[0].message)


if __name__ == "__main__":
    unittest.main()
