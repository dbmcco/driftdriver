# ABOUTME: Functional tests for the pre-dispatch hook script.
# ABOUTME: Exercises passthrough (no Agency) and skip flag paths via subprocess.

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


class PreDispatchHookFunctionalTests(unittest.TestCase):
    """Run the actual pre-dispatch.sh hook and verify behavior."""

    def setUp(self) -> None:
        self.template = (
            Path(__file__).parent.parent
            / "driftdriver"
            / "templates"
            / "hooks"
            / "pre-dispatch.sh"
        )
        # Create a temp .workgraph layout so the hook can find executors
        self.tmpdir = tempfile.mkdtemp()
        self.wg_dir = Path(self.tmpdir) / ".workgraph"
        self.hooks_dir = self.wg_dir / "hooks"
        self.executors_dir = self.wg_dir / "executors"
        self.hooks_dir.mkdir(parents=True)
        self.executors_dir.mkdir(parents=True)

        # Copy hook
        hook_dst = self.hooks_dir / "pre-dispatch.sh"
        hook_dst.write_bytes(self.template.read_bytes())
        hook_dst.chmod(hook_dst.stat().st_mode | stat.S_IXUSR)
        self.hook = hook_dst

        # Copy agency-assign-workgraph (real script, will fail health check)
        assign_src = (
            Path(__file__).parent.parent
            / "driftdriver"
            / "templates"
            / "executors"
            / "agency-assign-workgraph"
        )
        assign_dst = self.executors_dir / "agency-assign-workgraph"
        assign_dst.write_bytes(assign_src.read_bytes())
        assign_dst.chmod(assign_dst.stat().st_mode | stat.S_IXUSR)

        # Copy wrap script
        wrap_src = (
            Path(__file__).parent.parent
            / "driftdriver"
            / "templates"
            / "executors"
            / "agency-speedrift-wrap.py"
        )
        (self.executors_dir / "agency-speedrift-wrap.py").write_bytes(
            wrap_src.read_bytes()
        )

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir)

    def _run_hook(self, prompt: str, env_extra: dict[str, str] | None = None) -> str:
        env = os.environ.copy()
        env["AGENCY_PORT"] = "19999"  # unreachable
        env["AGENCY_HEALTH_TIMEOUT"] = "0.2"
        if env_extra:
            env.update(env_extra)

        result = subprocess.run(
            [str(self.hook)],
            input=prompt,
            capture_output=True,
            text=True,
            env=env,
            cwd=self.tmpdir,
        )
        self.assertEqual(result.returncode, 0, f"Hook failed: {result.stderr}")
        return result.stdout

    def test_passthrough_when_agency_unreachable(self) -> None:
        """When Agency is down, hook passes prompt through unchanged."""
        prompt = "Original speedrift prompt content"
        output = self._run_hook(prompt, {"WG_TASK_ID": "test-task-1"})
        self.assertEqual(output, prompt)

    def test_passthrough_when_no_task_id(self) -> None:
        """Without WG_TASK_ID, hook passes prompt through unchanged."""
        prompt = "Original prompt"
        output = self._run_hook(prompt)
        self.assertEqual(output, prompt)

    def test_passthrough_when_skip_agency(self) -> None:
        """WG_SKIP_AGENCY=1 bypasses Agency entirely."""
        prompt = "Original prompt"
        output = self._run_hook(
            prompt, {"WG_TASK_ID": "test-task-1", "WG_SKIP_AGENCY": "1"}
        )
        self.assertEqual(output, prompt)

    def test_emits_skip_event_when_agency_down(self) -> None:
        """When Agency is unreachable, an event should be emitted."""
        events_dir = self.wg_dir / "service" / "runtime"
        events_dir.mkdir(parents=True, exist_ok=True)
        events_file = events_dir / "events.jsonl"

        self._run_hook("test prompt", {"WG_TASK_ID": "test-task-2"})

        self.assertTrue(events_file.exists())
        content = events_file.read_text(encoding="utf-8")
        self.assertIn("agency.enrichment.skipped", content)
        self.assertIn("agency_unavailable", content)


if __name__ == "__main__":
    unittest.main()
