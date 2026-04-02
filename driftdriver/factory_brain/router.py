# ABOUTME: Brain event router — watches events, runs timer-based safety nets, routes
# ABOUTME: triggers to appropriate tiers, handles escalation chains, executes directives.
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from driftdriver.drift_task_guard import guarded_add_drift_task, record_finding_ledger
from driftdriver.factory_brain.brain import invoke_brain
from driftdriver.factory_brain.directives import BrainResponse, execute_directives
from driftdriver.factory_brain.events import TIER_ROUTING, Event, aggregate_events
from driftdriver.presence import read_all_presence

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
    last_known_stale: set[str] = field(default_factory=set)
    last_agent_health_check: datetime | None = None


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


def repos_with_active_sessions(
    repo_paths: list[Path],
    max_age_seconds: int = 600,
) -> set[str]:
    """Return repo names with active interactive presence records."""
    active: set[str] = set()
    now = datetime.now(timezone.utc)
    for repo_path in repo_paths:
        records = read_all_presence(repo_path)
        for rec in records:
            if rec.actor.actor_class != "interactive":
                continue
            hb = datetime.fromisoformat(rec.last_heartbeat)
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            age = (now - hb).total_seconds()
            if age <= max_age_seconds:
                active.add(repo_path.name)
                break
    return active


def repos_needing_human(repo_paths: list[Path]) -> set[str]:
    """Return repo names with needs_human continuation intent."""
    result: set[str] = set()
    for repo_path in repo_paths:
        control_file = repo_path / ".workgraph" / "service" / "runtime" / "control.json"
        try:
            data = json.loads(control_file.read_text())
            intent = data.get("continuation_intent", {}).get("intent")
            if intent == "needs_human":
                result.add(repo_path.name)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
    return result


def is_signal_claimed(key: str, signals_path: Path | None) -> bool:
    """Check if a signal key is already claimed in a pending-signals.json file."""
    if signals_path is None:
        return False
    try:
        data = json.loads(signals_path.read_text())
        return key in data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False


def should_sweep(state: BrainState, *, interval_seconds: int = 600) -> bool:
    """True if last_sweep is None or enough time has elapsed."""
    if state.last_sweep is None:
        return True
    elapsed = (datetime.now(timezone.utc) - state.last_sweep).total_seconds()
    return elapsed >= interval_seconds


