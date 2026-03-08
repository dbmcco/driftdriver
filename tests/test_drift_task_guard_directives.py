# ABOUTME: Tests that drift_task_guard emits directives through ExecutorShim.
# ABOUTME: Verifies the judgment/execution boundary is explicit via directive emission.

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, MagicMock

from driftdriver.actor import Actor
from driftdriver.directives import DirectiveLog


class TestGuardEmitsDirective(unittest.TestCase):
    @patch("driftdriver.executor_shim.subprocess.run")
    @patch("driftdriver.drift_task_guard._run_wg")
    def test_guarded_add_emits_create_task_directive(self, mock_wg: MagicMock, mock_subprocess: MagicMock) -> None:
        # Mock wg show (dedup check) to return non-zero (task doesn't exist)
        mock_wg.return_value = (1, "", "")
        # Mock shim's subprocess to succeed
        mock_subprocess.return_value = MagicMock(returncode=0, stdout="", stderr="")

        with TemporaryDirectory() as tmp:
            wg_dir = Path(tmp) / ".workgraph"
            wg_dir.mkdir()

            from driftdriver.drift_task_guard import guarded_add_drift_task

            result = guarded_add_drift_task(
                wg_dir=wg_dir,
                task_id="drift-harden-t1",
                title="harden: t1",
                description="Move guardrails to follow-up",
                lane_tag="coredrift",
                actor=Actor(id="coredrift", actor_class="lane", name="coredrift", repo="test"),
            )

            self.assertEqual(result, "created")

            # Verify directive was recorded in the log
            directive_dir = wg_dir / "service" / "directives"
            log = DirectiveLog(directive_dir)
            completed = log.read_completed()
            self.assertEqual(len(completed), 1)


if __name__ == "__main__":
    unittest.main()
