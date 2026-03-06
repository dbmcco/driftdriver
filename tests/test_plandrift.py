from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.plandrift import emit_plan_review_tasks, run_workgraph_plan_review


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


if __name__ == "__main__":
    unittest.main()
