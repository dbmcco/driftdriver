# ABOUTME: Attractor loop — orchestrates diagnose/plan/execute/re-diagnose convergence cycle.
# ABOUTME: Runs per-repo passes with circuit breakers, cross-repo sequencing deferred to caller.

from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from driftdriver.attractor_planner import (
    ConvergencePlan,
    EscalationRecord,
    build_convergence_plan,
)
from driftdriver.attractors import (
    ACTIONABLE_SEVERITIES,
    Attractor,
    AttractorGap,
    evaluate_attractor,
    load_attractors_from_dir,
    resolve_attractor,
)
from driftdriver.actor import Actor
from driftdriver.bundles import Bundle, load_bundles_from_dir
from driftdriver.drift_task_guard import guarded_add_drift_task, record_finding_ledger
from driftdriver.lane_contract import LaneResult


@dataclass
class CircuitBreakers:
    """Limits for the attractor loop."""

    max_passes: int = 3
    max_tasks_per_cycle: int = 30
    max_dispatches_per_cycle: int = 10
    plateau_threshold: int = 2  # consecutive passes with no improvement
    pass_timeout_seconds: int = 1800


@dataclass
class PassResult:
    """Result of a single pass through the loop."""

    pass_number: int
    findings_before: int
    findings_after: int
    duration_seconds: float
    bundles_applied: list[str] = field(default_factory=list)
    bundle_outcomes: dict[str, str] = field(default_factory=dict)
    plan: ConvergencePlan | None = None


@dataclass
class AttractorRun:
    """Full record of an attractor loop execution for one repo."""

    repo: str
    attractor: str
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    passes: list[PassResult] = field(default_factory=list)
    status: str = "pending"  # pending, converged, plateau, escalated, budget_exhausted, max_passes, timeout
    remaining_findings: list[dict[str, Any]] = field(default_factory=list)
    escalations: list[EscalationRecord] = field(default_factory=list)


_GATE_STATE_FILE = ".workgraph/attractor-gate-state.json"


def compute_gate_hash(lane_results: dict[str, Any]) -> str:
    """Compute a stable SHA-256 hash of drift findings for gate comparison."""
    serializable = []
    for lane_id in sorted(lane_results):
        result = lane_results[lane_id]
        for f in sorted(result.findings, key=lambda x: (x.file, x.line, x.severity, x.message)):
            serializable.append({
                "lane": lane_id,
                "severity": f.severity,
                "message": f.message,
                "file": f.file,
                "line": f.line,
            })
    return hashlib.sha256(json.dumps(serializable, sort_keys=True).encode()).hexdigest()


def load_gate_state(repo_path: Path) -> dict[str, str]:
    """Load persisted gate state from .workgraph/attractor-gate-state.json."""
    gate_file = repo_path / _GATE_STATE_FILE
    if gate_file.exists():
        return json.loads(gate_file.read_text(encoding="utf-8"))
    return {}


