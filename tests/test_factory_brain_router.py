# ABOUTME: Tests for factory brain router — event routing, heartbeat checks, sweep timing,
# ABOUTME: brain response processing with escalation, and directive tracking.
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from driftdriver.factory_brain.directives import BrainResponse, Directive
from driftdriver.factory_brain.events import Event
from driftdriver.factory_brain.router import (
    HEARTBEAT_REL_PATH,
    BrainState,
    check_heartbeats,
    process_brain_response,
    route_event,
    should_sweep,
)


def test_route_event_tier1() -> None:
    ev = Event(kind="loop.started", repo="/tmp/r", ts=1.0, payload={})
    assert route_event(ev) == 1

    ev2 = Event(kind="agent.died", repo="/tmp/r", ts=1.0, payload={})
    assert route_event(ev2) == 1

    ev3 = Event(kind="heartbeat.stale", repo="/tmp/r", ts=1.0, payload={})
    assert route_event(ev3) == 1


def test_route_event_tier2() -> None:
    ev = Event(kind="tasks.exhausted", repo="/tmp/r", ts=1.0, payload={})
    assert route_event(ev) == 2

    ev2 = Event(kind="repo.discovered", repo="/tmp/r", ts=1.0, payload={})
    assert route_event(ev2) == 2

    ev3 = Event(kind="tier1.escalation", repo="/tmp/r", ts=1.0, payload={})
    assert route_event(ev3) == 2


def test_route_event_unknown_defaults_tier1() -> None:
    ev = Event(kind="some.unknown.event", repo="/tmp/r", ts=1.0, payload={})
    assert route_event(ev) == 1


def test_check_heartbeats_detects_stale(tmp_path: Path) -> None:
    repo = tmp_path / "repo-a"
    hb_file = repo / HEARTBEAT_REL_PATH
    hb_file.parent.mkdir(parents=True, exist_ok=True)

    # Write a timestamp that is 200 seconds old
    old_ts = datetime.now(timezone.utc) - timedelta(seconds=200)
    hb_file.write_text(old_ts.isoformat())

    stale = check_heartbeats([repo], max_age_seconds=90)
    assert repo in stale


def test_check_heartbeats_fresh(tmp_path: Path) -> None:
    repo = tmp_path / "repo-a"
    hb_file = repo / HEARTBEAT_REL_PATH
    hb_file.parent.mkdir(parents=True, exist_ok=True)

    # Write a current timestamp
    now_ts = datetime.now(timezone.utc)
    hb_file.write_text(now_ts.isoformat())

    stale = check_heartbeats([repo], max_age_seconds=90)
    assert repo not in stale


def test_check_heartbeats_missing_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo-missing"
    # Don't create heartbeat file at all
    stale = check_heartbeats([repo], max_age_seconds=90)
    assert repo in stale


def test_should_sweep_first_time() -> None:
    state = BrainState()
    assert state.last_sweep is None
    assert should_sweep(state) is True


def test_should_sweep_too_soon() -> None:
    state = BrainState()
    state.last_sweep = datetime.now(timezone.utc) - timedelta(seconds=30)
    assert should_sweep(state, interval_seconds=600) is False


def test_process_brain_response_escalation() -> None:
    response = BrainResponse(
        reasoning="Agent died and needs restart",
        directives=[Directive(action="noop", params={"reason": "investigating"})],
        telegram="Agent down in repo-x",
        escalate=True,
    )
    state = BrainState()

    result = process_brain_response(
        response,
        tier=1,
        state=state,
        dry_run=True,
    )

    assert result["tier"] == 1
    assert result["escalated"] is True
    assert result["next_tier"] == 2
    assert result["directives_executed"] == 1
    assert result["reasoning"] == "Agent died and needs restart"
    assert result["telegram"] == "Agent down in repo-x"
    assert state.tier1_escalation_count == 1


def test_process_brain_response_no_escalation() -> None:
    response = BrainResponse(
        reasoning="All clear",
        directives=[Directive(action="noop", params={"reason": "healthy"})],
        telegram=None,
        escalate=False,
    )
    state = BrainState()

    result = process_brain_response(
        response,
        tier=1,
        state=state,
        dry_run=True,
    )

    assert result["tier"] == 1
    assert result["escalated"] is False
    assert result["next_tier"] is None
    assert result["directives_executed"] == 1
    assert state.tier1_escalation_count == 0


def test_process_brain_response_tracks_directives() -> None:
    state = BrainState()

    # Process multiple responses and check directive tracking
    for i in range(5):
        response = BrainResponse(
            reasoning=f"Response {i}",
            directives=[
                Directive(action="noop", params={"reason": f"step-{i}"}),
            ],
            escalate=False,
        )
        process_brain_response(
            response,
            tier=1,
            state=state,
            dry_run=True,
        )

    assert len(state.recent_directives) == 5
    assert state.recent_directives[0] == {"action": "noop", "params": {"reason": "step-0"}}
    assert state.recent_directives[4] == {"action": "noop", "params": {"reason": "step-4"}}

    # Verify capping at 50
    for i in range(50):
        response = BrainResponse(
            reasoning=f"Bulk {i}",
            directives=[Directive(action="noop", params={"reason": f"bulk-{i}"})],
            escalate=False,
        )
        process_brain_response(response, tier=1, state=state, dry_run=True)

    assert len(state.recent_directives) == 50
    # The 5 initial "step-*" entries should have been trimmed,
    # leaving bulk-0 through bulk-49 as the last 50
    assert state.recent_directives[0]["params"]["reason"] == "bulk-0"
    assert state.recent_directives[-1]["params"]["reason"] == "bulk-49"


def test_process_brain_response_no_escalation_at_tier3() -> None:
    """Escalation at tier 3 should NOT escalate further."""
    response = BrainResponse(
        reasoning="Critical issue",
        directives=[Directive(action="noop", params={"reason": "critical"})],
        escalate=True,
    )
    state = BrainState()

    result = process_brain_response(
        response,
        tier=3,
        state=state,
        dry_run=True,
    )

    assert result["escalated"] is False
    assert result["next_tier"] is None
