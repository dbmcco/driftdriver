# ABOUTME: Tests for unified install --all-clis and --with-lessons-mcp flags.
# ABOUTME: Covers all-adapter orchestration and lessons-mcp .mcp.json configuration.
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.install import (
    install_amplifier_adapter,
    install_claude_code_hooks,
    install_codex_adapter,
    install_lessons_mcp_config,
    install_opencode_hooks,
    install_session_driver_executor,
)


class UnifiedInstallAdapterTests(unittest.TestCase):
    def test_all_clis_calls_all_adapters(self) -> None:
        """All 5 adapter install functions each create their expected output files."""
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            wg_dir = project_dir / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_claude_code_hooks(project_dir)
            install_codex_adapter(project_dir)
            install_opencode_hooks(project_dir)
            install_amplifier_adapter(project_dir)
            install_session_driver_executor(wg_dir)

            self.assertTrue((project_dir / ".claude" / "hooks.json").exists())
            self.assertTrue((project_dir / "AGENTS.md").exists())
            self.assertTrue((project_dir / ".opencode" / "hooks.json").exists())
            self.assertTrue(
                (project_dir / ".amplifier" / "hooks" / "driftdriver" / "session-hooks.sh").exists()
            )
            self.assertTrue((wg_dir / "executors" / "session-driver.toml").exists())

    def test_with_lessons_mcp_creates_config(self) -> None:
        """install_lessons_mcp_config creates .mcp.json with a lessons-mcp entry."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            wrote = install_lessons_mcp_config(wg_dir)

            self.assertTrue(wrote)
            mcp_json = Path(td) / ".mcp.json"
            self.assertTrue(mcp_json.exists())
            data = json.loads(mcp_json.read_text(encoding="utf-8"))
            self.assertIn("lessons-mcp", data.get("mcpServers", {}))

    def test_with_lessons_mcp_idempotent(self) -> None:
        """Running install_lessons_mcp_config twice does not duplicate the entry."""
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            wrote1 = install_lessons_mcp_config(wg_dir)
            self.assertTrue(wrote1)

            wrote2 = install_lessons_mcp_config(wg_dir)
            self.assertFalse(wrote2)

            mcp_json = Path(td) / ".mcp.json"
            data = json.loads(mcp_json.read_text(encoding="utf-8"))
            servers = data.get("mcpServers", {})
            self.assertEqual(list(servers.keys()).count("lessons-mcp"), 1)

    def test_all_clis_flag_parsed(self) -> None:
        """--all-clis and --with-lessons-mcp flags are recognized by the CLI parser."""
        from driftdriver.cli import _build_parser

        parser = _build_parser()
        args = parser.parse_args(["install", "--all-clis", "--no-ensure-contracts"])
        self.assertTrue(args.all_clis)

        args2 = parser.parse_args(["install", "--with-lessons-mcp", "--no-ensure-contracts"])
        self.assertTrue(args2.with_lessons_mcp)


if __name__ == "__main__":
    unittest.main()
