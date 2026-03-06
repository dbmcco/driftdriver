# ABOUTME: Tests for qadrift speedrift lane — test quality evaluation.
# ABOUTME: Verifies finding detection for untested modules, mocks, false confidence, and missing integration.
from __future__ import annotations

import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from driftdriver.qadrift import (
    QAFinding,
    QAReport,
    check_false_confidence,
    check_integration_coverage,
    check_mock_usage,
    emit_quality_review_tasks,
    find_untested_modules,
    format_report,
    run_program_quality_scan,
    run_qa_check,
)


class QADriftTests(unittest.TestCase):
    def test_find_untested_modules_detects_missing_tests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            src_dir = project_dir / "src"
            src_dir.mkdir()
            (src_dir / "mymodule.py").write_text("def hello(): pass\n")
            # No corresponding test file

            findings = find_untested_modules(project_dir)

            self.assertTrue(len(findings) > 0)
            self.assertTrue(any(f.category == "coverage-gap" for f in findings))
            self.assertTrue(any("mymodule" in f.file for f in findings))

    def test_check_mock_usage_finds_unittest_mock(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            tests_dir = project_dir / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_something.py").write_text(
                "from unittest.mock import MagicMock\ndef test_foo(): pass\n"
            )

            findings = check_mock_usage(project_dir)

            self.assertTrue(len(findings) > 0)
            self.assertTrue(any(f.category == "mock-violation" for f in findings))
            self.assertTrue(any("test_something" in f.file for f in findings))

    def test_check_false_confidence_detects_import_only(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            tests_dir = project_dir / "tests"
            tests_dir.mkdir()
            (tests_dir / "test_imports.py").write_text(
                "import mymodule\n\ndef test_module_importable():\n    assert True\n"
            )

            findings = check_false_confidence(project_dir)

            self.assertTrue(len(findings) > 0)
            self.assertTrue(any(f.category == "false-confidence" for f in findings))
            self.assertTrue(any("test_imports" in f.file for f in findings))

    def test_check_integration_coverage_flags_subprocess(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            src_dir = project_dir / "src"
            src_dir.mkdir()
            (src_dir / "runner.py").write_text(
                "import subprocess\n\ndef run():\n    subprocess.check_call(['ls'])\n"
            )
            # No integration tests directory

            findings = check_integration_coverage(project_dir)

            self.assertTrue(len(findings) > 0)
            self.assertTrue(any(f.category == "missing-integration" for f in findings))
            self.assertTrue(any("runner" in f.file for f in findings))

    def test_run_qa_check_calculates_score(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            src_dir = project_dir / "src"
            src_dir.mkdir()
            (src_dir / "mymodule.py").write_text("def hello(): pass\n")

            report = run_qa_check(project_dir)

            self.assertIsInstance(report, QAReport)
            self.assertGreater(report.drift_score, 0.0)
            self.assertLessEqual(report.drift_score, 1.0)

    def test_format_report_includes_summary(self) -> None:
        report = QAReport(
            findings=[
                QAFinding(
                    file="src/foo.py",
                    category="coverage-gap",
                    severity="HIGH",
                    description="No tests for foo.py",
                )
            ],
            drift_score=0.2,
            modules_tested=0,
            modules_untested=1,
            mock_count=0,
            summary="1 HIGH finding",
        )

        output = format_report(report)

        self.assertIn("coverage-gap", output)
        self.assertIn("0.2", output)


class QADriftWrapperTests(unittest.TestCase):
    def test_qadrift_wrapper_no_cd(self) -> None:
        wrapper = (
            Path(__file__).parent.parent
            / "driftdriver"
            / "templates"
            / "qadrift_wrapper.sh"
        )
        content = wrapper.read_text(encoding="utf-8")
        self.assertNotIn("cd ", content, "qadrift_wrapper.sh must not contain a 'cd' command")

    def test_qadrift_finds_nested_tests(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            src_dir = project_dir / "src"
            src_dir.mkdir()
            (src_dir / "foo.py").write_text("def foo(): pass\n")

            # Test file is nested in a subdirectory
            subdir = project_dir / "tests" / "subdir"
            subdir.mkdir(parents=True)
            (subdir / "test_foo.py").write_text("def test_foo(): assert True\n")

            findings = find_untested_modules(project_dir)

            coverage_gap_files = [f.file for f in findings if f.category == "coverage-gap"]
            self.assertFalse(
                any("foo" in f for f in coverage_gap_files),
                f"foo should be considered tested via nested test file, but got gaps: {coverage_gap_files}",
            )


class QADriftInstallTests(unittest.TestCase):
    def test_write_qadrift_wrapper_creates_file(self) -> None:
        """write_qadrift_wrapper writes an executable .workgraph/qadrift wrapper."""
        from driftdriver.install import write_qadrift_wrapper

        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            wrote = write_qadrift_wrapper(wg_dir)

            self.assertTrue(wrote)
            self.assertTrue((wg_dir / "qadrift").exists())


class ProgramQADriftTests(unittest.TestCase):
    def test_run_program_quality_scan_detects_risk(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "app.py").write_text("def run(): return 1\n", encoding="utf-8")
            report = run_program_quality_scan(
                repo_name="demo",
                repo_path=repo,
                repo_snapshot={
                    "stalled": True,
                    "stall_reasons": ["no active executor"],
                    "missing_dependencies": 1,
                    "blocked_open": 1,
                    "workgraph_exists": True,
                    "service_running": False,
                    "in_progress": [],
                    "ready": [{"id": "r1"}],
                },
                policy_cfg={"include_playwright": False},
            )
            summary = report.get("summary") or {}
            self.assertTrue(bool(summary.get("at_risk")))
            self.assertGreater(int(summary.get("findings_total") or 0), 0)

    def test_emit_quality_review_tasks_creates_and_reuses(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            (repo / ".workgraph").mkdir(parents=True, exist_ok=True)
            report = {
                "recommended_reviews": [
                    {
                        "fingerprint": "abc1234567890def",
                        "severity": "high",
                        "category": "work-stalled",
                        "title": "Stalled repo",
                        "evidence": "no executor",
                        "recommendation": "unblock",
                        "model_prompt": "prompt",
                    },
                    {
                        "fingerprint": "fff1234567890def",
                        "severity": "medium",
                        "category": "tests-missing",
                        "title": "Missing tests",
                        "evidence": "src only",
                        "recommendation": "add tests",
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

            with patch("driftdriver.qadrift.subprocess.run", side_effect=_fake_run):
                out = emit_quality_review_tasks(repo_path=repo, report=report, max_tasks=2)

            self.assertEqual(out["attempted"], 2)
            self.assertEqual(out["created"], 1)
            self.assertEqual(out["existing"], 1)
            self.assertEqual(len(out["errors"]), 0)


if __name__ == "__main__":
    unittest.main()
