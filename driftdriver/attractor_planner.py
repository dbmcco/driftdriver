# ABOUTME: Attractor planner — selects and composes bundles to close the gap between current state and attractor.
# ABOUTME: Deterministic bundle matching with escalation for unmatched findings. Model call deferred to future.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from driftdriver.attractors import Attractor, evaluate_attractor, ACTIONABLE_SEVERITIES
from driftdriver.bundles import Bundle, BundleInstance, parameterize_bundle
from driftdriver.lane_contract import LaneFinding, LaneResult


@dataclass
class EscalationRecord:
    """A finding the planner cannot address with available bundles."""

    repo: str = ""
    attractor: str = ""
    reason: str = ""  # "no_matching_bundle", "low_confidence", "plateau", "budget_exhausted", "timeout"
    remaining_findings: list[dict[str, Any]] = field(default_factory=list)
    passes_completed: int = 0
    bundles_applied: list[str] = field(default_factory=list)
    bundle_outcomes: dict[str, str] = field(default_factory=dict)
    suggested_action: str = ""
    suggested_prompt: str = ""


@dataclass
class ConvergencePlan:
    """Output of the planner — what to do this pass."""

    attractor: str
    repo: str
    pass_number: int
    bundle_instances: list[BundleInstance] = field(default_factory=list)
    cross_bundle_edges: list[tuple[str, str]] = field(default_factory=list)
    budget_cost: int = 0
    escalations: list[EscalationRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def select_bundles_for_findings(
    findings: list[LaneFinding],
    bundles: list[Bundle],
    *,
    lane: str,
    task_id: str,
) -> list[BundleInstance]:
    """Match findings to bundles and return parameterized instances.

    Deduplicates: each bundle is instantiated at most once per lane+task.
    Skips info-severity findings.
    """
    # Build finding-kind to bundle lookup
    kind_to_bundle: dict[str, Bundle] = {}
    for b in bundles:
        for kind in b.finding_kinds:
            kind_to_bundle[kind] = b

    seen_bundle_ids: set[str] = set()
    instances: list[BundleInstance] = []

    for finding in findings:
        if finding.severity not in ACTIONABLE_SEVERITIES:
            continue

        matched_bundle: Bundle | None = None
        for tag in finding.tags:
            if tag in kind_to_bundle:
                matched_bundle = kind_to_bundle[tag]
                break

        if matched_bundle is None:
            continue

        if matched_bundle.id in seen_bundle_ids:
            continue
        seen_bundle_ids.add(matched_bundle.id)

        context = {
            "finding_id": f"{lane}-{matched_bundle.id}-{task_id}",
            "task_title": finding.message[:80],
            "evidence": finding.message,
            "file": finding.file,
            "repo_name": "",
        }
        instance = parameterize_bundle(matched_bundle, context)
        instances.append(instance)

    return instances


def build_convergence_plan(
    *,
    attractor: Attractor,
    lane_results: dict[str, LaneResult],
    bundles: list[Bundle],
    repo: str,
    pass_number: int,
    outcome_history: dict[str, float] | None = None,
) -> ConvergencePlan:
    """Build a convergence plan for one pass.

    Selects bundles for each lane's findings, escalates unmatched findings.
    """
    all_instances: list[BundleInstance] = []
    escalations: list[EscalationRecord] = []
    budget_cost = 0

    gap = evaluate_attractor(attractor, lane_results)
    if gap.converged:
        return ConvergencePlan(
            attractor=attractor.id,
            repo=repo,
            pass_number=pass_number,
        )

    for lane_name, result in lane_results.items():
        actionable = [f for f in result.findings if f.severity in ACTIONABLE_SEVERITIES]
        if not actionable:
            continue

        instances = select_bundles_for_findings(
            actionable, bundles, lane=lane_name, task_id=f"pass{pass_number}",
        )
        all_instances.extend(instances)

        # Find unmatched findings
        matched_tags: set[str] = set()
        for inst in instances:
            bundle = next((b for b in bundles if b.id == inst.bundle_id), None)
            if bundle:
                matched_tags.update(bundle.finding_kinds)

        for finding in actionable:
            if not any(tag in matched_tags for tag in finding.tags):
                escalations.append(EscalationRecord(
                    repo=repo,
                    attractor=attractor.id,
                    reason="no_matching_bundle",
                    remaining_findings=[{"message": finding.message, "severity": finding.severity}],
                    suggested_action=f"Create a bundle for finding kind: {finding.tags}",
                ))

    for inst in all_instances:
        budget_cost += len(inst.tasks)

    return ConvergencePlan(
        attractor=attractor.id,
        repo=repo,
        pass_number=pass_number,
        bundle_instances=all_instances,
        budget_cost=budget_cost,
        escalations=escalations,
    )
