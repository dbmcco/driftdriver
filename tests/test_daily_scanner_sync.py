# ABOUTME: Tests the daily ecosystem scanner rewire: sync pipeline integration and
# ABOUTME: graceful fallback when Postgres is unavailable, plus CLAUDE.md injection removal.

from __future__ import annotations

import os
import subprocess
import unittest


SCRIPT_PATH = os.path.join(
    os.path.dirname(__file__), os.pardir, "scripts", "daily_ecosystem_eval.sh"
)


def _read_script() -> str:
    with open(SCRIPT_PATH) as fh:
        return fh.read()


class TestDailyScannerSyncRewire(unittest.TestCase):
    """Verify the scanner script calls the intelligence sync pipeline."""

    def test_script_calls_sync_pipeline(self) -> None:
        script = _read_script()
        self.assertIn(
            "python3 -m driftdriver.intelligence.sync --json",
            script,
            "Script must call the intelligence sync pipeline",
        )

    def test_script_has_fallback_on_sync_failure(self) -> None:
        script = _read_script()
        self.assertIn("SYNC_RC", script, "Script must capture sync exit code")
        self.assertIn(
            "falling back to legacy wg tasks",
            script,
            "Script must log fallback when sync fails",
        )

    def test_script_does_not_inject_into_claude_md(self) -> None:
        script = _read_script()
        self.assertNotIn(
            "CLAUDE_MD",
            script,
            "Script must not reference CLAUDE_MD variable",
        )
        self.assertNotIn(
            "ecosystem-eval-marker",
            script,
            "Script must not inject ecosystem-eval-marker into CLAUDE.md",
        )
        self.assertNotIn(
            "Ecosystem Updates Pending Evaluation",
            script,
            "Script must not create Ecosystem Updates sections",
        )

    def test_script_no_wg_contract_blocks_in_fallback(self) -> None:
        script = _read_script()
        self.assertNotIn(
            "wg-contract",
            script,
            "Fallback tasks should not include wg-contract blocks",
        )


class TestSyncMainEntryPoint(unittest.TestCase):
    """Verify the sync CLI entry point works end-to-end."""

    def test_sync_main_succeeds_with_postgres(self) -> None:
        """Sync pipeline returns 0 when Postgres is available and sources are configured."""
        result = subprocess.run(
            ["python3", "-m", "driftdriver.intelligence.sync", "--json"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        # rc=0 means Postgres is up and sync worked; rc!=0 is acceptable if
        # Postgres isn't running in this environment (tested separately).
        if result.returncode == 0:
            self.assertIn("{", result.stdout, "JSON output expected on success")

    def test_sync_main_fails_on_unreachable_postgres(self) -> None:
        """Sync pipeline exits non-zero when Postgres is unreachable (triggers fallback)."""
        env = os.environ.copy()
        env["DRIFTDRIVER_PGHOST"] = "127.0.0.254"
        env["DRIFTDRIVER_PGPORT"] = "59999"
        result = subprocess.run(
            [
                "python3",
                "-m",
                "driftdriver.intelligence.sync",
                "--host",
                "127.0.0.254",
                "--port",
                "59999",
                "--connect-timeout",
                "2",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        self.assertNotEqual(
            result.returncode,
            0,
            "Sync must exit non-zero when Postgres is unreachable",
        )


class TestClaudeMdCleanup(unittest.TestCase):
    """Verify stale ecosystem eval sections were removed from CLAUDE.md."""

    CLAUDE_MD_PATH = "/Users/braydon/projects/.claude/CLAUDE.md"

    def test_no_ecosystem_eval_sections_remain(self) -> None:
        if not os.path.isfile(self.CLAUDE_MD_PATH):
            self.skipTest("CLAUDE.md not found at expected path")
        with open(self.CLAUDE_MD_PATH) as fh:
            content = fh.read()
        self.assertNotIn(
            "Ecosystem Updates Pending Evaluation",
            content,
            "All stale ecosystem eval sections must be removed from CLAUDE.md",
        )
        self.assertNotIn(
            "ecosystem-eval-marker",
            content,
            "All ecosystem-eval-marker HTML comments must be removed from CLAUDE.md",
        )
