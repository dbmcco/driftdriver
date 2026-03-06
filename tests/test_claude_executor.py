# ABOUTME: Tests for the default Claude executor assets installed by driftdriver.
# ABOUTME: Covers wrapper/timeout installation and legacy claude.toml patching.

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

import tomllib

from driftdriver.install import ensure_executor_guidance, install_claude_executor_support


class ClaudeExecutorInstallTests(unittest.TestCase):
    def test_install_claude_executor_support_copies_runner_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            wrote_runner, wrote_timeout = install_claude_executor_support(wg_dir)
            self.assertTrue(wrote_runner)
            self.assertTrue(wrote_timeout)

            runner = wg_dir / "executors" / "claude-run.sh"
            timeout = wg_dir / "bin" / "timeout"
            self.assertTrue(runner.exists())
            self.assertTrue(timeout.exists())
            self.assertTrue(runner.stat().st_mode & stat.S_IXUSR)
            self.assertTrue(timeout.stat().st_mode & stat.S_IXUSR)

    def test_install_claude_executor_support_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_claude_executor_support(wg_dir)
            wrote_runner, wrote_timeout = install_claude_executor_support(wg_dir)
            self.assertFalse(wrote_runner)
            self.assertFalse(wrote_timeout)

    def test_ensure_executor_guidance_creates_wrapper_backed_claude_toml(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            created, patched = ensure_executor_guidance(
                wg_dir,
                include_archdrift=False,
                include_uxdrift=False,
                include_therapydrift=False,
                include_fixdrift=False,
                include_yagnidrift=False,
                include_redrift=False,
            )

            self.assertTrue(created)
            self.assertEqual(patched, [])

            claude_toml = wg_dir / "executors" / "claude.toml"
            data = tomllib.loads(claude_toml.read_text(encoding="utf-8"))
            executor = data["executor"]
            self.assertEqual(executor["command"], ".workgraph/executors/claude-run.sh")
            self.assertEqual(executor["args"], [])
            self.assertTrue((wg_dir / "executors" / "claude-run.sh").exists())
            self.assertTrue((wg_dir / "bin" / "timeout").exists())

    def test_ensure_executor_guidance_patches_legacy_claude_command(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            executors_dir = wg_dir / "executors"
            executors_dir.mkdir(parents=True, exist_ok=True)
            claude_toml = executors_dir / "claude.toml"
            claude_toml.write_text(
                (
                    "[executor]\n"
                    'type = "claude"\n'
                    'command = "claude"\n'
                    'args = ["--print", "--dangerously-skip-permissions", "--no-session-persistence"]\n\n'
                    "[executor.prompt_template]\n"
                    'template = """Task: {{task_id}}"""\n'
                ),
                encoding="utf-8",
            )

            created, patched = ensure_executor_guidance(
                wg_dir,
                include_archdrift=False,
                include_uxdrift=False,
                include_therapydrift=False,
                include_fixdrift=False,
                include_yagnidrift=False,
                include_redrift=False,
            )

            self.assertFalse(created)
            self.assertIn(str(claude_toml), patched)
            data = tomllib.loads(claude_toml.read_text(encoding="utf-8"))
            executor = data["executor"]
            self.assertEqual(executor["command"], ".workgraph/executors/claude-run.sh")
            self.assertEqual(executor["args"], [])


if __name__ == "__main__":
    unittest.main()
