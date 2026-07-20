# ABOUTME: Regression tests for the lease-aware dispatch-loop.sh template and its install paths.
# ABOUTME: Ensures wg spawn is authority-gated and stale-daemon cleanup is preserved.

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "driftdriver" / "templates" / "dispatch-loop.sh"


class DispatchLoopTemplateContentTests(unittest.TestCase):
    """Static guarantees about the template source."""

    def setUp(self) -> None:
        self.skip_if_template_missing()
        self.content = TEMPLATE.read_text(encoding="utf-8")

    def skip_if_template_missing(self) -> None:
        if not TEMPLATE.exists():
            self.skipTest(f"template not found: {TEMPLATE}")

    def test_template_defines_dispatch_authority_gate(self) -> None:
        """The loop must define a has_dispatch_authority gate that inspects the lease."""
        self.assertIn("has_dispatch_authority", self.content)
        # Must consult the speedriftd control plane (mode + lease_active).
        self.assertIn("speedriftd status", self.content)
        self.assertIn(".control.mode", self.content)
        self.assertIn(".control.lease_active", self.content)

    def test_template_preserves_stale_daemon_cleanup(self) -> None:
        """The graphwork/workgraph#4 stale-daemon cleanup stop must remain."""
        self.assertIn("Kill stale daemon on startup", self.content)
        # The wg service stop used to kill the stale daemon must still be present.
        self.assertGreaterEqual(self.content.count("wg service stop"), 1)

    def test_template_gates_spawn_on_authority(self) -> None:
        """wg spawn must only run after the authority gate has been consulted."""
        gate_idx = self.content.find("has_dispatch_authority")
        spawn_idx = self.content.find("wg spawn")
        self.assertGreater(gate_idx, 0, "authority gate must be defined")
        self.assertGreater(spawn_idx, 0, "wg spawn must be present")
        # The gate must be defined before the spawn call so spawning is gated.
        self.assertLess(gate_idx, spawn_idx)


