# ABOUTME: Tests for the WorkGraph executor bridge to claude-session-driver
# ABOUTME: Validates template files, install function, and idempotency

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

import tomllib

from driftdriver.install import install_session_driver_executor

_TEMPLATES = Path(__file__).parent.parent / "driftdriver" / "templates" / "executors"


class SessionDriverTemplateTests(unittest.TestCase):
    def test_toml_template_exists(self) -> None:
        toml_path = _TEMPLATES / "session-driver.toml"
        self.assertTrue(toml_path.exists(), f"Missing template: {toml_path}")

    def test_shell_script_template_exists(self) -> None:
        sh_path = _TEMPLATES / "session-driver-run.sh"
        self.assertTrue(sh_path.exists(), f"Missing template: {sh_path}")

    def test_toml_has_required_executor_fields(self) -> None:
        toml_path = _TEMPLATES / "session-driver.toml"
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))

        executor = data.get("executor", {})
        self.assertEqual(executor.get("type"), "claude")
        self.assertEqual(executor.get("command"), ".workgraph/executors/session-driver-run.sh")
        self.assertIsInstance(executor.get("timeout"), int)
        self.assertGreater(executor["timeout"], 0)

    def test_toml_has_task_id_env_var(self) -> None:
        toml_path = _TEMPLATES / "session-driver.toml"
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))

        env = data.get("executor", {}).get("env", {})
        self.assertIn("WG_TASK_ID", env)

    def test_toml_prompt_template_contains_tdd_protocol(self) -> None:
        toml_path = _TEMPLATES / "session-driver.toml"
        text = toml_path.read_text(encoding="utf-8")
        self.assertIn("TDD", text)
        self.assertIn("wg done", text)
        self.assertIn("drifts check", text)

    def test_shell_script_has_required_env_vars(self) -> None:
        sh_path = _TEMPLATES / "session-driver-run.sh"
        text = sh_path.read_text(encoding="utf-8")
        self.assertIn("WG_TASK_ID", text)
        self.assertIn("WG_PROJECT_DIR", text)
        self.assertIn("WG_PROMPT", text)

    def test_shell_script_references_session_driver(self) -> None:
        sh_path = _TEMPLATES / "session-driver-run.sh"
        text = sh_path.read_text(encoding="utf-8")
        self.assertIn("launch-worker.sh", text)
        self.assertIn("converse.sh", text)
        self.assertIn("stop-worker.sh", text)


class SessionDriverInstallTests(unittest.TestCase):
    def test_install_copies_toml_and_script(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            wrote_toml, wrote_script = install_session_driver_executor(wg_dir)
            self.assertTrue(wrote_toml)
            self.assertTrue(wrote_script)

            self.assertTrue((wg_dir / "executors" / "session-driver.toml").exists())
            self.assertTrue((wg_dir / "executors" / "session-driver-run.sh").exists())

    def test_install_makes_script_executable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_session_driver_executor(wg_dir)

            sh_path = wg_dir / "executors" / "session-driver-run.sh"
            mode = sh_path.stat().st_mode
            self.assertTrue(mode & stat.S_IXUSR, "script not user-executable")
            self.assertTrue(mode & stat.S_IXGRP, "script not group-executable")
            self.assertTrue(mode & stat.S_IXOTH, "script not world-executable")

    def test_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            install_session_driver_executor(wg_dir)
            wrote_toml_2, wrote_script_2 = install_session_driver_executor(wg_dir)
            self.assertFalse(wrote_toml_2)
            self.assertFalse(wrote_script_2)

    def test_install_creates_executors_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            # executors dir should not exist beforehand
            self.assertFalse((wg_dir / "executors").exists())
            install_session_driver_executor(wg_dir)
            self.assertTrue((wg_dir / "executors").is_dir())


if __name__ == "__main__":
    unittest.main()
