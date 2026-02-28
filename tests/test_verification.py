# ABOUTME: Tests for silent failure monitor (verification.py)
# ABOUTME: Uses real temp git repos - no mocks.

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from driftdriver.verification import VerificationResult, verify_task_completion


def _init_repo(path: Path) -> None:
    """Set up a bare git repo with an initial commit."""
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    # Initial commit so HEAD exists
    (path / "README.md").write_text("# project\n")
    subprocess.run(["git", "add", "README.md"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)


def _add_pytest_cache(path: Path) -> None:
    cache = path / ".pytest_cache"
    cache.mkdir()
    (cache / "README.md").write_text("pytest cache\n")


def _stage_clean_change(path: Path, filename: str = "src.py") -> None:
    (path / filename).write_text("x = 1\n")
    subprocess.run(["git", "add", filename], cwd=path, check=True, capture_output=True)


def _commit_clean_change(path: Path, filename: str = "src.py") -> None:
    _stage_clean_change(path, filename)
    subprocess.run(["git", "commit", "-m", "add file"], cwd=path, check=True, capture_output=True)


class VerificationTests(unittest.TestCase):
    def test_verification_passes_when_all_checks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _init_repo(project)
            _add_pytest_cache(project)
            _commit_clean_change(project)

            result = verify_task_completion(project, {})

            self.assertIsInstance(result, VerificationResult)
            self.assertTrue(result.checks["tests_ran"])
            self.assertTrue(result.checks["diff_exists"])
            self.assertTrue(result.checks["no_todo_markers"])
            self.assertTrue(result.checks["contract_scope"])
            self.assertTrue(result.passed)
            self.assertEqual(result.blockers, [])

    def test_todo_markers_detected_in_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _init_repo(project)
            # Stage a file with a TODO marker (not yet committed = in diff vs HEAD)
            (project / "work.py").write_text("# TODO: finish this\nx = 1\n")
            subprocess.run(["git", "add", "work.py"], cwd=project, check=True, capture_output=True)

            result = verify_task_completion(project, {})

            self.assertFalse(result.checks["no_todo_markers"])
            self.assertTrue(any("TODO" in w for w in result.warnings))

    def test_empty_contract_passes_scope_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _init_repo(project)
            _commit_clean_change(project)

            result = verify_task_completion(project, {})

            self.assertTrue(result.checks["contract_scope"])

    def test_verification_reports_all_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            _init_repo(project)
            _commit_clean_change(project)

            result = verify_task_completion(project, {})

            self.assertIn("tests_ran", result.checks)
            self.assertIn("diff_exists", result.checks)
            self.assertIn("no_todo_markers", result.checks)
            self.assertIn("contract_scope", result.checks)
            self.assertIsInstance(result.warnings, list)
            self.assertIsInstance(result.blockers, list)


if __name__ == "__main__":
    unittest.main()
