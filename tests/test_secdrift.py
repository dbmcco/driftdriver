from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.secdrift import (
    _map_severity,
    emit_security_review_tasks,
    run_as_lane,
    run_secdrift_scan,
)


class SecdriftTests(unittest.TestCase):
    def test_run_secdrift_scan_detects_secret_and_missing_lock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "secrets.py").write_text(
                "API_KEY = 'super-secret-token-value'\n",
                encoding="utf-8",
            )
            report = run_secdrift_scan(
                repo_name="demo",
                repo_path=repo,
                policy_cfg={"run_pentest": False, "allow_network_scans": False},
            )
            summary = report.get("summary") or {}
            self.assertGreaterEqual(int(summary.get("findings_total") or 0), 1)
            categories = {str(row.get("category") or "") for row in report.get("top_findings") or []}
            self.assertTrue("generic-secret-assignment" in categories or "node-lock-missing" in categories)

    def test_emit_security_review_tasks_creates_and_reuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph").mkdir(parents=True, exist_ok=True)
            report = {
                "recommended_reviews": [
                    {
                        "fingerprint": "abc1234567890def",
                        "severity": "high",
                        "category": "generic-secret-assignment",
                        "title": "Potential secret",
                        "evidence": "API_KEY",
                        "file": "src/main.py",
                        "recommendation": "rotate",
                        "model_prompt": "prompt",
                    },
                    {
                        "fingerprint": "fff1234567890def",
                        "severity": "medium",
                        "category": "node-lock-missing",
                        "title": "Lock missing",
                        "evidence": "package.json",
                        "file": "package.json",
                        "recommendation": "commit lock",
                        "model_prompt": "prompt",
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

            with patch("driftdriver.secdrift.subprocess.run", side_effect=_fake_run):
                out = emit_security_review_tasks(repo_path=repo, report=report, max_tasks=2)

            self.assertEqual(out["attempted"], 2)
            self.assertEqual(out["created"], 1)
            self.assertEqual(out["existing"], 1)
            self.assertEqual(len(out["errors"]), 0)


class RunAsLaneTests(unittest.TestCase):
    def test_run_as_lane_returns_lane_result(self) -> None:
        """run_as_lane returns a valid LaneResult that passes contract validation."""
        import json
        from driftdriver.lane_contract import LaneResult, validate_lane_output

        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            (project_dir / "package.json").write_text('{"name":"demo"}\n', encoding="utf-8")
            (project_dir / "src").mkdir(parents=True, exist_ok=True)
            (project_dir / "src" / "secrets.py").write_text(
                "API_KEY = 'super-secret-token-value'\n",
                encoding="utf-8",
            )

            result = run_as_lane(project_dir)

            self.assertIsInstance(result, LaneResult)
            self.assertEqual(result.lane, "secdrift")
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
            self.assertEqual(validated.lane, "secdrift")

            # Check severity mapping produces valid lane values
            severities = {f.severity for f in result.findings}
            self.assertTrue(severities.issubset({"info", "warning", "error", "critical"}))

    def test_run_as_lane_clean_repo(self) -> None:
        """run_as_lane returns exit_code 0 for a clean repo with no findings."""
        from driftdriver.lane_contract import LaneResult

        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            # Empty project — no secrets, no manifests, no findings expected

            result = run_as_lane(project_dir)

            self.assertIsInstance(result, LaneResult)
            self.assertEqual(result.lane, "secdrift")
            self.assertEqual(len(result.findings), 0)
            self.assertEqual(result.exit_code, 0)

    def test_run_as_lane_handles_exception(self) -> None:
        """run_as_lane returns an error LaneResult if run_secdrift_scan raises."""
        with patch("driftdriver.secdrift.run_secdrift_scan", side_effect=RuntimeError("boom")):
            result = run_as_lane(Path("/nonexistent"))

        self.assertEqual(result.lane, "secdrift")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(len(result.findings), 1)
        self.assertEqual(result.findings[0].severity, "error")
        self.assertIn("boom", result.findings[0].message)

    def test_map_severity_values(self) -> None:
        """Verify severity mapping from secdrift findings to lane contract levels."""
        self.assertEqual(_map_severity({"severity": "critical"}), "critical")
        self.assertEqual(_map_severity({"severity": "high"}), "error")
        self.assertEqual(_map_severity({"severity": "medium"}), "warning")
        self.assertEqual(_map_severity({"severity": "low"}), "info")
        self.assertEqual(_map_severity({"severity": "unknown"}), "info")
        self.assertEqual(_map_severity({}), "info")


if __name__ == "__main__":
    unittest.main()

