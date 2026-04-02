# tests/test_paia_agent_health_router.py
# ABOUTME: Tests agent health tick lane in the factory brain router.

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
from driftdriver.factory_brain.router import BrainState, should_run_agent_health


def test_brain_state_has_agent_health_fields():
    state = BrainState()
    assert hasattr(state, "last_agent_health_check")
    assert state.last_agent_health_check is None


def test_should_run_agent_health_first_time():
    state = BrainState()
    assert should_run_agent_health(state, interval_seconds=86400) is True


def test_should_run_agent_health_not_yet_due():
    state = BrainState()
    state.last_agent_health_check = datetime.now(timezone.utc) - timedelta(hours=1)
    assert should_run_agent_health(state, interval_seconds=86400) is False


def test_should_run_agent_health_due():
    state = BrainState()
    state.last_agent_health_check = datetime.now(timezone.utc) - timedelta(hours=25)
    assert should_run_agent_health(state, interval_seconds=86400) is True
