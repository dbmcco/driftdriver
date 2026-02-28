# ABOUTME: Tests for the OpenCode and Amplifier adapters - template existence and install functions.
# ABOUTME: Covers template existence, valid JSON/shell content, correct install paths, and idempotency.

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.install import install_opencode_hooks, install_amplifier_adapter


OPENCODE_TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "driftdriver"
    / "templates"
    / "adapters"
    / "opencode"
    / "hooks.json"
)

AMPLIFIER_TEMPLATE_PATH = (
    Path(__file__).parent.parent
    / "driftdriver"
    / "templates"
    / "adapters"
    / "amplifier"
    / "session-hooks.sh"
)

EXPECTED_OPENCODE_HANDLERS = {
    "SessionStart": "session-start.sh --cli opencode",
    "PreCompact": "pre-compact.sh --cli opencode",
    "PostToolUse": "progress-check.sh --cli opencode",
    "Stop": "agent-stop.sh --cli opencode",
}


class OpenCodeAdapterTests(unittest.TestCase):
    def test_hooks_json_template_exists(self) -> None:
        self.assertTrue(
            OPENCODE_TEMPLATE_PATH.exists(),
            f"Template not found at {OPENCODE_TEMPLATE_PATH}",
        )

    def test_hooks_json_template_is_valid_json(self) -> None:
        content = OPENCODE_TEMPLATE_PATH.read_text(encoding="utf-8")
        parsed = json.loads(content)
        self.assertIn("hooks", parsed)

    def test_each_lifecycle_event_maps_to_correct_handler(self) -> None:
        parsed = json.loads(OPENCODE_TEMPLATE_PATH.read_text(encoding="utf-8"))
        hooks = parsed["hooks"]
        for event, handler_suffix in EXPECTED_OPENCODE_HANDLERS.items():
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
            hooks_path = project_dir / ".opencode" / "hooks.json"
            self.assertFalse(hooks_path.exists())

            wrote = install_opencode_hooks(project_dir)

            self.assertTrue(hooks_path.exists())
            self.assertTrue(wrote)

    def test_install_creates_dot_opencode_dir_if_needed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            opencode_dir = project_dir / ".opencode"
            self.assertFalse(opencode_dir.exists())

            install_opencode_hooks(project_dir)

            self.assertTrue(opencode_dir.exists())
            self.assertTrue(opencode_dir.is_dir())

    def test_installed_hooks_json_matches_template(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            install_opencode_hooks(project_dir)

            installed = json.loads(
                (project_dir / ".opencode" / "hooks.json").read_text(encoding="utf-8")
            )
            template = json.loads(OPENCODE_TEMPLATE_PATH.read_text(encoding="utf-8"))
            self.assertEqual(installed, template)

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)

            wrote1 = install_opencode_hooks(project_dir)
            self.assertTrue(wrote1)

            wrote2 = install_opencode_hooks(project_dir)
            self.assertFalse(wrote2)

            hooks_path = project_dir / ".opencode" / "hooks.json"
            parsed = json.loads(hooks_path.read_text(encoding="utf-8"))
            self.assertIn("hooks", parsed)


class AmplifierAdapterTests(unittest.TestCase):
    def test_session_hooks_template_exists(self) -> None:
        self.assertTrue(
            AMPLIFIER_TEMPLATE_PATH.exists(),
            f"Template not found at {AMPLIFIER_TEMPLATE_PATH}",
        )

    def test_session_hooks_template_references_shared_handlers(self) -> None:
        content = AMPLIFIER_TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn("session-start.sh", content)
        self.assertIn("--cli amplifier", content)

    def test_install_writes_to_correct_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            dest_path = project_dir / ".amplifier" / "hooks" / "driftdriver" / "session-hooks.sh"
            self.assertFalse(dest_path.exists())

            wrote = install_amplifier_adapter(project_dir)

            self.assertTrue(dest_path.exists())
            self.assertTrue(wrote)

    def test_install_creates_hook_dir_if_needed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            hook_dir = project_dir / ".amplifier" / "hooks" / "driftdriver"
            self.assertFalse(hook_dir.exists())

            install_amplifier_adapter(project_dir)

            self.assertTrue(hook_dir.exists())
            self.assertTrue(hook_dir.is_dir())

    def test_installed_script_matches_template(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            install_amplifier_adapter(project_dir)

            dest_path = project_dir / ".amplifier" / "hooks" / "driftdriver" / "session-hooks.sh"
            installed = dest_path.read_text(encoding="utf-8")
            template = AMPLIFIER_TEMPLATE_PATH.read_text(encoding="utf-8")
            self.assertEqual(installed, template)

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)

            wrote1 = install_amplifier_adapter(project_dir)
            self.assertTrue(wrote1)

            wrote2 = install_amplifier_adapter(project_dir)
            self.assertFalse(wrote2)

            dest_path = project_dir / ".amplifier" / "hooks" / "driftdriver" / "session-hooks.sh"
            self.assertIn("session-start.sh", dest_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
