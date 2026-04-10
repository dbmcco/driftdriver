"""Operator-first landing payloads for the ecosystem hub."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from driftdriver.hub_analytics import build_operator_domains, is_stale_decision


@dataclass(frozen=True)
class OperatorItem:
    bucket: str
    kind: str
    title: str
    repo: str
    urgency: int
    confidence: float
    rationale: str
    evidence: dict[str, Any]
    full_view: dict[str, Any]
    decision_id: str | None = None


def build_factory_scorecard(domains: dict[str, Any]) -> dict[str, Any]:
    """Compute the top-line Dark Factory status card."""
    control_errors = int((domains.get("control_plane") or {}).get("error_count") or 0)
    pending_decisions = int((domains.get("gate") or {}).get("pending_count") or 0)
    convergence = str((domains.get("convergence") or {}).get("trend") or "flat")
    status = "green"
    why = "control plane healthy and convergence stable"
    if control_errors > 0:
        status = "red"
        why = "control-plane failures are blocking reliable factory operation"
    elif pending_decisions > 10 or convergence == "regressing":
        status = "yellow"
        why = "Gate load or convergence trend is above healthy operating range"
    return {
        "status": status,
        "why": why,
        "needs_you": pending_decisions,
        "autonomous_this_week": int((domains.get("autonomy") or {}).get("closed_without_operator") or 0),
        "convergence_trend": convergence,
        "confidence": str((domains.get("control_plane") or {}).get("confidence") or "low"),
    }


def rank_operator_items(
    *,
    snapshot: dict[str, Any],
    decisions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Bucket and rank operator items for the home view."""
    items: list[OperatorItem] = []
    control_errors = list((snapshot.get("control_plane") or {}).get("errors") or [])
    if control_errors:
        first_error = str(control_errors[0])
        items.append(
            OperatorItem(
                bucket="now",
                kind="control_plane",
                title="Control plane needs attention",
                repo="driftdriver",
                urgency=100,
                confidence=0.95,
                rationale=first_error,
                evidence={"errors": control_errors},
                full_view={"tab": "operations", "focus": "control-plane"},
            )
        )
    for decision in decisions:
        if str(decision.get("status") or "pending") != "pending":
            continue
        context = decision.get("context") or {}
        severity = str(context.get("severity") or "medium")
        confidence = float(context.get("confidence") or 0.0)
        bucket = "watch" if severity == "low" or confidence < 0.5 or is_stale_decision(decision) else "decide"
        full_view = context.get("full_view")
        if not isinstance(full_view, dict):
            full_view = {"tab": "factory", "focus": f"decision:{decision.get('id', '')}"}
        urgency = 90 if severity == "high" else 70 if severity == "medium" else 40
        items.append(
            OperatorItem(
                bucket=bucket,
                kind="decision",
                title=str(decision.get("question") or "Decision needed"),
                repo=str(decision.get("repo") or ""),
                urgency=urgency,
                confidence=confidence,
                rationale=f"{severity} confidence={confidence:.2f}",
                evidence={"decision": decision},
                full_view=full_view,
                decision_id=str(decision.get("id") or ""),
            )
        )
    ranked = sorted(items, key=lambda item: (-item.urgency, -item.confidence, item.repo, item.title))
    return [
        {
            "bucket": item.bucket,
            "kind": item.kind,
            "title": item.title,
            "repo": item.repo,
            "urgency": item.urgency,
            "confidence": item.confidence,
            "rationale": item.rationale,
            "evidence": item.evidence,
            "full_view": item.full_view,
            "decision_id": item.decision_id,
        }
        for item in ranked
    ]


def build_operator_home(
    *,
    snapshot: dict[str, Any],
    decisions: list[dict[str, Any]],
    notification_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the operator-home payload from canonical snapshot and decision objects."""
    domains = build_operator_domains(
        snapshot=snapshot,
        decisions=decisions,
        notification_ledger=notification_ledger,
    )
    items = rank_operator_items(snapshot=snapshot, decisions=decisions)
    return {
        "scorecard": build_factory_scorecard(domains),
        "domains": domains,
        "now": [item for item in items if item["bucket"] == "now"],
        "decide": [item for item in items if item["bucket"] == "decide"],
        "watch": [item for item in items if item["bucket"] == "watch"],
        "counts": {
            "now": sum(1 for item in items if item["bucket"] == "now"),
            "decide": sum(1 for item in items if item["bucket"] == "decide"),
            "watch": sum(1 for item in items if item["bucket"] == "watch"),
        },
    }
