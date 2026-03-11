# ABOUTME: Brain event router — watches events, runs timer-based safety nets, routes
# ABOUTME: triggers to appropriate tiers, handles escalation chains, executes directives.
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from driftdriver.factory_brain.brain import invoke_brain
from driftdriver.factory_brain.directives import BrainResponse, execute_directives
from driftdriver.factory_brain.events import TIER_ROUTING, Event, aggregate_events

logger = logging.getLogger(__name__)

HEARTBEAT_REL_PATH = Path(".workgraph") / "service" / "runtime" / "heartbeat"


@dataclass
class BrainState:
    """Mutable state for the router across ticks."""

    last_heartbeat_check: datetime | None = None
    last_sweep: datetime | None = None
    last_event_ts: str = ""
    recent_directives: list[dict] = field(default_factory=list)
    tier1_escalation_count: int = 0


def route_event(event: Event) -> int:
    """Lookup event kind in TIER_ROUTING, default to 1 for unknown."""
    return TIER_ROUTING.get(event.kind, 1)


def check_heartbeats(
    repo_paths: list[Path],
    *,
    max_age_seconds: int = 90,
) -> list[Path]:
    """Return repos where heartbeat is stale or missing."""
    now = datetime.now(timezone.utc)
    stale: list[Path] = []

    for repo_path in repo_paths:
        hb_file = repo_path / HEARTBEAT_REL_PATH
        try:
            raw = hb_file.read_text().strip()
            ts = datetime.fromisoformat(raw)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age = (now - ts).total_seconds()
            if age > max_age_seconds:
                stale.append(repo_path)
        except (ValueError, OSError):
            stale.append(repo_path)

    return stale


def should_sweep(state: BrainState, *, interval_seconds: int = 600) -> bool:
    """True if last_sweep is None or enough time has elapsed."""
    if state.last_sweep is None:
        return True
    elapsed = (datetime.now(timezone.utc) - state.last_sweep).total_seconds()
    return elapsed >= interval_seconds