class DispatchLoopFunctionalTests(unittest.TestCase):
    """Run the loop with stubbed wg/driftdriver and verify lease-aware behavior."""

    def setUp(self) -> None:
        if not TEMPLATE.exists():
            self.skipTest(f"template not found: {TEMPLATE}")
        if shutil.which("jq") is None:
            self.skipTest("jq required for functional dispatch-loop tests")
        # `timeout` is used by ready_tasks(); the loop hard-depends on it.
        if shutil.which("timeout") is None and shutil.which("gtimeout") is None:
            self.skipTest("timeout required for functional dispatch-loop tests")

        self.tmpdir = tempfile.mkdtemp()
        self.repo = Path(self.tmpdir) / "repo"
        self.repo.mkdir()
        # Minimal .workgraph runtime the loop writes events/heartbeat into.
        (self.repo / ".workgraph" / "service" / "runtime").mkdir(parents=True)

        # Stub bin directory prepended to PATH.
        self.bin = Path(self.tmpdir) / "bin"
        self.bin.mkdir()
        self.spawn_log = Path(self.tmpdir) / "spawns.log"

        self._write_wg_stub()
        # driftdriver stub is written per-test by _write_driftdriver_stub().

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- stubs -----------------------------------------------------------

    def _write_wg_stub(self, ready_tasks: str = "") -> None:
        """wg stub: record spawns, emit ready tasks, noop everything else."""
        ready_block = (
            f'    printf "  {ready_tasks}\\n"; exit 0 ;;\n'
            if ready_tasks
            else "    exit 0 ;;\n"
        )
        stub = self.bin / "wg"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"SPAWN_LOG={self.spawn_log!s}\n"
            'case "$1" in\n'
            '  spawn)\n'
            f'    echo "spawn $*" >> "$SPAWN_LOG"\n'
            "    exit 0 ;;\n"
            "  ready)\n"
            + ready_block +
            "  *)\n"
            "    exit 0 ;;\n"
            "esac\n"
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def _write_driftdriver_stub(self, mode: str, lease_active: bool,
                                flip_after: int | None = None) -> None:
        """driftdriver stub emitting speedriftd status JSON.

        If flip_after is set, the first flip_after calls return the given
        authority, and every subsequent call returns observe/no-lease. This lets
        a positive test spawn once then observe the loop exit on authority loss.
        """
        counter = Path(self.tmpdir) / "dd.counter"
        initial = "true" if lease_active else "false"
        flip = "true" if flip_after is not None else "false"
        flip_n = flip_after if flip_after is not None else 0
        stub = self.bin / "driftdriver"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f"COUNTER={counter!s}\n"
            f'INITIAL_LEASE="{initial}"\n'
            f'MODE="{mode}"\n'
            f"FLIP={flip}\n"
            f"FLIP_AFTER={flip_n}\n"
            'n=0\n'
            '[ -f "$COUNTER" ] && n="$(cat "$COUNTER" 2>/dev/null || echo 0)"\n'
            'n=$((n + 1))\n'
            'echo "$n" > "$COUNTER"\n'
            'lease="$INITIAL_LEASE"\n'
            'mode="$MODE"\n'
            'if [ "$FLIP" = "true" ] && [ "$n" -gt "$FLIP_AFTER" ]; then\n'
            '  lease="false"\n'
            '  mode="observe"\n'
            'fi\n'
            'printf \'{"control":{"mode":"%s","lease_active":%s}}\\n\' "$mode" "$lease"\n'
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)

    def _run_loop(self, timeout_s: float = 15.0) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PATH"] = f"{self.bin}:{env.get('PATH', '')}"
        # Short poll so positive tests don't wait 30s between cycles.
        env["WG_POLL_INTERVAL"] = "0.1"
        # Avoid firing real macOS notifications during tests.
        env["WG_NOTIFY_SCRIPT"] = str(Path(self.tmpdir) / "no-such-notify")
        return subprocess.run(
            ["bash", str(TEMPLATE)],
            cwd=str(self.repo),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )

    def _events(self) -> str:
        f = self.repo / ".workgraph" / "service" / "runtime" / "events.jsonl"
        return f.read_text(encoding="utf-8") if f.exists() else ""

    # --- behavior --------------------------------------------------------

    def test_loop_exits_without_spawning_when_no_authority(self) -> None:
        """No active lease -> loop exits cleanly and never calls wg spawn."""
        self._write_driftdriver_stub(mode="observe", lease_active=False)
        res = self._run_loop()
        self.assertEqual(res.returncode, 0, f"stderr: {res.stderr}")
        events = self._events()
        self.assertIn("authority.revoked", events)
        self.assertIn("loop.exited", events)
        self.assertIn("no_authority", events)
        # Crucially: no agent was spawned without authority.
        if self.spawn_log.exists():
            spawns = self.spawn_log.read_text(encoding="utf-8").strip()
            self.assertEqual(spawns, "",
                             "wg spawn must not run without dispatch authority")

    def test_loop_spawns_under_authority_then_exits_on_revocation(self) -> None:
        """Active lease -> spawn occurs; once authority is revoked, loop exits."""
        self._write_wg_stub(ready_tasks="task-A")
        # Authority present on the first cycle only, then revoked.
        self._write_driftdriver_stub(mode="supervise", lease_active=True,
                                     flip_after=1)
        res = self._run_loop()
        self.assertEqual(res.returncode, 0, f"stderr: {res.stderr}")
        events = self._events()
        self.assertIn("agent.spawned", events)
        self.assertIn("task-A", events)
        # After revocation the loop must stop (not spin forever).
        self.assertIn("authority.revoked", events)
        self.assertIn("loop.exited", events)
        # Exactly one spawn under the single cycle of authority.
        spawns = self.spawn_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(spawns), 1, f"expected one spawn, got: {spawns}")
        self.assertIn("task-A", spawns[0])


if __name__ == "__main__":
    unittest.main()
