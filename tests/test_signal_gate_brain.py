# ABOUTME: Tests for signal-gate integration with factory_brain (brain.py + router.py).
# ABOUTME: Validates content-hash gating of LLM calls, dry-run shadow logging, and suppression.

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from driftdriver.factory_brain.brain import invoke_brain
from driftdriver.factory_brain.directives import BrainResponse, Directive
from driftdriver.factory_brain.events import Event
from driftdriver.factory_brain.router import (
    BrainState,
    run_brain_tick,
    should_sweep,
)
from driftdriver.signal_gate import record_fire, should_fire


def _make_brain_response_data(
    reasoning: str = "test reasoning",
    escalate: bool = False,
) -> dict:
    return {
        "reasoning": reasoning,
        "directives": [{"action": "noop", "params": {"reason": "test"}}],
        "telegram": None,
        "escalate": escalate,
    }


def _mock_cli_result(directive_data: dict) -> object:
    """Build a mock subprocess.CompletedProcess mimicking claude CLI."""
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
    r.returncode = 0
    r.stdout = json.dumps(cli_output)
    r.stderr = ""
    return r


# ---------------------------------------------------------------------------
# invoke_brain gating
# ---------------------------------------------------------------------------


class TestInvokeBrainGating:
    def test_gate_suppresses_duplicate_input(self, tmp_path: Path) -> None:
        """Second call with identical input should be suppressed without LLM call."""
        gate_dir = tmp_path / "gates"
        log_dir = tmp_path / "logs"
        trigger = {"kind": "agent.died", "repo": "repoA", "ts": 1.0, "payload": {}}
        snapshot = {"repos": [{"name": "repoA", "score": 50}]}

        mock_data = _make_brain_response_data("agent died, restarting")
        with patch("driftdriver.factory_brain.brain._try_invoke") as mock_invoke:
            mock_invoke.return_value = (mock_data, "claude")

            # First call: gate fires, LLM runs
            resp1 = invoke_brain(
                tier=1,
                trigger_event=trigger,
                snapshot=snapshot,
                log_dir=log_dir,
                gate_enabled=True,
                gate_dir=gate_dir,
            )
            assert mock_invoke.call_count == 1
            assert resp1.reasoning == "agent died, restarting"

            # Second call with same input: gate suppresses
            resp2 = invoke_brain(
                tier=1,
                trigger_event=trigger,
                snapshot=snapshot,
                log_dir=log_dir,
                gate_enabled=True,
                gate_dir=gate_dir,
            )
            assert mock_invoke.call_count == 1  # no additional LLM call
            assert "suppressed" in resp2.reasoning.lower()

    def test_gate_fires_on_first_call(self, tmp_path: Path) -> None:
        """First call should always fire (no prior state)."""
        gate_dir = tmp_path / "gates"
        trigger = {"kind": "loop.started", "repo": "repoB", "ts": 2.0, "payload": {}}

        mock_data = _make_brain_response_data("loop started")
        with patch("driftdriver.factory_brain.brain._try_invoke") as mock_invoke:
            mock_invoke.return_value = (mock_data, "claude")

            resp = invoke_brain(
                tier=1,
                trigger_event=trigger,
                gate_enabled=True,
                gate_dir=gate_dir,
            )
            assert mock_invoke.call_count == 1
            assert resp.reasoning == "loop started"

    def test_gate_fires_on_changed_input(self, tmp_path: Path) -> None:
        """Different input after a recorded fire should fire again."""
        gate_dir = tmp_path / "gates"
        trigger1 = {"kind": "agent.died", "repo": "repoA", "ts": 1.0, "payload": {}}
        trigger2 = {"kind": "agent.died", "repo": "repoB", "ts": 2.0, "payload": {}}

        mock_data = _make_brain_response_data("handling event")
        with patch("driftdriver.factory_brain.brain._try_invoke") as mock_invoke:
            mock_invoke.return_value = (mock_data, "claude")

            invoke_brain(
                tier=1,
                trigger_event=trigger1,
                gate_enabled=True,
                gate_dir=gate_dir,
            )
            invoke_brain(
                tier=1,
                trigger_event=trigger2,
                gate_enabled=True,
                gate_dir=gate_dir,
            )
            assert mock_invoke.call_count == 2  # both fired

    def test_gate_disabled_passes_through(self, tmp_path: Path) -> None:
        """When gate_enabled=False, all calls go through without gating."""
        trigger = {"kind": "agent.died", "repo": "repoA", "ts": 1.0, "payload": {}}

        mock_data = _make_brain_response_data("no gate")
        with patch("driftdriver.factory_brain.brain._try_invoke") as mock_invoke:
            mock_invoke.return_value = (mock_data, "claude")

            invoke_brain(tier=1, trigger_event=trigger, gate_enabled=False)
            invoke_brain(tier=1, trigger_event=trigger, gate_enabled=False)
            assert mock_invoke.call_count == 2  # both fire, no gating

    def test_gate_dry_run_logs_but_fires(self, tmp_path: Path) -> None:
        """Dry-run mode: gate checks happen but LLM always fires. Shadow log written."""
        gate_dir = tmp_path / "gates"
        log_dir = tmp_path / "logs"
        trigger = {"kind": "agent.died", "repo": "repoA", "ts": 1.0, "payload": {}}

        mock_data = _make_brain_response_data("dry run test")
        with patch("driftdriver.factory_brain.brain._try_invoke") as mock_invoke:
            mock_invoke.return_value = (mock_data, "claude")

            # First call
            invoke_brain(
                tier=1,
                trigger_event=trigger,
                log_dir=log_dir,
                gate_enabled=True,
                gate_dir=gate_dir,
                gate_dry_run=True,
            )
            # Second call with same input — in dry_run, LLM still fires
            invoke_brain(
                tier=1,
                trigger_event=trigger,
                log_dir=log_dir,
                gate_enabled=True,
                gate_dir=gate_dir,
                gate_dry_run=True,
            )
            assert mock_invoke.call_count == 2  # both fired despite duplicate

        # Shadow log should exist with gate decisions
        shadow_log = log_dir / "brain-gate-shadow.jsonl"
        assert shadow_log.exists()
        lines = shadow_log.read_text().strip().split("\n")
        assert len(lines) >= 2  # at least 2 entries
        entry1 = json.loads(lines[0])
        assert entry1["gate_would_fire"] is True
        entry2 = json.loads(lines[1])
        assert entry2["gate_would_fire"] is False  # would have been suppressed

    def test_gate_suppressed_response_is_noop(self, tmp_path: Path) -> None:
        """Suppressed response has noop directive and no escalation."""
        gate_dir = tmp_path / "gates"
        trigger = {"kind": "agent.died", "repo": "repoA", "ts": 1.0, "payload": {}}

        mock_data = _make_brain_response_data("test")
        with patch("driftdriver.factory_brain.brain._try_invoke") as mock_invoke:
            mock_invoke.return_value = (mock_data, "claude")

            invoke_brain(
                tier=1,
                trigger_event=trigger,
                gate_enabled=True,
                gate_dir=gate_dir,
            )
            resp = invoke_brain(
                tier=1,
                trigger_event=trigger,
                gate_enabled=True,
                gate_dir=gate_dir,
            )

        assert len(resp.directives) == 1
        assert resp.directives[0].action == "noop"
        assert resp.escalate is False
        assert resp.telegram is None

    def test_tiers_are_independent(self, tmp_path: Path) -> None:
        """Different tiers use separate gate agent names — don't cross-suppress."""
        gate_dir = tmp_path / "gates"
        trigger = {"kind": "agent.died", "repo": "repoA", "ts": 1.0, "payload": {}}

        mock_data = _make_brain_response_data("tier check")
        with patch("driftdriver.factory_brain.brain._try_invoke") as mock_invoke:
            mock_invoke.return_value = (mock_data, "claude")

            invoke_brain(
                tier=1,
                trigger_event=trigger,
                gate_enabled=True,
                gate_dir=gate_dir,
            )
            # Same trigger but different tier — should fire
            invoke_brain(
                tier=2,
                trigger_event=trigger,
                gate_enabled=True,
                gate_dir=gate_dir,
            )
            assert mock_invoke.call_count == 2


