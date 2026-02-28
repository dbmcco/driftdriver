# ABOUTME: Tests for the Claude Code adapter - hooks.json template installation
# ABOUTME: Covers template existence, valid JSON, event-handler mapping, and idempotency.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.install import install_claude_code_hooks


TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "driftdriver"
    / "templates"
    / "adapters"
    / "claude-code"
    / "hooks.json"
)

EXPECTED_HANDLERS = {
    "SessionStart": "session-start.sh --cli claude-code",
    "PreCompact": "pre-compact.sh --cli claude-code",
    "PostToolUse": "progress-check.sh --cli claude-code",
    "Stop": "agent-stop.sh --cli claude-code",
}


class ClaudeCodeAdapterTests(unittest.TestCase):
    def test_hooks_json_template_exists(self) -> None:
        self.assertTrue(
            TEMPLATE_PATH.exists(),
            f"Template not found at {TEMPLATE_PATH}",
        )

    def test_hooks_json_template_is_valid_json(self) -> None:
        content = TEMPLATE_PATH.read_text(encoding="utf-8")
        parsed = json.loads(content)
        self.assertIn("hooks", parsed)

    def test_each_lifecycle_event_maps_to_correct_handler(self) -> None:
        parsed = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
        hooks = parsed["hooks"]
        for event, handler_suffix in EXPECTED_HANDLERS.items():
            self.assertIn(event, hooks, f"Missing lifecycle event: {event}")
            entries = hooks[event]
            self.assertTrue(len(entries) >= 1, f"No entries for {event}")
            command = entries[0]["command"]
            self.assertIn(
                handler_suffix,
                command,
                f"{event} command should reference {handler_suffix}",
            )

    def test_install_writes_to_correct_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            hooks_path = project_dir / ".claude" / "hooks.json"
            self.assertFalse(hooks_path.exists())

            wrote = install_claude_code_hooks(project_dir)

            self.assertTrue(hooks_path.exists())
            self.assertTrue(wrote)

    def test_install_creates_dot_claude_dir_if_needed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            claude_dir = project_dir / ".claude"
            self.assertFalse(claude_dir.exists())

            install_claude_code_hooks(project_dir)

            self.assertTrue(claude_dir.exists())
            self.assertTrue(claude_dir.is_dir())

    def test_installed_hooks_json_is_valid_json_matching_template(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            install_claude_code_hooks(project_dir)

            installed = json.loads(
                (project_dir / ".claude" / "hooks.json").read_text(encoding="utf-8")
            )
            template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))
            self.assertEqual(installed, template)

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)

            wrote1 = install_claude_code_hooks(project_dir)
            self.assertTrue(wrote1)

            wrote2 = install_claude_code_hooks(project_dir)
            self.assertFalse(wrote2)

            hooks_path = project_dir / ".claude" / "hooks.json"
            content = hooks_path.read_text(encoding="utf-8")
            parsed = json.loads(content)
            self.assertIn("hooks", parsed)


if __name__ == "__main__":
    unittest.main()
