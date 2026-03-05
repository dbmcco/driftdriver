from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.secdrift import emit_security_review_tasks, run_secdrift_scan


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


if __name__ == "__main__":
    unittest.main()

