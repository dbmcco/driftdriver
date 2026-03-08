# ABOUTME: Attractor loop — orchestrates diagnose/plan/execute/re-diagnose convergence cycle.
# ABOUTME: Runs per-repo passes with circuit breakers, cross-repo sequencing deferred to caller.

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from driftdriver.bundles import Bundle, load_bundles_from_dir
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
) -> AttractorRun:
    """Run the full attractor loop for a single repo until convergence or circuit breaker."""
    if breakers is None:
        breakers = CircuitBreakers()

    run = AttractorRun(repo=repo, attractor=attractor.id)
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
