# ABOUTME: Tests for speedrift protocol compliance detection.
# ABOUTME: Detects agents working outside workgraph, missing drift checks, untracked commits.
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from driftdriver.protocol_compliance import (
    ComplianceReport,
    check_compliance,
)


class ComplianceTests(unittest.TestCase):
    def _setup_repo(self, tmp: Path, *, with_workgraph: bool = True) -> Path:
        """Create a git repo with optional .workgraph."""
        subprocess.run(["git", "init", str(tmp)], capture_output=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.email", "test@test.com"], capture_output=True)
        subprocess.run(["git", "-C", str(tmp), "config", "user.name", "Test"], capture_output=True)
        if with_workgraph:
            wg = tmp / ".workgraph"
            wg.mkdir()
            (wg / "tasks.json").write_text("[]")
            runtime = wg / "service" / "runtime"
            runtime.mkdir(parents=True)
        return tmp

    def test_clean_repo_passes(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            # Install drifts wrapper so it's fully compliant
            drifts = project / ".workgraph" / "drifts"
            drifts.mkdir()
            (drifts / "check").write_text("#!/bin/bash\necho ok")
            report = check_compliance(project)
            self.assertTrue(report.compliant)
            self.assertEqual(len(report.violations), 0)

    def test_no_workgraph_is_violation(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp), with_workgraph=False)
            report = check_compliance(project)
            self.assertFalse(report.compliant)
            self.assertTrue(any(v["kind"] == "missing_workgraph" for v in report.violations))

    def test_no_driftdriver_installed_is_violation(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            # Has .workgraph but no drifts/ wrapper
            report = check_compliance(project)
            self.assertTrue(any(v["kind"] == "missing_driftdriver" for v in report.violations))

    def test_driftdriver_installed_no_violation(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            drifts = project / ".workgraph" / "drifts"
            drifts.mkdir()
            (drifts / "check").write_text("#!/bin/bash\necho ok")
            report = check_compliance(project)
            self.assertFalse(any(v["kind"] == "missing_driftdriver" for v in report.violations))

    def test_commits_without_task_reference_flagged(self) -> None:
        with TemporaryDirectory() as tmp:
            project = self._setup_repo(Path(tmp))
            # Make a commit without any task reference
            (project / "file.py").write_text("x = 1")
            subprocess.run(["git", "-C", str(project), "add", "."], capture_output=True)
            subprocess.run(
                ["git", "-C", str(project), "commit", "-m", "random change with no task"],
                capture_output=True,
            )
            report = check_compliance(project, check_recent_commits=3)
            self.assertTrue(any(v["kind"] == "untasked_commit" for v in report.violations))


if __name__ == "__main__":
    unittest.main()
