# ABOUTME: Tests for install_handler_scripts - copies .sh files from templates/handlers to .workgraph/handlers/
# ABOUTME: Verifies copy, executability, idempotency, and directory creation

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from driftdriver.install import install_handler_scripts

_TEMPLATES = Path(__file__).parent.parent / "driftdriver" / "templates" / "handlers"


class InstallHandlerScriptsTests(unittest.TestCase):
    def test_install_handler_scripts_copies_all_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            any_written, count = install_handler_scripts(wg_dir)

            expected = list(_TEMPLATES.glob("*.sh"))
            self.assertTrue(any_written)
            self.assertEqual(count, len(expected))
            for src in expected:
                dst = wg_dir / "handlers" / src.name
                self.assertTrue(dst.exists(), f"Missing: {src.name}")
                self.assertEqual(dst.read_bytes(), src.read_bytes())

    def test_install_handler_scripts_makes_executable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_handler_scripts(wg_dir)

            for src in _TEMPLATES.glob("*.sh"):
                dst = wg_dir / "handlers" / src.name
                mode = dst.stat().st_mode
                self.assertTrue(mode & stat.S_IXUSR, f"{src.name} not user-executable")
                self.assertTrue(mode & stat.S_IXGRP, f"{src.name} not group-executable")
                self.assertTrue(mode & stat.S_IXOTH, f"{src.name} not world-executable")

    def test_install_handler_scripts_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_handler_scripts(wg_dir)
            any_written2, count2 = install_handler_scripts(wg_dir)

            self.assertFalse(any_written2)
            self.assertEqual(count2, 0)

    def test_install_handler_scripts_creates_directory(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            self.assertFalse((wg_dir / "handlers").exists())
            install_handler_scripts(wg_dir)
            self.assertTrue((wg_dir / "handlers").is_dir())


class InstallResultHandlerFieldTests(unittest.TestCase):
    def test_install_result_tracks_handlers(self) -> None:
        from driftdriver.install import InstallResult
        import dataclasses

        fields = {f.name for f in dataclasses.fields(InstallResult)}
        self.assertIn(
            "wrote_handlers",
            fields,
            "InstallResult must have a 'wrote_handlers' field",
        )


if __name__ == "__main__":
    unittest.main()
