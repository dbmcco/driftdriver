# ABOUTME: Tests for qadrift speedrift lane — test quality evaluation.
# ABOUTME: Verifies finding detection for untested modules, mocks, false confidence, and missing integration.
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driftdriver.qadrift import (
    QAFinding,
    QAReport,
    check_false_confidence,
    check_integration_coverage,
    check_mock_usage,
    find_untested_modules,
    format_report,
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


if __name__ == "__main__":
    unittest.main()
