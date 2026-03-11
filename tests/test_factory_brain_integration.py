# ABOUTME: End-to-end integration tests for the full factory brain tick cycle.
# ABOUTME: Simulates crash events, escalation chains, and empty roster scenarios with mocked Anthropic API.
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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


def _mock_anthropic_response(tool_input: dict) -> SimpleNamespace:
    """Build a SimpleNamespace mimicking an Anthropic messages.create() response."""
    tool_block = SimpleNamespace(
        type="tool_use",
        name="issue_directives",
        input=tool_input,
    )
    usage = SimpleNamespace(input_tokens=150, output_tokens=80)
    return SimpleNamespace(content=[tool_block], usage=usage)


def _build_mock_module(side_effect=None, return_value=None):
    """Build a mock anthropic module with Anthropic().messages.create configured."""
    mock_client = MagicMock()
    if side_effect is not None:
        mock_client.messages.create.side_effect = side_effect
    else:
        mock_client.messages.create.return_value = return_value

    mock_mod = MagicMock()
    mock_mod.Anthropic.return_value = mock_client
    return mock_mod, mock_client


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
        tool_input = {
            "reasoning": "Dispatch loop crashed. Kill daemon and restart.",
            "directives": [
                {"action": "kill_daemon", "params": {"repo": repo.name}},
                {"action": "start_dispatch_loop", "params": {"repo": repo.name}},
            ],
            "telegram": None,
            "escalate": False,
        }
        response = _mock_anthropic_response(tool_input)
        mock_mod, mock_client = _build_mock_module(return_value=response)

        with patch.dict("sys.modules", {"anthropic": mock_mod}):
            results = brain.tick(snapshot={"repos": 1})

        # Verify API was called
        assert mock_client.messages.create.call_count >= 1

        # Verify results returned
        assert len(results) >= 1
        first = results[0]
        assert first["directives_executed"] == 2
        assert first["reasoning"] == "Dispatch loop crashed. Kill daemon and restart."

        # Verify brain log files exist
        assert (brain.log_dir / "brain-invocations.jsonl").exists()
        assert (brain.log_dir / "brain-log.md").exists()

        # Verify log content
        jsonl_lines = (brain.log_dir / "brain-invocations.jsonl").read_text().strip().splitlines()
        assert len(jsonl_lines) >= 1
        record = json.loads(jsonl_lines[0])
        assert record["tier"] == 1
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
        tier1_input = {
            "reasoning": "Loop crashed repeatedly. Escalating to Tier 2.",
            "directives": [
                {"action": "noop", "params": {"reason": "deferring to tier 2"}},
            ],
            "telegram": "Crash loop detected, escalating.",
            "escalate": True,
        }
        tier1_response = _mock_anthropic_response(tier1_input)

        # Heartbeat response (heartbeat file missing, so stale heartbeat fires)
        heartbeat_input = {
            "reasoning": "Heartbeat stale, acknowledging.",
            "directives": [
                {"action": "noop", "params": {"reason": "heartbeat noted"}},
            ],
            "telegram": None,
            "escalate": False,
        }
        heartbeat_response = _mock_anthropic_response(heartbeat_input)

        # Tier 2 response: unenroll the problematic repo
        tier2_input = {
            "reasoning": "Repo is consistently unstable. Unenrolling to stop damage.",
            "directives": [
                {"action": "unenroll", "params": {"repo": repo.name}},
            ],
            "telegram": None,
            "escalate": False,
        }
        tier2_response = _mock_anthropic_response(tier2_input)

        mock_mod, mock_client = _build_mock_module(
            side_effect=[tier1_response, heartbeat_response, tier2_response],
        )

        with patch.dict("sys.modules", {"anthropic": mock_mod}):
            results = brain.tick(snapshot={"repos": 1})

        # Verify API was called at least twice (Tier 1 + Tier 2 escalation)
        assert mock_client.messages.create.call_count >= 2

        # Verify we got results from multiple tiers
        assert len(results) >= 2

        # First result should be the Tier 1 escalation
        assert results[0]["tier"] == 1
        assert results[0]["escalated"] is True

        # There should be a Tier 2 result (may be at index 1, 2, etc. depending
        # on heartbeat/sweep checks that also fire)
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

        # No repos enrolled — tick should return empty, no API calls
        mock_mod, mock_client = _build_mock_module(return_value=None)

        with patch.dict("sys.modules", {"anthropic": mock_mod}):
            results = brain.tick()

        assert results == []
        mock_client.messages.create.assert_not_called()
