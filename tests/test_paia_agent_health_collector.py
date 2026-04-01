# tests/test_paia_agent_health_collector.py
# ABOUTME: Tests for SignalBundle collection — mocks all HTTP endpoints.

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from driftdriver.paia_agent_health.collector import (
    AgentSignals, SignalBundle, AGENT_CONFIGS, collect_signals
)


def test_agent_configs_covers_all_agents():
    names = {a["name"] for a in AGENT_CONFIGS}
    assert names == {"samantha", "derek", "ingrid", "caroline"}


def test_agent_configs_tenant_ids():
    # Tenant IDs are capitalized per paia-memory convention
    tenants = {a["name"]: a["tenant_id"] for a in AGENT_CONFIGS}
    assert tenants["samantha"] == "Samantha"
    assert tenants["caroline"] == "Caroline"


@pytest.mark.asyncio
async def test_collect_signals_returns_bundle_with_all_agents():
    mock_atoms_response = {"atoms": [{"content": "user said: that didn't work", "event_type": "conversation.turn"}]}
    mock_events_response = {"events": [{"event_type": "agent.task.failed", "data": {"agent": "samantha"}}]}
    mock_shell_response = {"metrics": []}

    async def mock_get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        if "retrieve/atoms" in url:
            resp.json.return_value = mock_atoms_response
        elif "v1/events" in url:
            resp.json.return_value = mock_events_response
        elif "health/metrics" in url:
            resp.json.return_value = mock_shell_response
        return resp

    with patch("driftdriver.paia_agent_health.collector.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("driftdriver.paia_agent_health.collector._run_wg_list", return_value=[]):
            bundle = await collect_signals()

    assert isinstance(bundle, SignalBundle)
    assert set(bundle.agents.keys()) == {"samantha", "derek", "ingrid", "caroline"}
    assert bundle.collected_at is not None


@pytest.mark.asyncio
async def test_collect_signals_tolerates_http_failure():
    """Partial failures should not prevent bundle creation."""
    async def mock_get_fail(url, **kwargs):
        raise Exception("connection refused")

    with patch("driftdriver.paia_agent_health.collector.httpx") as mock_httpx:
        mock_client = AsyncMock()
        mock_client.get = mock_get_fail
        mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)

        with patch("driftdriver.paia_agent_health.collector._run_wg_list", return_value=[]):
            bundle = await collect_signals()

    # Bundle still created, agents present but with empty signals
    assert set(bundle.agents.keys()) == {"samantha", "derek", "ingrid", "caroline"}