def save_gate_state(repo_path: Path, state: dict[str, str]) -> None:
    """Persist gate state to .workgraph/attractor-gate-state.json."""
    gate_file = repo_path / _GATE_STATE_FILE
    gate_file.parent.mkdir(parents=True, exist_ok=True)
    gate_file.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _write_gate_log(
    wg_dir: Path,
    *,
    agent: str,
    skipped: bool,
    reason: str = "",
    gate_key: str = "",
    fired: bool | None = None,
) -> None:
    """Append a gate decision entry to gate-log.jsonl for factory_report visibility."""
    entry: dict[str, Any] = {
        "ts": time.time(),
        "agent": agent,
        "skipped": skipped,
        "fired": not skipped if fired is None else fired,
        "gate_key": gate_key,
    }
    if reason:
        entry["reason"] = reason
    gate_log = wg_dir / "gate-log.jsonl"
    try:
        gate_log.parent.mkdir(parents=True, exist_ok=True)
        with open(gate_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _check_attractor_canary(
    gate_state: dict[str, Any],
    *,
    alert_hours: int = 4,
) -> None:
    """Alert via wg notify if no evidence has been detected in alert_hours.

    A gate that never sees evidence is indistinguishable from a broken gate.
    """
    last_evidence = gate_state.get("last_evidence_at")
    if not last_evidence:
        return
    try:
        dt = datetime.fromisoformat(str(last_evidence))
    except (ValueError, TypeError):
        return
    now = datetime.now(timezone.utc)
    if (now - dt).total_seconds() > alert_hours * 3600:
        try:
            subprocess.run(
                ["wg", "notify", f"attractor-gate: no evidence detected in {alert_hours}h — gate may be stuck or findings are truly static"],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass


def check_convergence(passes: list[PassResult], breakers: CircuitBreakers) -> str:
    """Determine loop status from pass history.

    Returns: 'converged', 'plateau', 'max_passes', or 'continue'.
    """
    if not passes:
        return "continue"

    last = passes[-1]

    # Converged: no actionable findings remain
    if last.findings_after == 0:
        return "converged"

    # Plateau: N consecutive passes with no improvement (check before max_passes)
    if len(passes) >= breakers.plateau_threshold:
        recent = passes[-breakers.plateau_threshold:]
        counts = [p.findings_after for p in recent]
        if all(c >= counts[0] for c in counts):
            return "plateau"

    # Max passes exceeded
    if len(passes) >= breakers.max_passes:
        return "max_passes"

    return "continue"


def run_attractor_pass(
    *,
    repo: str,
    repo_path: Path,
    attractor: Attractor,
    bundles: list[Bundle],
    pass_number: int,
    diagnose_fn: Any,  # callable(repo_path) -> dict[str, LaneResult]
    execute_fn: Any,  # callable(plan, repo_path) -> dict[str, str]  (bundle_id -> outcome)
) -> PassResult:
    """Execute a single attractor pass: diagnose -> plan -> execute -> re-diagnose."""
    start = time.monotonic()

    # Diagnose
    lane_results = diagnose_fn(repo_path)
    findings_before = sum(
        1 for r in lane_results.values()
        for f in r.findings if f.severity in ACTIONABLE_SEVERITIES
    )

    # Plan
    plan = build_convergence_plan(
        attractor=attractor,
        lane_results=lane_results,
        bundles=bundles,
        repo=repo,
        pass_number=pass_number,
    )

    # Execute (if there's anything to do)
    bundle_outcomes: dict[str, str] = {}
    bundles_applied: list[str] = []
    if plan.bundle_instances:
        bundle_outcomes = execute_fn(plan, repo_path)
        bundles_applied = [inst.bundle_id for inst in plan.bundle_instances]

    # Re-diagnose
    lane_results_after = diagnose_fn(repo_path)
    findings_after = sum(
        1 for r in lane_results_after.values()
        for f in r.findings if f.severity in ACTIONABLE_SEVERITIES
    )

    elapsed = time.monotonic() - start

    return PassResult(
        pass_number=pass_number,
        findings_before=findings_before,
        findings_after=findings_after,
        duration_seconds=elapsed,
        bundles_applied=bundles_applied,
        bundle_outcomes=bundle_outcomes,
        plan=plan,
    )


def run_attractor_loop(
    *,
    repo: str,
    repo_path: Path,
    attractor: Attractor,
    bundles: list[Bundle],
    breakers: CircuitBreakers | None = None,
    diagnose_fn: Any,
    execute_fn: Any,
    signal_gate_enabled: bool = False,
) -> AttractorRun:
    """Run the full attractor loop for a single repo until convergence or circuit breaker."""
    if breakers is None:
        breakers = CircuitBreakers()

    run = AttractorRun(repo=repo, attractor=attractor.id)

    # Pre-call signal gate: skip loop if findings unchanged since last run.
    gate_key = f"{repo}::{attractor.id}"
    wg_dir = repo_path / ".workgraph"
    if signal_gate_enabled:
        current_findings = diagnose_fn(repo_path)
        current_hash = compute_gate_hash(current_findings)
        gate_state = load_gate_state(repo_path)
        stored = gate_state.get(gate_key)
        # Support both old format (plain hash string) and new format (dict).
        stored_hash = stored["hash"] if isinstance(stored, dict) else stored
        if stored_hash == current_hash:
            log.info("[attractor-gate] %s — no new findings, skipping LLM pass (hash=%s)", gate_key, current_hash[:12])
            _write_gate_log(wg_dir, agent="attractor", skipped=True, reason="no_signal", gate_key=gate_key)
            if isinstance(stored, dict):
                _check_attractor_canary(stored)
                stored["last_checked_at"] = datetime.now(timezone.utc).isoformat()
                save_gate_state(repo_path, gate_state)
            run.status = "signal_gated"
            return run
        log.info("[attractor-gate] %s — findings changed, proceeding (hash=%s)", gate_key, current_hash[:12])
        _write_gate_log(wg_dir, agent="attractor", skipped=False, reason="evidence_changed", gate_key=gate_key)

    tasks_emitted = 0

    for pass_number in range(breakers.max_passes):
        result = run_attractor_pass(
            repo=repo,
            repo_path=repo_path,
            attractor=attractor,
            bundles=bundles,
            pass_number=pass_number,
            diagnose_fn=diagnose_fn,
            execute_fn=execute_fn,
        )
        run.passes.append(result)

        # Track budget
        if result.plan:
            tasks_emitted += result.plan.budget_cost
            run.escalations.extend(result.plan.escalations)

        if tasks_emitted >= breakers.max_tasks_per_cycle:
            run.status = "budget_exhausted"
            break

        status = check_convergence(run.passes, breakers)
        if status == "converged":
            run.status = "converged"
            break
        elif status == "plateau":
            run.status = "plateau"
            break
        elif status == "max_passes":
            run.status = "max_passes"
            break
        # else: continue

    if run.status == "pending":
        run.status = "max_passes"

    # Record remaining findings from last pass
    if run.passes and run.passes[-1].findings_after > 0:
        last_pass = run.passes[-1]
        if last_pass.plan:
            for esc in last_pass.plan.escalations:
                run.remaining_findings.extend(esc.remaining_findings)

    # Wire remaining escalations to drift tasks (report → workgraph).
    if wg_dir.exists() and run.escalations:
        actor = Actor(id="daemon-attractor-loop", actor_class="daemon", name="attractor-loop", repo=repo)
        seen_task_ids: set[str] = set()
        for esc in run.escalations:
            reason_slug = re.sub(r"[^a-z0-9]+", "-", esc.reason.lower()).strip("-") or "unmatched"
            task_id = f"drift:{repo}:attractor:{reason_slug}"
            if task_id in seen_task_ids:
                continue
            seen_task_ids.add(task_id)
            title = f"attractor: {esc.reason.replace('_', ' ')} in {repo}"
            verify_cmd = f"wg analyze --repo {repo} | grep attractor_status"
            findings_summary = "; ".join(
                f.get("message", "")[:80] for f in esc.remaining_findings[:3]
            )
            desc = (
                f"Attractor loop could not resolve findings in {repo}.\n\n"
                f"Attractor: {esc.attractor}\n"
                f"Reason: {esc.reason}\n"
                f"Suggested action: {esc.suggested_action}\n"
                f"Sample findings: {findings_summary}\n\n"
                f"Verify: {verify_cmd}\n"
            )
            result = guarded_add_drift_task(
                wg_dir=wg_dir,
                task_id=task_id,
                title=title,
                description=desc,
                lane_tag="attractor",
                actor=actor,
                cwd=repo_path,
            )
            record_finding_ledger(
                wg_dir,
                repo=repo,
                lane="attractor",
                finding_type=reason_slug,
                task_id=task_id,
                result=result,
                severity="warning",
                message=esc.reason,
            )

    # Persist gate hash after a real run so next call can compare.
    if signal_gate_enabled and run.passes:
        final_findings = diagnose_fn(repo_path)
        new_hash = compute_gate_hash(final_findings)
        gate_state = load_gate_state(repo_path)
        now_iso = datetime.now(timezone.utc).isoformat()
        gate_state[gate_key] = {
            "hash": new_hash,
            "last_evidence_at": now_iso,
            "last_checked_at": now_iso,
        }
        save_gate_state(repo_path, gate_state)
        log.info("[attractor-gate] %s — saved new hash after run (hash=%s)", gate_key, new_hash[:12])

    return run


def save_attractor_run(run: AttractorRun, service_dir: Path) -> None:
    """Persist an attractor run to the service directory."""
    attractor_dir = service_dir / "attractor"
    attractor_dir.mkdir(parents=True, exist_ok=True)

    # Current run
    current = attractor_dir / "current-run.json"
    current.write_text(json.dumps(_run_to_dict(run), indent=2), encoding="utf-8")

    # History
    history_dir = attractor_dir / "history"
    history_dir.mkdir(exist_ok=True)
    ts = run.started_at.replace(":", "-").replace("+", "")
    history_file = history_dir / f"{ts}.json"
    history_file.write_text(json.dumps(_run_to_dict(run), indent=2), encoding="utf-8")


def _run_to_dict(run: AttractorRun) -> dict[str, Any]:
    """Serialize an AttractorRun for JSON persistence."""
    return {
        "repo": run.repo,
        "attractor": run.attractor,
        "started_at": run.started_at,
        "status": run.status,
        "passes": [
            {
                "pass_number": p.pass_number,
                "findings_before": p.findings_before,
                "findings_after": p.findings_after,
                "duration_seconds": p.duration_seconds,
                "bundles_applied": p.bundles_applied,
                "bundle_outcomes": p.bundle_outcomes,
            }
            for p in run.passes
        ],
        "remaining_findings": run.remaining_findings,
        "escalation_count": len(run.escalations),
    }
