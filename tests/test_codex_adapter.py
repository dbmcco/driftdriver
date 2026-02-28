# ABOUTME: Tests for the Codex adapter - AGENTS.md.partial template injection
# ABOUTME: Covers template existence, create, append, and idempotency behavior.

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from driftdriver.install import install_codex_adapter


TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "driftdriver"
    / "templates"
    / "adapters"
    / "codex"
    / "AGENTS.md.partial"
)

MARKER = "## Driftdriver Integration Protocol"


class CodexAdapterTests(unittest.TestCase):
    def test_agents_md_partial_template_exists(self) -> None:
        self.assertTrue(
            TEMPLATE_PATH.exists(),
            f"Template not found at {TEMPLATE_PATH}",
        )
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn(MARKER, content)

    def test_install_creates_agents_md_when_none_exists(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            agents_md = project_dir / "AGENTS.md"
            self.assertFalse(agents_md.exists())

            result = install_codex_adapter(project_dir)

            self.assertTrue(agents_md.exists())
            self.assertTrue(result.wrote_agents_md)
            content = agents_md.read_text(encoding="utf-8")
            self.assertIn(MARKER, content)
            self.assertIn("session-start.sh --cli codex", content)
            self.assertIn("task-claimed.sh --cli codex", content)

    def test_install_appends_to_existing_agents_md(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            agents_md = project_dir / "AGENTS.md"
            existing_content = "# My Project Agents\n\nSome existing content.\n"
            agents_md.write_text(existing_content, encoding="utf-8")

            result = install_codex_adapter(project_dir)

            self.assertTrue(result.wrote_agents_md)
            content = agents_md.read_text(encoding="utf-8")
            self.assertIn("Some existing content.", content)
            self.assertIn(MARKER, content)

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)

            result1 = install_codex_adapter(project_dir)
            self.assertTrue(result1.wrote_agents_md)

            result2 = install_codex_adapter(project_dir)
            self.assertFalse(result2.wrote_agents_md)

            content = (project_dir / "AGENTS.md").read_text(encoding="utf-8")
            self.assertEqual(content.count(MARKER), 1)


if __name__ == "__main__":
    unittest.main()
