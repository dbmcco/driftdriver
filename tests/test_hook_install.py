# ABOUTME: Tests for install_hook_scripts — copies .sh files from templates/hooks to .workgraph/hooks/
# ABOUTME: Verifies copy, executability, idempotency, and directory creation

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from driftdriver.install import install_hook_scripts

_TEMPLATES = Path(__file__).parent.parent / "driftdriver" / "templates" / "hooks"


class InstallHookScriptsTests(unittest.TestCase):
    def test_copies_all_hook_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            any_written, count = install_hook_scripts(wg_dir)

            expected = list(_TEMPLATES.glob("*.sh"))
            self.assertTrue(any_written)
            self.assertEqual(count, len(expected))
            for src in expected:
                dst = wg_dir / "hooks" / src.name
                self.assertTrue(dst.exists(), f"Missing: {src.name}")
                self.assertEqual(dst.read_bytes(), src.read_bytes())

    def test_makes_executable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_hook_scripts(wg_dir)

            for src in _TEMPLATES.glob("*.sh"):
                dst = wg_dir / "hooks" / src.name
                mode = dst.stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR, f"{src.name} not user-executable")

    def test_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_hook_scripts(wg_dir)
            any_written2, count2 = install_hook_scripts(wg_dir)

            self.assertFalse(any_written2)
            self.assertEqual(count2, 0)

    def test_creates_hooks_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            self.assertFalse((wg_dir / "hooks").exists())
            install_hook_scripts(wg_dir)
            self.assertTrue((wg_dir / "hooks").is_dir())

    def test_pre_dispatch_hook_exists(self) -> None:
        """The pre-dispatch.sh hook template must exist."""
        self.assertTrue(
            (_TEMPLATES / "pre-dispatch.sh").exists(),
            "pre-dispatch.sh template missing",
        )


class PreDispatchHookContentTests(unittest.TestCase):
    """Verify pre-dispatch.sh contains expected structure."""

    def setUp(self) -> None:
        self.hook = _TEMPLATES / "pre-dispatch.sh"
        self.content = self.hook.read_text(encoding="utf-8")

    def test_references_agency_assign(self) -> None:
        self.assertIn("agency-assign-workgraph", self.content)

    def test_references_wrap_script(self) -> None:
        self.assertIn("agency-speedrift-wrap.py", self.content)

    def test_respects_skip_flag(self) -> None:
        self.assertIn("WG_SKIP_AGENCY", self.content)

    def test_emits_events(self) -> None:
        self.assertIn("agency.enrichment.applied", self.content)
        self.assertIn("agency.enrichment.skipped", self.content)


if __name__ == "__main__":
    unittest.main()