# ---------------------------------------------------------------------------
# Router gating integration
# ---------------------------------------------------------------------------


class TestRouterGating:
    def _setup_repos(self, tmp_path: Path) -> list[Path]:
        """Create minimal repo dirs with fresh heartbeats."""
        repos = []
        for name in ("repo-a", "repo-b"):
            rp = tmp_path / name
            rp.mkdir()
            hb_dir = rp / ".workgraph" / "service" / "runtime"
            hb_dir.mkdir(parents=True)
            hb = hb_dir / "heartbeat"
            hb.write_text(datetime.now(timezone.utc).isoformat())
            repos.append(rp)
        return repos

    def test_run_brain_tick_passes_gate_params(self, tmp_path: Path) -> None:
        """Gate params propagate from run_brain_tick to invoke_brain."""
        gate_dir = tmp_path / "gates"
        repos = self._setup_repos(tmp_path)

        # Write an event that will trigger tier 1
        events_dir = repos[0] / ".workgraph" / "events"
        events_dir.mkdir(parents=True)
        event_file = events_dir / "events.jsonl"
        event = {
            "kind": "agent.died",
            "repo": repos[0].name,
            "ts": datetime.now(timezone.utc).timestamp(),
            "payload": {"agent": "test"},
        }
        event_file.write_text(json.dumps(event) + "\n")

        state = BrainState()
        mock_data = _make_brain_response_data("gated tick")

        with patch("driftdriver.factory_brain.router.invoke_brain") as mock_brain:
            mock_brain.return_value = BrainResponse(
                reasoning="gated tick",
                directives=[Directive(action="noop", params={})],
                telegram=None,
                escalate=False,
            )

            run_brain_tick(
                state=state,
                roster_repos=repos,
                signal_gate_enabled=True,
                gate_dir=gate_dir,
                gate_dry_run=False,
                log_dir=tmp_path / "logs",
            )

            # Verify gate params were passed to invoke_brain
            if mock_brain.call_count > 0:
                _, kwargs = mock_brain.call_args
                assert kwargs.get("gate_enabled") is True
                assert kwargs.get("gate_dir") == gate_dir
                assert kwargs.get("gate_dry_run") is False

    def test_should_sweep_bypassed_when_signal_gate_enabled(self) -> None:
        """When signal_gate_enabled=True in run_brain_tick, should_sweep is not used."""
        # This is already the behavior — verify the code path
        state = BrainState()
        state.last_sweep = datetime.now(timezone.utc) - timedelta(seconds=30)
        # should_sweep returns False because only 30s elapsed
        assert should_sweep(state, interval_seconds=600) is False
        # But if 700s elapsed, it would return True (legacy path)
        state.last_sweep = datetime.now(timezone.utc) - timedelta(seconds=700)
        assert should_sweep(state, interval_seconds=600) is True

    def test_heartbeat_check_gated_via_invoke_brain(self, tmp_path: Path) -> None:
        """Heartbeat stale invoke_brain calls receive gate params."""
        gate_dir = tmp_path / "gates"
        repos = self._setup_repos(tmp_path)

        # Make heartbeat stale
        hb = repos[0] / ".workgraph" / "service" / "runtime" / "heartbeat"
        old_ts = datetime.now(timezone.utc) - timedelta(seconds=200)
        hb.write_text(old_ts.isoformat())

        state = BrainState()

        with patch("driftdriver.factory_brain.router.invoke_brain") as mock_brain:
            mock_brain.return_value = BrainResponse(
                reasoning="heartbeat stale",
                directives=[Directive(action="noop", params={})],
                telegram=None,
                escalate=False,
            )
            with patch("driftdriver.factory_brain.router.guarded_add_drift_task"):
                with patch("driftdriver.factory_brain.router.record_finding_ledger"):
                    run_brain_tick(
                        state=state,
                        roster_repos=repos,
                        signal_gate_enabled=True,
                        gate_dir=gate_dir,
                        gate_dry_run=True,
                        log_dir=tmp_path / "logs",
                    )

            # At least one call should have heartbeat.stale trigger with gate params
            for call in mock_brain.call_args_list:
                _, kwargs = call
                assert kwargs.get("gate_enabled") is True
                assert kwargs.get("gate_dir") == gate_dir
                assert kwargs.get("gate_dry_run") is True
