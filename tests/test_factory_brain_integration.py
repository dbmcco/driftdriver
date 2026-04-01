# ABOUTME: End-to-end integration tests for the full factory brain tick cycle.
# ABOUTME: Simulates crash events, escalation chains, and empty roster scenarios with mocked claude CLI.
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, call

import pytest

from driftdriver.factory_brain.events import EVENTS_REL_PATH, emit_event
from driftdriver.factory_brain.hub_integration import FactoryBrain
from driftdriver.factory_brain.roster import enroll_repo


def _make_repo(tmp_path: Path, name: str = "test-repo") -> Path:
    """Create a minimal repo directory with .workgraph runtime and dispatch-loop.sh."""
    repo = tmp_path / name
    runtime = repo / ".workgraph" / "service" / "runtime"
    runtime.mkdir(parents=True)
    dispatch = repo / ".workgraph" / "executors" / "dispatch-loop.sh"
    dispatch.parent.mkdir(parents=True)
    dispatch.write_text("#!/usr/bin/env bash\necho dispatch\n")
    dispatch.chmod(0o755)
    return repo


def _mock_cli_result(directive_data: dict, *, returncode: int = 0) -> object:
    """Build a mock subprocess.CompletedProcess mimicking claude CLI --output-format json."""
    cli_output = {
        "type": "result",
        "subtype": "success",
        "result": "Done.",
        "structured_output": directive_data,
        "cost_usd": 0.003,
        "is_error": False,
        "duration_ms": 1200,
        "num_turns": 1,
        "session_id": "test-session",
    }

    class FakeResult:
        pass

    r = FakeResult()
    r.returncode = returncode
    r.stdout = json.dumps(cli_output)
    r.stderr = ""
    return r


class TestFullTickWithCrashEvent:
    """Test 1: Full tick with a loop.crashed event producing kill_daemon + start_dispatch_loop."""

    def test_full_tick_with_crash_event(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        # Emit a loop.crashed event
        events_file = repo / EVENTS_REL_PATH
        emit_event(events_file, kind="loop.crashed", repo=repo.name, payload={"exit_code": 1})

        # Set up hub
        hub_dir = tmp_path / "hub-data"
        hub_dir.mkdir()

        brain = FactoryBrain(
            hub_data_dir=hub_dir,
            workspace_roots=[tmp_path],
            dry_run=True,
        )
        enroll_repo(brain.roster, path=str(repo), target="onboarded")

        # Mock response: kill_daemon + start_dispatch_loop
        directive_data = {
            "reasoning": "Dispatch loop crashed. Kill daemon and restart.",
            "directives": [
                {"action": "kill_daemon", "params": {"repo": repo.name}},
                {"action": "start_dispatch_loop", "params": {"repo": repo.name}},
            ],
            "telegram": None,
            "escalate": False,
        }

        with (
            patch("driftdriver.factory_brain.brain.subprocess.run", return_value=_mock_cli_result(directive_data)),
            patch("driftdriver.factory_brain.router.guarded_add_drift_task", return_value="mocked"),
            patch("driftdriver.factory_brain.router.record_finding_ledger"),
        ):
            results = brain.tick(snapshot={"repos": 1})

        # Verify results returned
        assert len(results) >= 1
        first = results[0]
        assert first["directives_executed"] == 2
        assert first["reasoning"] == "Dispatch loop crashed. Kill daemon and restart."

        # Verify brain log files exist (dry_run=True writes to brain-dryruns.jsonl)
        assert (brain.log_dir / "brain-dryruns.jsonl").exists()

        # Verify log content
        jsonl_lines = (brain.log_dir / "brain-dryruns.jsonl").read_text().strip().splitlines()
        assert len(jsonl_lines) >= 1
        record = json.loads(jsonl_lines[0])
        assert record["tier"] == 1
        assert record["dry_run"] is True
        assert record["reasoning"] == "Dispatch loop crashed. Kill daemon and restart."


class TestFullTickWithEscalation:
    """Test 2: Full tick where Tier 1 escalates and Tier 2 returns an unenroll directive."""

    def test_full_tick_with_escalation(self, tmp_path: Path) -> None:
        repo = _make_repo(tmp_path)

        # Emit a loop.crashed event
        events_file = repo / EVENTS_REL_PATH
        emit_event(events_file, kind="loop.crashed", repo=repo.name, payload={"exit_code": 1})

        # Set up hub
        hub_dir = tmp_path / "hub-data"
        hub_dir.mkdir()

        brain = FactoryBrain(
            hub_data_dir=hub_dir,
            workspace_roots=[tmp_path],
            dry_run=True,
        )
        enroll_repo(brain.roster, path=str(repo), target="onboarded")

        # Tier 1 response: escalate
        tier1_data = {
            "reasoning": "Loop crashed repeatedly. Escalating to Tier 2.",
            "directives": [
                {"action": "noop", "params": {"reason": "deferring to tier 2"}},
            ],
            "telegram": "Crash loop detected, escalating.",
            "escalate": True,
        }

        # Heartbeat response (heartbeat file missing, so stale heartbeat fires)
        heartbeat_data = {
            "reasoning": "Heartbeat stale, acknowledging.",
            "directives": [
                {"action": "noop", "params": {"reason": "heartbeat noted"}},
            ],
            "telegram": None,
            "escalate": False,
        }

        # Tier 2 response: unenroll the problematic repo
        tier2_data = {
            "reasoning": "Repo is consistently unstable. Unenrolling to stop damage.",
            "directives": [
                {"action": "unenroll", "params": {"repo": repo.name}},
            ],
            "telegram": None,
            "escalate": False,
        }

        mock_results = [
            _mock_cli_result(tier1_data),
            _mock_cli_result(heartbeat_data),
            _mock_cli_result(tier2_data),
        ]

        with (
            patch("driftdriver.factory_brain.brain.subprocess.run", side_effect=mock_results),
            patch("driftdriver.factory_brain.router.guarded_add_drift_task", return_value="mocked"),
            patch("driftdriver.factory_brain.router.record_finding_ledger"),
        ):
            results = brain.tick(snapshot={"repos": 1})

        # Verify we got results from multiple tiers
        assert len(results) >= 2

        # First result should be the Tier 1 escalation
        assert results[0]["tier"] == 1
        assert results[0]["escalated"] is True

        # There should be a Tier 2 result
        tier2_results = [r for r in results if r["tier"] == 2]
        assert len(tier2_results) >= 1


class TestFullTickEmptyRoster:
    """Test 3: Full tick with no enrolled repos."""

    def test_full_tick_empty_roster(self, tmp_path: Path) -> None:
        hub_dir = tmp_path / "hub-data"
        hub_dir.mkdir()

        brain = FactoryBrain(
            hub_data_dir=hub_dir,
            workspace_roots=[tmp_path],
        )

        # No repos enrolled — tick should return empty, no CLI calls
        with patch("subprocess.run") as mock_run:
            results = brain.tick()

        assert results == []
        mock_run.assert_not_called()