def should_run_agent_health(state: BrainState, *, interval_seconds: int = 86400) -> bool:
    """True if last_agent_health_check is None or interval has elapsed."""
    if state.last_agent_health_check is None:
        return True
    elapsed = (datetime.now(timezone.utc) - state.last_agent_health_check).total_seconds()
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
    signal_gate_enabled: bool = False,
    pending_signals_path: Path | None = None,
    gate_dir: Path | None = None,
    gate_dry_run: bool = False,
) -> list[dict]:
    """Main tick function — aggregate events, route by tier, handle escalation."""
    all_results: list[dict] = []

    # Build repo_paths lookup for directive execution
    repo_paths_lookup: dict[str, str] = {}
    for rp in roster_repos:
        repo_paths_lookup[rp.name] = str(rp)

    # Detect active interactive sessions for suppression
    active_sessions = repos_with_active_sessions(roster_repos)

    # Detect needs_human repos for tier2 snapshot enrichment
    needs_human = repos_needing_human(roster_repos)

    # 1. Aggregate new events from all repos
    since = float(state.last_event_ts) if state.last_event_ts else None
    events = aggregate_events(roster_repos, since=since)

    # Update last_event_ts
    if events:
        state.last_event_ts = str(events[-1].ts)

    # 2. Route events to tiers (skip tier 0 informational events)
    tier1_events: list[Event] = []
    tier2_events: list[Event] = []
    tier3_events: list[Event] = []

    for ev in events:
        tier = route_event(ev)
        if tier == 0:
            continue  # Informational, no brain invocation
        if tier == 1:
            tier1_events.append(ev)
        elif tier == 2:
            tier2_events.append(ev)
        else:
            tier3_events.append(ev)

    # 3. Process Tier 1 events (suppress repos with active sessions)
    tier1_reasoning_parts: list[str] = []
    for ev in tier1_events:
        # Suppress tier1 for repos with active interactive sessions
        if ev.repo in active_sessions:
            continue

        trigger = {"kind": ev.kind, "repo": ev.repo, "ts": ev.ts, "payload": ev.payload}
        response = invoke_brain(
            tier=1,
            trigger_event=trigger,
            recent_events=[{"kind": e.kind, "repo": e.repo, "ts": e.ts, "payload": e.payload} for e in events],
            snapshot=snapshot,
            heuristic_recommendation=heuristic_recommendation,
            recent_directives=state.recent_directives,
            log_dir=log_dir,
            dry_run=dry_run,
            gate_enabled=signal_gate_enabled,
            gate_dir=gate_dir,
            gate_dry_run=gate_dry_run,
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
    newly_stale_repos: set[str] = set()
    if run_heartbeat:
        state.last_heartbeat_check = now
        stale_repos = check_heartbeats(roster_repos)

        for repo_path in stale_repos:
            repo_name = repo_path.name

            # Signal gate: skip if already known stale from prior tick
            if signal_gate_enabled and repo_name in state.last_known_stale:
                continue

            # Signal gate: skip if already claimed by another agent
            signal_key = f"heartbeat.stale:{repo_name}"
            if signal_gate_enabled and is_signal_claimed(signal_key, pending_signals_path):
                continue

            newly_stale_repos.add(repo_name)

            # Wire heartbeat-stale finding as a drift task (suppressed for active sessions)
            if repo_name not in active_sessions:
                wg_dir = repo_path / ".workgraph"
                task_id = f"drift:{repo_name}:factory-brain:heartbeat-stale"
                description = (
                    f"Heartbeat stale for repo {repo_name}. "
                    f"Verify: check .workgraph/service/runtime/heartbeat is recent."
                )
                result_str = guarded_add_drift_task(
                    wg_dir=wg_dir,
                    task_id=task_id,
                    title=f"heartbeat-stale: {repo_name}",
                    description=description,
                    lane_tag="factory-brain",
                )
                record_finding_ledger(
                    wg_dir,
                    repo=repo_name,
                    lane="factory-brain",
                    finding_type="heartbeat-stale",
                    task_id=task_id,
                    result=result_str,
                )

            if not signal_gate_enabled:
                # Without signal gate, process as tier1 heartbeat events
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
                    dry_run=dry_run,
                    gate_enabled=signal_gate_enabled,
                    gate_dir=gate_dir,
                    gate_dry_run=gate_dry_run,
                )
                result = process_brain_response(
                    response,
                    tier=1,
                    state=state,
                    repo_paths=repo_paths_lookup,
                    dry_run=dry_run,
                )
                all_results.append(result)

        # Update known stale set
        stale_names = {rp.name for rp in stale_repos}
        state.last_known_stale = stale_names

    # 5. Determine whether to run tier 2
    has_tier2_signals = bool(tier2_events) or bool(newly_stale_repos)
    if signal_gate_enabled:
        run_tier2 = has_tier2_signals
    else:
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

        # Enrich snapshot with needs_human repos
        enriched_snapshot = dict(snapshot) if snapshot else {}
        if needs_human:
            enriched_snapshot["needs_human_repos"] = sorted(needs_human)

        response = invoke_brain(
            tier=2,
            trigger_event=tier2_trigger_events[0] if tier2_trigger_events else None,
            recent_events=recent_as_dicts,
            snapshot=enriched_snapshot if enriched_snapshot else None,
            heuristic_recommendation=heuristic_recommendation,
            recent_directives=state.recent_directives,
            roster=roster,
            tier1_reasoning=tier1_reasoning,
            log_dir=log_dir,
            dry_run=dry_run,
            gate_enabled=signal_gate_enabled,
            gate_dir=gate_dir,
            gate_dry_run=gate_dry_run,
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
            dry_run=dry_run,
            gate_enabled=signal_gate_enabled,
            gate_dir=gate_dir,
            gate_dry_run=gate_dry_run,
        )
        result = process_brain_response(
            response,
            tier=3,
            state=state,
            repo_paths=repo_paths_lookup,
            dry_run=dry_run,
        )
        all_results.append(result)

    # 7-day outcome checks — re-escalate unresolved fixes
    try:
        from driftdriver.paia_agent_health.fix_history import pending_checks, update_outcome, DEFAULT_PATH as DEFAULT_HISTORY_PATH
        from driftdriver.paia_agent_health.fixes import send_proposal
        from driftdriver.paia_agent_health.analyzer import Finding, FixProposal
        import asyncio
        for due_record in pending_checks(DEFAULT_HISTORY_PATH):
            from driftdriver.paia_agent_health.collector import collect_signals
            bundle = asyncio.run(collect_signals())
            agent_signals = bundle.agents.get(due_record.agent)
            still_failing = agent_signals and any(
                due_record.finding_pattern in str(e) or due_record.component in str(e)
                for e in (agent_signals.conversation_turns + agent_signals.tool_events)[:10]
            )
            if still_failing:
                finding = Finding(
                    agent=due_record.agent, pattern_type=due_record.finding_pattern,
                    evidence=[f"Previous fix did not resolve: {due_record.change_summary}"],
                    evidence_count=1,
                    affected_component=due_record.component, severity="high", confidence=1.0,
                )
                proposal = FixProposal(
                    finding=finding,
                    change_summary=f"Re-escalation: previous fix for {due_record.component} did not resolve the issue.",
                    diff=due_record.diff,
                    auto_apply=False, risk="medium",
                )
                send_proposal(proposal)
                update_outcome(DEFAULT_HISTORY_PATH, due_record.fix_id, "persists")
            else:
                update_outcome(DEFAULT_HISTORY_PATH, due_record.fix_id, "resolved")
    except Exception as exc:
        logger.warning("agent_health_outcome_check_failed: %s", exc)

    # Agent health lane — periodic 24h sweep + event-triggered fast path
    agent_health_triggered = any(
        e.kind in ("agent.task.failed",) for e in events
    )
    if should_run_agent_health(state) or agent_health_triggered:
        state.last_agent_health_check = now
        try:
            import asyncio
            from driftdriver.paia_agent_health.collector import collect_signals
            from driftdriver.paia_agent_health.analyzer import run_analysis
            from driftdriver.paia_agent_health.fixes import apply_fix, send_proposal
            from driftdriver.paia_agent_health.fix_history import is_duplicate_pending, DEFAULT_PATH as DEFAULT_HEALTH_PATH

            bundle = asyncio.run(collect_signals())
            proposals = run_analysis(bundle)

            for proposal in proposals:
                if is_duplicate_pending(
                    DEFAULT_HEALTH_PATH,
                    proposal.finding.agent,
                    proposal.finding.affected_component,
                    proposal.finding.pattern_type,
                ):
                    continue
                if proposal.auto_apply:
                    apply_fix(proposal)
                else:
                    send_proposal(proposal)
        except Exception as exc:
            logger.warning("agent_health_lane_failed: %s", exc)

    return all_results
