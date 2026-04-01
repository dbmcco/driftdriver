# driftdriver/paia_agent_health/collector.py
# ABOUTME: Collects quality signals from paia-memory, paia-events, paia-shell, and workgraph.
# ABOUTME: Returns a SignalBundle used by the analyzer. Stateless — tolerates partial failures.

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

MEMORY_URL = os.environ.get("PAIA_MEMORY_URL", "http://localhost:3510")
EVENTS_URL = os.environ.get("PAIA_EVENTS_URL", "http://localhost:3511")
SHELL_URL = os.environ.get("PAIA_SHELL_URL", "http://localhost:3540")

AGENT_CONFIGS = [
    {"name": "samantha", "tenant_id": "Samantha", "repo": "samantha"},
    {"name": "derek",    "tenant_id": "Derek",    "repo": "derek"},
    {"name": "ingrid",   "tenant_id": "Ingrid",   "repo": "ingrid"},
    {"name": "caroline", "tenant_id": "Caroline", "repo": "caroline"},
]

EXPERIMENTS = os.environ.get("EXPERIMENTS_DIR", os.path.expanduser("~/projects/experiments"))


@dataclass
class AgentSignals:
    name: str
    tenant_id: str
    conversation_turns: list[dict] = field(default_factory=list)
    session_extracts: list[dict] = field(default_factory=list)
    task_events: list[dict] = field(default_factory=list)
    tool_events: list[dict] = field(default_factory=list)
    failed_tasks: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class SignalBundle:
    agents: dict[str, AgentSignals]
    shell_metrics: list[dict]
    collected_at: str


async def _fetch_atoms(client: httpx.AsyncClient, tenant_id: str, event_type: str, limit: int = 50) -> list[dict]:
    url = f"{MEMORY_URL}/retrieve/atoms"
    params = {"tenant_id": tenant_id, "event_type": event_type, "limit": limit}
    resp = await client.get(url, params=params, timeout=10)
    return resp.json().get("atoms", [])


async def _fetch_events(client: httpx.AsyncClient, prefix: str, limit: int = 200) -> list[dict]:
    url = f"{EVENTS_URL}/v1/events"
    params = {"event_type_prefix": prefix, "limit": limit}
    resp = await client.get(url, params=params, timeout=10)
    return resp.json().get("events", [])


def _run_wg_list(repo: str, status: str) -> list[dict]:
    """Run wg list --status <status> --json in the agent repo. Returns empty list on failure."""
    repo_path = os.path.join(EXPERIMENTS, repo)
    try:
        result = subprocess.run(
            ["wg", "list", "--status", status, "--json"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return json.loads(result.stdout)
        return []
    except Exception as exc:
        logger.debug("wg list failed for %s/%s: %s", repo, status, exc)
        return []


async def _collect_agent(client: httpx.AsyncClient, cfg: dict) -> AgentSignals:
    signals = AgentSignals(name=cfg["name"], tenant_id=cfg["tenant_id"])
    try:
        signals.conversation_turns = await _fetch_atoms(client, cfg["tenant_id"], "conversation.turn")
    except Exception as exc:
        signals.errors.append(f"conversation_turns: {exc}")

    try:
        signals.session_extracts = await _fetch_atoms(client, cfg["tenant_id"], "session.extract", limit=10)
    except Exception as exc:
        signals.errors.append(f"session_extracts: {exc}")

    try:
        all_task_events = await _fetch_events(client, "agent.task")
        agent_name = cfg["name"]
        signals.task_events = [
            e for e in all_task_events
            if e.get("data", {}).get("agent") == agent_name
            or agent_name in str(e.get("data", {}))
        ]
    except Exception as exc:
        signals.errors.append(f"task_events: {exc}")

    try:
        all_tool_events = await _fetch_events(client, "agent.tool.called")
        agent_name = cfg["name"]
        signals.tool_events = [
            e for e in all_tool_events
            if e.get("data", {}).get("agent") == agent_name
        ]
    except Exception as exc:
        signals.errors.append(f"tool_events: {exc}")

    signals.failed_tasks = _run_wg_list(cfg["repo"], "failed")
    return signals


async def collect_signals(window_hours: int = 24) -> SignalBundle:
    """Collect all agent quality signals. Tolerates partial failures."""
    agents: dict[str, AgentSignals] = {}
    shell_metrics: list[dict] = []

    async with httpx.AsyncClient() as client:
        for cfg in AGENT_CONFIGS:
            agents[cfg["name"]] = await _collect_agent(client, cfg)

        try:
            resp = await client.get(f"{SHELL_URL}/api/health/metrics", timeout=10)
            shell_metrics = resp.json() if isinstance(resp.json(), list) else []
        except Exception as exc:
            logger.debug("shell metrics fetch failed: %s", exc)

    return SignalBundle(
        agents=agents,
        shell_metrics=shell_metrics,
        collected_at=datetime.now(timezone.utc).isoformat(),
    )