def process_brain_response(
    response: BrainResponse,
    *,
    tier: int,
    state: BrainState,
    repo_paths: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict:
    """Execute directives from a brain response and track state."""
    results = execute_directives(
        response.directives,
        dry_run=dry_run,
        repo_paths=repo_paths,
    )

    # Track recent directives (keep last 50)
    for d in response.directives:
        state.recent_directives.append(
            {"action": d.action, "params": d.params}
        )
    state.recent_directives = state.recent_directives[-50:]

    # Detect escalation
    escalated = response.escalate and tier < 3
    next_tier = tier + 1 if escalated else None

    if escalated:
        state.tier1_escalation_count += 1

    return {
        "tier": tier,
        "directives_executed": len(response.directives),
        "results": results,
        "escalated": escalated,
        "next_tier": next_tier,
        "reasoning": response.reasoning,
        "telegram": response.telegram,
    }


def run_brain_tick(
    *,
    state: BrainState,
    roster_repos: list[Path],
    snapshot: dict | None = None,
    heuristic_recommendation: str | None = None,
    log_dir: Path | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Main tick function — aggregate events, route by tier, handle escalation."""
    all_results: list[dict] = []

    # Build repo_paths lookup for directive execution
    repo_paths_lookup: dict[str, str] = {}
    for rp in roster_repos:
        repo_paths_lookup[rp.name] = str(rp)

    # 1. Aggregate new events from all repos
    since = float(state.last_event_ts) if state.last_event_ts else None
    events = aggregate_events(roster_repos, since=since)

    # Update last_event_ts
    if events:
        state.last_event_ts = str(events[-1].ts)

    # 2. Route events to tiers
    tier1_events: list[Event] = []
    tier2_events: list[Event] = []
    tier3_events: list[Event] = []

    for ev in events:
        tier = route_event(ev)
        if tier == 1:
            tier1_events.append(ev)
        elif tier == 2:
            tier2_events.append(ev)
        else:
            tier3_events.append(ev)

    # 3. Process Tier 1 events
    tier1_reasoning_parts: list[str] = []
    for ev in tier1_events:
        trigger = {"kind": ev.kind, "repo": ev.repo, "ts": ev.ts, "payload": ev.payload}
        response = invoke_brain(
            tier=1,
            trigger_event=trigger,
            recent_events=[{"kind": e.kind, "repo": e.repo, "ts": e.ts, "payload": e.payload} for e in events],
            snapshot=snapshot,
            heuristic_recommendation=heuristic_recommendation,
            recent_directives=state.recent_directives,
            log_dir=log_dir,
        )
        result = process_brain_response(
            response,
            tier=1,
            state=state,
            repo_paths=repo_paths_lookup,
            dry_run=dry_run,
        )
        all_results.append(result)
        tier1_reasoning_parts.append(response.reasoning)

        # If escalated, add to tier2 queue
        if result["escalated"]:
            tier2_events.append(Event(
                kind="tier1.escalation",
                repo=ev.repo,
                ts=ev.ts,
                payload={"original_kind": ev.kind, "reason": response.reasoning},
            ))

    # 4. Heartbeat check (60s timer)
    now = datetime.now(timezone.utc)
    run_heartbeat = (
        state.last_heartbeat_check is None
        or (now - state.last_heartbeat_check).total_seconds() >= 60
    )
    if run_heartbeat:
        state.last_heartbeat_check = now
        stale_repos = check_heartbeats(roster_repos)
        for repo_path in stale_repos:
            trigger = {
                "kind": "heartbeat.stale",
                "repo": str(repo_path),
                "ts": now.timestamp(),
                "payload": {"repo": str(repo_path)},
            }
            response = invoke_brain(
                tier=1,
                trigger_event=trigger,
                snapshot=snapshot,
                recent_directives=state.recent_directives,
                log_dir=log_dir,
            )
            result = process_brain_response(
                response,
                tier=1,
                state=state,
                repo_paths=repo_paths_lookup,
                dry_run=dry_run,
            )
            all_results.append(result)

    # 5. Process Tier 2
    run_tier2 = bool(tier2_events) or should_sweep(state)
    if run_tier2:
        state.last_sweep = now
        recent_as_dicts = [
            {"kind": e.kind, "repo": e.repo, "ts": e.ts, "payload": e.payload}
            for e in events
        ]
        tier2_trigger_events = [
            {"kind": e.kind, "repo": e.repo, "ts": e.ts, "payload": e.payload}
            for e in tier2_events
        ]
        roster = {
            "repos": [str(rp) for rp in roster_repos],
        }
        tier1_reasoning = "\n".join(tier1_reasoning_parts) if tier1_reasoning_parts else None

        response = invoke_brain(
            tier=2,
            trigger_event=tier2_trigger_events[0] if tier2_trigger_events else None,
            recent_events=recent_as_dicts,
            snapshot=snapshot,
            heuristic_recommendation=heuristic_recommendation,
            recent_directives=state.recent_directives,
            roster=roster,
            tier1_reasoning=tier1_reasoning,
            log_dir=log_dir,
        )
        result = process_brain_response(
            response,
            tier=2,
            state=state,
            repo_paths=repo_paths_lookup,
            dry_run=dry_run,
        )
        all_results.append(result)

        # Handle tier2 escalation to tier3
        if result["escalated"]:
            tier3_events.append(Event(
                kind="tier2.escalation",
                repo="factory",
                ts=now.timestamp(),
                payload={"reason": response.reasoning},
            ))

    # 6. Process Tier 3
    if tier3_events:
        recent_as_dicts = [
            {"kind": e.kind, "repo": e.repo, "ts": e.ts, "payload": e.payload}
            for e in events
        ]
        roster = {
            "repos": [str(rp) for rp in roster_repos],
        }
        escalation_reason = "; ".join(
            e.payload.get("reason", e.kind) for e in tier3_events
        )
        response = invoke_brain(
            tier=3,
            recent_events=recent_as_dicts,
            snapshot=snapshot,
            recent_directives=state.recent_directives,
            roster=roster,
            escalation_reason=escalation_reason,
            tier1_reasoning="\n".join(tier1_reasoning_parts) if tier1_reasoning_parts else None,
            log_dir=log_dir,
        )
        result = process_brain_response(
            response,
            tier=3,
            state=state,
            repo_paths=repo_paths_lookup,
            dry_run=dry_run,
        )
        all_results.append(result)

    return all_results
