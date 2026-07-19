# ABOUTME: Functional regression tests for the generated dispatch-loop.sh template.
# ABOUTME: Verifies the loop is lease-aware (respects control.json dispatch_enabled),
# ABOUTME: preserves stale-daemon cleanup, and that the install path ships the gate.

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

TEMPLATE = (
    Path(__file__).parent.parent / "driftdriver" / "templates" / "dispatch-loop.sh"
)


class _LoopHarness:
    """Build a temp repo + fake ``wg`` and run the dispatch-loop template."""

    def __init__(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())
        self.repo = self.tmpdir / "repo-under-test"
        self.wg_dir = self.repo / ".workgraph"
        self.runtime_dir = self.wg_dir / "service" / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

        self.bin_dir = self.tmpdir / "bin"
        self.bin_dir.mkdir()
        self._write_fake_wg()

        self.events_file = self.runtime_dir / "events.jsonl"
        self.spawn_log = self.tmpdir / "wg-spawn.log"
        self.stop_log = self.tmpdir / "wg-stop.log"
        self.ready_counter = self.tmpdir / "ready-counter"
        self.ready_counter.write_text("0")

    # -- fake wg -----------------------------------------------------------
    def _write_fake_wg(self) -> None:
        """A minimal ``wg`` stub: service stop, ready, agents, list, spawn, notify.

        ``ready`` yields exactly one task on its *first* call, then nothing, so
        the loop can terminate via the all-complete exit path.
        """
        script = self.bin_dir / "wg"
        script.write_text(
            f"""#!/usr/bin/env bash
# Fake wg for dispatch-loop regression tests.
case "$1" in
  service)
    echo "stop" >> "{self.tmpdir}/wg-stop.log"
    exit 0
    ;;
  ready)
    count=$(cat "{self.ready_counter}" 2>/dev/null || echo 0)
    if [ "$count" -eq 0 ]; then
      echo "  ready-task-1"
      echo 1 > "{self.ready_counter}"
    fi
    exit 0
    ;;
  agents)
    exit 0
    ;;
  list)
    exit 0
    ;;
  spawn)
    echo "$@" >> "{self.spawn_log}"
    exit 0
    ;;
  notify)
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
"""
        )
        script.chmod(script.stat().st_mode | stat.S_IXUSR)

    # -- control state -----------------------------------------------------
    def write_control(self, payload: dict | None) -> None:
        if payload is None:
            (self.runtime_dir / "control.json").unlink(missing_ok=True)
            return
        (self.runtime_dir / "control.json").write_text(json.dumps(payload))

    # -- run ---------------------------------------------------------------
    def run(self, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin_dir}:{os.environ.get('PATH', '')}",
            "HOME": os.environ.get("HOME", str(self.tmpdir)),
            # Speed the loop up and pin tiny bounds.
            "WG_POLL_INTERVAL": "0",
            "WG_MAX_AGENTS": "1",
            "WG_EXECUTOR": "stubexec",
            # No real notifier; fake wg still swallows `wg notify`.
            "WG_NOTIFY_SCRIPT": str(self.tmpdir / "no-notify"),
        }
        if env_extra:
            env.update(env_extra)
        return subprocess.run(
            ["bash", str(TEMPLATE)],
            cwd=self.repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

    # -- assertions helpers ------------------------------------------------
    def events(self) -> list[dict]:
        if not self.events_file.exists():
            return []
        rows = []
        for line in self.events_file.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return rows

    def event_kinds(self) -> list[str]:
        return [e.get("kind", "") for e in self.events()]

    def spawn_calls(self) -> list[str]:
        if not self.spawn_log.exists():
            return []
        return [
            ln.strip()
            for ln in self.spawn_log.read_text().splitlines()
            if ln.strip()
        ]

    def cleanup(self) -> None:
        import shutil

        shutil.rmtree(self.tmpdir, ignore_errors=True)


class DispatchLoopLeaseGateTests(unittest.TestCase):
    """The generated loop must honor the lease-gated dispatch authority."""

    def setUp(self) -> None:
        self.h = _LoopHarness()

    def tearDown(self) -> None:
        self.h.cleanup()

    def test_denied_loop_does_not_spawn_and_emits_denied_event(self) -> None:
        """dispatch_enabled=false => no wg spawn, dispatch.denied event, clean exit."""
        self.h.write_control(
            {"dispatch_enabled": False, "reason": "lease is not active"}
        )
        result = self.h.run({"WG_DISPATCH_DENIAL_LIMIT": "1"})

        self.assertEqual(result.returncode, 0, f"loop stderr:\n{result.stderr}")
        self.assertEqual(self.h.spawn_calls(), [], "loop spawned while lease denied")
        self.assertIn("dispatch.denied", self.h.event_kinds())
        denied = [e for e in self.h.events() if e.get("kind") == "dispatch.denied"]
        self.assertTrue(denied, "missing dispatch.denied event")
        self.assertEqual(denied[-1].get("reason"), "lease is not active")
        self.assertIn("loop.exited", self.h.event_kinds())

    def test_allowed_loop_spawns_and_never_denies(self) -> None:
        """dispatch_enabled=true => gate passes, a task spawns, no denial."""
        self.h.write_control({"dispatch_enabled": True})
        result = self.h.run()

        self.assertEqual(result.returncode, 0, f"loop stderr:\n{result.stderr}")
        self.assertNotIn("dispatch.denied", self.h.event_kinds())
        self.assertTrue(self.h.spawn_calls(), "loop did not spawn when lease allowed")
        self.assertTrue(
            any("spawn" in c for c in self.h.spawn_calls()),
            f"spawn args missing executor: {self.h.spawn_calls()}",
        )
        self.assertIn("agent.spawned", self.h.event_kinds())

    def test_no_control_file_defaults_to_allowed(self) -> None:
        """Backward-compat: without control.json the loop still dispatches."""
        self.h.write_control(None)
        result = self.h.run()

        self.assertEqual(result.returncode, 0, f"loop stderr:\n{result.stderr}")
        self.assertNotIn("dispatch.denied", self.h.event_kinds())
        self.assertTrue(self.h.spawn_calls(), "loop should spawn absent lease system")

    def test_require_lease_denies_without_control_file(self) -> None:
        """WG_REQUIRE_LEASE=1 => missing control.json is treated as denied."""
        self.h.write_control(None)
        result = self.h.run(
            {"WG_REQUIRE_LEASE": "1", "WG_DISPATCH_DENIAL_LIMIT": "1"}
        )

        self.assertEqual(result.returncode, 0, f"loop stderr:\n{result.stderr}")
        self.assertEqual(self.h.spawn_calls(), [], "loop spawned without a lease")
        self.assertIn("dispatch.denied", self.h.event_kinds())
        denied = [e for e in self.h.events() if e.get("kind") == "dispatch.denied"]
        self.assertEqual(denied[-1].get("reason"), "no_control_state")

    def test_stale_daemon_cleanup_runs_on_startup(self) -> None:
        """The lease gate must not remove the startup stale-daemon cleanup stop."""
        self.h.write_control({"dispatch_enabled": False})
        self.h.run({"WG_DISPATCH_DENIAL_LIMIT": "1"})

        self.assertTrue(self.h.stop_log.exists(), "wg service stop never invoked")


class DispatchLoopTemplateInstallTests(unittest.TestCase):
    """Install path ships the lease gate (regression guard)."""

    def test_template_carries_lease_gate_markers(self) -> None:
        text = TEMPLATE.read_text()
        # Core gate primitives must survive into the installed copy.
        self.assertIn("dispatch_allowed", text)
        self.assertIn("dispatch_enabled", text)
        self.assertIn("control.json", text)
        self.assertIn("dispatch.denied", text)

    def test_hub_enroll_install_copies_executable_gate(self) -> None:
        """FactoryBrain._handle_enroll installs an executable, gate-bearing loop."""
        from driftdriver.factory_brain.hub_integration import FactoryBrain

        tmpdir = Path(tempfile.mkdtemp())
        hub_dir = tmpdir / "hub-data"
        hub_dir.mkdir()
        repo = tmpdir / "enroll-repo"
        (repo / ".workgraph").mkdir(parents=True)

        brain = FactoryBrain(hub_data_dir=hub_dir, workspace_roots=[tmpdir])
        brain._handle_enroll(str(repo))

        installed = repo / ".workgraph" / "dispatch-loop.sh"
        self.assertTrue(installed.exists(), "dispatch-loop.sh not installed on enroll")
        self.assertTrue(
            installed.stat().st_mode & stat.S_IXUSR, "installed loop not executable"
        )
        copied = installed.read_text()
        self.assertIn("dispatch_allowed", copied)
        self.assertIn("dispatch_enabled", copied)


if __name__ == "__main__":
    unittest.main()
