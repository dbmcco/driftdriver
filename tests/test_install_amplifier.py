from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from driftdriver.install import ensure_amplifier_autostart_hook, ensure_amplifier_executor


class AmplifierInstallTests(unittest.TestCase):
    def test_ensure_amplifier_executor_writes_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wg_dir = Path(td) / ".workgraph"
            wg_dir.mkdir(parents=True, exist_ok=True)

            wrote_toml, wrote_runner = ensure_amplifier_executor(wg_dir, bundle_name="speedrift")
            self.assertTrue(wrote_toml)
            self.assertTrue(wrote_runner)

            runner = wg_dir / "executors" / "amplifier-run.sh"
            toml = wg_dir / "executors" / "amplifier.toml"
            self.assertTrue(runner.exists())
            self.assertTrue(toml.exists())
            self.assertIn("--bundle \"$BUNDLE\"", runner.read_text(encoding="utf-8"))
            self.assertIn("Speedrift Execution Rules", toml.read_text(encoding="utf-8"))

            wrote_toml_2, wrote_runner_2 = ensure_amplifier_executor(wg_dir, bundle_name="speedrift")
            self.assertFalse(wrote_toml_2)
            self.assertFalse(wrote_runner_2)

    def test_ensure_amplifier_autostart_hook_writes_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            project_dir = Path(td)
            wrote_script, wrote_json = ensure_amplifier_autostart_hook(project_dir)
            self.assertTrue(wrote_script)
            self.assertTrue(wrote_json)

            hook_dir = project_dir / ".amplifier" / "hooks" / "speedrift-autostart"
            script_path = hook_dir / "session-start.sh"
            hooks_json_path = hook_dir / "hooks.json"
            self.assertTrue(script_path.exists())
            self.assertTrue(hooks_json_path.exists())

            script_text = script_path.read_text(encoding="utf-8")
            self.assertIn("driftdriver --dir", script_text)
            self.assertIn("ensure-contracts --apply", script_text)
            self.assertIn("STAMP_FILE", script_text)

            parsed = json.loads(hooks_json_path.read_text(encoding="utf-8"))
            user_prompt_submit = parsed["hooks"]["UserPromptSubmit"][0]
            session_start = parsed["hooks"]["SessionStart"][0]
            self.assertEqual(user_prompt_submit["matcher"], ".*")
            self.assertEqual(session_start["matcher"], ".*")

            wrote_script_2, wrote_json_2 = ensure_amplifier_autostart_hook(project_dir)
            self.assertFalse(wrote_script_2)
            self.assertFalse(wrote_json_2)


if __name__ == "__main__":
    unittest.main()
