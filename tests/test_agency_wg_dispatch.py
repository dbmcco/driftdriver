# ABOUTME: Tests for Agency-wg dispatch integration — enrichment and fallback paths.
# ABOUTME: Verifies agency-speedrift-wrap.py merging and claude-run.sh fallback behavior.

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

from driftdriver.install import install_claude_executor_support


class AgencySpeedriftWrapTests(unittest.TestCase):
    """Test the agency-speedrift-wrap.py prompt merger."""

    def setUp(self) -> None:
        self.wrap_script = (
            Path(__file__).parent.parent
            / "driftdriver"
            / "templates"
            / "executors"
            / "agency-speedrift-wrap.py"
        )

    def test_wrap_merges_composed_and_original(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("## Speedrift Protocol\nOriginal prompt content")
            original_path = f.name

        try:
            result = subprocess.run(
                ["python3", str(self.wrap_script), original_path],
                input="You are a specialist in data pipelines.",
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            output = result.stdout
            self.assertIn("Agency-Composed Agent Identity", output)
            self.assertIn("specialist in data pipelines", output)
            self.assertIn("Speedrift Protocol", output)
            self.assertIn("Original prompt content", output)
            # Agency identity should come before speedrift protocol
            agency_pos = output.index("Agency-Composed")
            speedrift_pos = output.index("Speedrift Protocol")
            self.assertLess(agency_pos, speedrift_pos)
        finally:
            os.unlink(original_path)

    def test_wrap_passes_through_on_empty_composition(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Original prompt only")
            original_path = f.name

        try:
            result = subprocess.run(
                ["python3", str(self.wrap_script), original_path],
                input="",
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "Original prompt only")
        finally:
            os.unlink(original_path)


class AgencyAssignWorkgraphTests(unittest.TestCase):
    """Test the agency-assign-workgraph script."""

    def setUp(self) -> None:
        self.assign_script = (
            Path(__file__).parent.parent
            / "driftdriver"
            / "templates"
            / "executors"
            / "agency-assign-workgraph"
        )

    def test_exits_nonzero_when_agency_unreachable(self) -> None:
        """When Agency is not running, the script should exit non-zero."""
        env = os.environ.copy()
        env["AGENCY_PORT"] = "19999"  # port nothing listens on
        env["AGENCY_HEALTH_TIMEOUT"] = "0.2"

        result = subprocess.run(
            [str(self.assign_script), "test-task-id", "test description"],
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_exits_nonzero_without_task_id(self) -> None:
        result = subprocess.run(
            [str(self.assign_script)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)


class ClaudeRunAgencyFallbackTests(unittest.TestCase):
    """Test that claude-run.sh falls back gracefully when Agency is down."""

    def test_install_copies_agency_helpers(self) -> None:
        """install_claude_executor_support deploys agency-assign-workgraph and wrap script."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_claude_executor_support(wg_dir)

            assign = wg_dir / "executors" / "agency-assign-workgraph"
            wrap = wg_dir / "executors" / "agency-speedrift-wrap.py"
            self.assertTrue(assign.exists())
            self.assertTrue(wrap.exists())
            self.assertTrue(assign.stat().st_mode & stat.S_IXUSR)

    def test_claude_run_skips_agency_with_env_flag(self) -> None:
        """WG_SKIP_AGENCY=1 should bypass Agency enrichment entirely."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_claude_executor_support(wg_dir)

            runner = wg_dir / "executors" / "claude-run.sh"
            content = runner.read_text(encoding="utf-8")
            self.assertIn("WG_SKIP_AGENCY", content)
            self.assertIn("agency-assign-workgraph", content)
            self.assertIn("agency-speedrift-wrap.py", content)


if __name__ == "__main__":
    unittest.main()
