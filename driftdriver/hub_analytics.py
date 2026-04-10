# ABOUTME: Ecosystem-level aggregation and analytics extracted from ecosystem_hub/snapshot.py.
# ABOUTME: Pure computation functions: overview, narrative, secdrift/qadrift, dependency graph, attention scoring.
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from driftdriver.ecosystem_hub.models import NextWorkItem, RepoSnapshot


def repo_attention_entry(repo: RepoSnapshot) -> dict[str, Any] | None:
    """Compute an attention/risk entry for a single repo.

    Returns None if the repo has no risk signals.
    """
    reasons: list[str] = []
    score = 0
    if repo.errors:
        score += 18
        reasons.append(f"errors: {', '.join(repo.errors[:2])}")
    if repo.workgraph_exists and not repo.service_running:
        score += 10
        reasons.append("workgraph service stopped")
    if repo.stalled:
        score += 12
        top_reason = repo.stall_reasons[0] if repo.stall_reasons else "no active execution"
        reasons.append(f"stalled: {top_reason}")
    if repo.missing_dependencies > 0:
        score += min(20, repo.missing_dependencies * 4)
        reasons.append(f"missing dependencies: {repo.missing_dependencies}")
    if repo.blocked_open > 0:
        score += min(16, repo.blocked_open * 2)
        reasons.append(f"blocked open tasks: {repo.blocked_open}")
    if repo.stale_in_progress:
        score += min(18, len(repo.stale_in_progress) * 3)
        reasons.append(f"aging in-progress: {len(repo.stale_in_progress)}")
    if repo.stale_open:
        score += min(14, len(repo.stale_open) * 2)
        reasons.append(f"aging open: {len(repo.stale_open)}")
    if repo.behind > 0:
        score += min(8, repo.behind)
        reasons.append(f"behind upstream: {repo.behind}")
    if repo.git_dirty:
        score += 2
        reasons.append("dirty working tree")
    repo_ns = repo.repo_north_star if isinstance(repo.repo_north_star, dict) else {}
    if not bool(repo_ns.get("present")):
        score += 8
        reasons.append("repo north star missing")
    elif str(repo_ns.get("status") or "") == "weak":
        score += 3
        reasons.append("repo north star weak")
    sec = repo.security if isinstance(repo.security, dict) else {}
    sec_critical = max(0, int(sec.get("critical") or 0))
    sec_high = max(0, int(sec.get("high") or 0))
    sec_total = max(0, int(sec.get("findings_total") or 0))
    if sec_critical > 0:
        score += min(30, sec_critical * 12)
        reasons.append(f"security critical: {sec_critical}")
    if sec_high > 0:
        score += min(16, sec_high * 5)
        reasons.append(f"security high: {sec_high}")
    if sec_total > 0 and sec_critical <= 0 and sec_high <= 0:
        score += min(6, sec_total * 2)
        reasons.append(f"security findings: {sec_total}")
    qa = repo.quality if isinstance(repo.quality, dict) else {}
    qa_critical = max(0, int(qa.get("critical") or 0))
    qa_high = max(0, int(qa.get("high") or 0))
    qa_score = max(0, int(qa.get("quality_score") or 100))
    if qa_critical > 0:
        score += min(24, qa_critical * 10)
        reasons.append(f"quality critical: {qa_critical}")
    if qa_high > 0:
        score += min(12, qa_high * 4)
        reasons.append(f"quality high: {qa_high}")
    if qa_score < 80:
        score += min(10, max(1, (80 - qa_score) // 4))
        reasons.append(f"quality score: {qa_score}")
    if score <= 0:
        return None
    return {
        "repo": repo.name,
        "score": score,
        "reasons": reasons[:4],
        "narrative": repo.narrative,
    }


def build_repo_dependency_overview(repos: list[RepoSnapshot]) -> dict[str, Any]:
    """Build a cross-repo dependency graph with nodes, edges, and summary stats."""
    if not repos:
        return {
            "nodes": [],
            "edges": [],
            "summary": {
                "repo_count": 0,
                "edge_count": 0,
                "linked_repos": 0,
                "isolated_repos": 0,
                "top_outbound": [],
                "top_inbound": [],
            },
        }

    node_index: dict[str, dict[str, Any]] = {}
    for repo in repos:
        attention = repo_attention_entry(repo)
        attention_score = int(attention.get("score") or 0) if isinstance(attention, dict) else 0
        node_index[repo.name] = {
            "id": repo.name,
            "source": repo.source,
            "workgraph_exists": repo.workgraph_exists,
            "service_running": repo.service_running,
            "risk_score": attention_score,
            "outbound": 0,
            "inbound": 0,
            "outbound_weight": 0,
            "inbound_weight": 0,
        }

    edge_index: dict[tuple[str, str], dict[str, Any]] = {}
    known_repos = set(node_index.keys())
    for repo in repos:
        for dep in repo.cross_repo_dependencies:
            if not isinstance(dep, dict):
                continue
            target = str(dep.get("repo") or "").strip()
            if not target or target not in known_repos or target == repo.name:
                continue
            weight = max(1, int(dep.get("score") or 0))
            reasons_raw = dep.get("reasons")
            reasons = [str(item) for item in reasons_raw if str(item).strip()] if isinstance(reasons_raw, list) else []
            key = (repo.name, target)
            edge = edge_index.get(key)
            if edge is None:
                edge = {"source": repo.name, "target": target, "weight": 0, "reasons": []}
                edge_index[key] = edge
            edge["weight"] = min(24, int(edge.get("weight") or 0) + weight)
            existing_reasons = set(str(item) for item in edge.get("reasons") or [])
            merged = [item for item in (edge.get("reasons") or []) if str(item).strip()]
            for reason in reasons:
                if reason not in existing_reasons:
                    merged.append(reason)
                    existing_reasons.add(reason)
            edge["reasons"] = merged[:6]

    for edge in edge_index.values():
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        weight = int(edge.get("weight") or 0)
        if source in node_index:
            node_index[source]["outbound"] = int(node_index[source]["outbound"]) + 1
            node_index[source]["outbound_weight"] = int(node_index[source]["outbound_weight"]) + weight
        if target in node_index:
            node_index[target]["inbound"] = int(node_index[target]["inbound"]) + 1
            node_index[target]["inbound_weight"] = int(node_index[target]["inbound_weight"]) + weight

    nodes = [node_index[name] for name in sorted(node_index.keys())]
    edges = sorted(
        edge_index.values(),
        key=lambda row: (
            -int(row.get("weight") or 0),
            str(row.get("source") or ""),
            str(row.get("target") or ""),
        ),
    )

    linked = [row for row in nodes if int(row.get("outbound") or 0) > 0 or int(row.get("inbound") or 0) > 0]
    isolated = [row for row in nodes if row not in linked]
    top_outbound = sorted(
        nodes,
        key=lambda row: (
            -int(row.get("outbound_weight") or 0),
            -int(row.get("outbound") or 0),
            str(row.get("id") or ""),
        ),
    )[:3]
    top_inbound = sorted(
        nodes,
        key=lambda row: (
            -int(row.get("inbound_weight") or 0),
            -int(row.get("inbound") or 0),
            str(row.get("id") or ""),
        ),
    )[:3]

    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "repo_count": len(nodes),
            "edge_count": len(edges),
            "linked_repos": len(linked),
            "isolated_repos": len(isolated),
            "top_outbound": [
                {
                    "repo": str(row.get("id") or ""),
                    "weight": int(row.get("outbound_weight") or 0),
                    "count": int(row.get("outbound") or 0),
                }
                for row in top_outbound
            ],
            "top_inbound": [
                {
                    "repo": str(row.get("id") or ""),
                    "weight": int(row.get("inbound_weight") or 0),
                    "count": int(row.get("inbound") or 0),
                }
                for row in top_inbound
            ],
        },
    }


def build_ecosystem_overview(
    repos: list[RepoSnapshot],
    *,
    upstream_candidates: int,
    updates: dict[str, Any],
    central_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate stats across all repo snapshots into an ecosystem overview."""
    total_open = 0
    total_ready = 0
    total_in_progress = 0
    total_waiting = 0
    total_done = 0
    stale_open = 0
    stale_in_progress = 0
    blocked_open = 0
    missing_dependencies = 0
    repos_with_errors = 0
    repos_with_inactive_service = 0
    repos_stalled = 0
    repos_idle = 0
    repos_untracked = 0
    repos_dirty = 0
    repos_security_risk = 0
    repos_quality_risk = 0
    repos_with_north_star = 0
    repos_missing_north_star = 0
    repos_weak_north_star = 0
    security_critical = 0
    security_high = 0
    quality_critical = 0
    quality_high = 0
    total_ahead = 0
    total_behind = 0
    attention: list[dict[str, Any]] = []

    for repo in repos:
        total_open += int(repo.task_counts.get("open", 0))
        total_ready += int(repo.task_counts.get("ready", 0))
        total_in_progress += int(repo.task_counts.get("in-progress", 0))
        total_waiting += int(repo.task_counts.get("waiting", 0))
        total_done += int(repo.task_counts.get("done", 0))
        stale_open += len(repo.stale_open)
        stale_in_progress += len(repo.stale_in_progress)
        blocked_open += repo.blocked_open
        missing_dependencies += repo.missing_dependencies
        if repo.errors:
            repos_with_errors += 1
        if repo.workgraph_exists and not repo.service_running:
            repos_with_inactive_service += 1
        if repo.activity_state == "stalled":
            repos_stalled += 1
        elif repo.activity_state == "idle":
            repos_idle += 1
        elif repo.activity_state in ("untracked", "missing", "error"):
            repos_untracked += 1
        if repo.git_dirty:
            repos_dirty += 1
        repo_ns = repo.repo_north_star if isinstance(repo.repo_north_star, dict) else {}
        if bool(repo_ns.get("present")):
            repos_with_north_star += 1
            if str(repo_ns.get("status") or "") == "weak":
                repos_weak_north_star += 1
        else:
            repos_missing_north_star += 1
        sec = repo.security if isinstance(repo.security, dict) else {}
        qa = repo.quality if isinstance(repo.quality, dict) else {}
        if bool(sec.get("at_risk")):
            repos_security_risk += 1
        if bool(qa.get("at_risk")):
            repos_quality_risk += 1
        security_critical += int(sec.get("critical") or 0)
        security_high += int(sec.get("high") or 0)
        quality_critical += int(qa.get("critical") or 0)
        quality_high += int(qa.get("high") or 0)
        total_ahead += repo.ahead
        total_behind += repo.behind
        entry = repo_attention_entry(repo)
        if entry:
            attention.append(entry)

    attention.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("repo") or "")))
    return {
        "repos_total": len(repos),
        "repos_with_errors": repos_with_errors,
        "repos_with_inactive_service": repos_with_inactive_service,
        "repos_stalled": repos_stalled,
        "repos_idle": repos_idle,
        "repos_untracked": repos_untracked,
        "repos_dirty": repos_dirty,
        "repos_with_north_star": repos_with_north_star,
        "repos_missing_north_star": repos_missing_north_star,
        "repos_weak_north_star": repos_weak_north_star,
        "repos_security_risk": repos_security_risk,
        "repos_quality_risk": repos_quality_risk,
        "security_critical": security_critical,
        "security_high": security_high,
        "quality_critical": quality_critical,
        "quality_high": quality_high,
        "tasks_open": total_open,
        "tasks_ready": total_ready,
        "tasks_in_progress": total_in_progress,
        "tasks_waiting": total_waiting,
        "tasks_done": total_done,
        "stale_open": stale_open,
        "stale_in_progress": stale_in_progress,
        "blocked_open": blocked_open,
        "missing_dependencies": missing_dependencies,
        "upstream_candidates": upstream_candidates,
        "central_reports": len(central_reports),
        "total_ahead": total_ahead,
        "total_behind": total_behind,
        "update_has_updates": bool(updates.get("has_updates")),
        "update_has_discoveries": bool(updates.get("has_discoveries")),
        "attention_repos": attention[:12],
    }


def build_ecosystem_narrative(overview: dict[str, Any]) -> str:
    """Generate a human-readable ecosystem narrative from an overview dict."""
    repos_total = int(overview.get("repos_total") or 0)
    if repos_total <= 0:
        return "No repositories are currently visible to the ecosystem hub."

    blockers = int(overview.get("blocked_open") or 0) + int(overview.get("missing_dependencies") or 0)
    stale = int(overview.get("stale_open") or 0) + int(overview.get("stale_in_progress") or 0)
    service_gaps = int(overview.get("repos_with_inactive_service") or 0)
    stalled_repos = int(overview.get("repos_stalled") or 0)
    error_repos = int(overview.get("repos_with_errors") or 0)
    security_critical = int(overview.get("security_critical") or 0)
    security_high = int(overview.get("security_high") or 0)
    quality_critical = int(overview.get("quality_critical") or 0)
    quality_high = int(overview.get("quality_high") or 0)
    missing_north_star = int(overview.get("repos_missing_north_star") or 0)
    active = int(overview.get("tasks_in_progress") or 0)
    ready = int(overview.get("tasks_ready") or 0)

    if (
        error_repos > 0
        or service_gaps > 0
        or stalled_repos > 0
        or int(overview.get("missing_dependencies") or 0) > 0
        or missing_north_star > 0
        or security_critical > 0
        or quality_critical > 0
    ):
        tone = "Alert posture"
    elif stale > 0 or blockers > 0:
        tone = "Watch posture"
    else:
        tone = "Stable posture"

    headline = (
        f"{tone}: tracking {repos_total} repos with {active} active tasks and {ready} ready tasks."
    )
    pressure = (
        f"Pressure points: {blockers} dependency blockers, {stale} aging tasks, {stalled_repos} stalled repos, "
        f"{service_gaps} repos without a running workgraph service."
    )
    if missing_north_star > 0:
        pressure += f" {missing_north_star} repos still lack a canonical North Star."
    drift = (
        f"Security/quality signals: security critical={security_critical}, security high={security_high}, "
        f"quality critical={quality_critical}, quality high={quality_high}."
    )
    attention = overview.get("attention_repos") or []
    if isinstance(attention, list) and attention:
        top = attention[0] if isinstance(attention[0], dict) else {}
        repo = str(top.get("repo") or "unknown")
        reasons = top.get("reasons") or []
        reason_line = ", ".join(str(x) for x in reasons[:2]) if isinstance(reasons, list) else "high risk signals"
        focus = f"Top follow-up repo: {repo} ({reason_line})."
    else:
        focus = "No concentrated risk repo detected right now."
    return " ".join((headline, pressure, drift, focus))


def build_secdrift_overview(repos: list[RepoSnapshot]) -> dict[str, Any]:
    """Aggregate security drift findings across all repos."""
    rows: list[dict[str, Any]] = []
    critical = 0
    high = 0
    medium = 0
    low = 0
    at_risk = 0
    for repo in repos:
        sec = repo.security if isinstance(repo.security, dict) else {}
        row = {
            "repo": repo.name,
            "findings_total": int(sec.get("findings_total") or 0),
            "critical": int(sec.get("critical") or 0),
            "high": int(sec.get("high") or 0),
            "medium": int(sec.get("medium") or 0),
            "low": int(sec.get("low") or 0),
            "risk_score": int(sec.get("risk_score") or 0),
            "at_risk": bool(sec.get("at_risk")),
            "narrative": str(sec.get("narrative") or ""),
        }
        critical += row["critical"]
        high += row["high"]
        medium += row["medium"]
        low += row["low"]
        if row["at_risk"]:
            at_risk += 1
        if row["findings_total"] > 0:
            rows.append(row)
    rows.sort(
        key=lambda item: (
            -int(item.get("critical") or 0),
            -int(item.get("high") or 0),
            -int(item.get("risk_score") or 0),
            str(item.get("repo") or ""),
        )
    )
    return {
        "summary": {
            "repos_at_risk": at_risk,
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
            "findings_total": critical + high + medium + low,
        },
        "repos": rows[:24],
    }


def build_qadrift_overview(repos: list[RepoSnapshot]) -> dict[str, Any]:
    """Aggregate quality drift findings across all repos."""
    rows: list[dict[str, Any]] = []
    critical = 0
    high = 0
    medium = 0
    low = 0
    at_risk = 0
    for repo in repos:
        qa = repo.quality if isinstance(repo.quality, dict) else {}
        row = {
            "repo": repo.name,
            "findings_total": int(qa.get("findings_total") or 0),
            "critical": int(qa.get("critical") or 0),
            "high": int(qa.get("high") or 0),
            "medium": int(qa.get("medium") or 0),
            "low": int(qa.get("low") or 0),
            "quality_score": int(qa.get("quality_score") or 100),
            "at_risk": bool(qa.get("at_risk")),
            "narrative": str(qa.get("narrative") or ""),
        }
        critical += row["critical"]
        high += row["high"]
        medium += row["medium"]
        low += row["low"]
        if row["at_risk"]:
            at_risk += 1
        if row["findings_total"] > 0 or row["at_risk"] or row["quality_score"] < 90:
            rows.append(row)
    rows.sort(
        key=lambda item: (
            -int(item.get("critical") or 0),
            -int(item.get("high") or 0),
            int(item.get("quality_score") or 100),
            str(item.get("repo") or ""),
        )
    )
    return {
        "summary": {
            "repos_at_risk": at_risk,
            "critical": critical,
            "high": high,
            "medium": medium,
            "low": low,
            "findings_total": critical + high + medium + low,
        },
        "repos": rows[:24],
    }


def rank_next_work(repos: list[RepoSnapshot], *, limit: int = 20) -> list[dict[str, Any]]:
    """Rank and merge next-work items across all repos."""
    items: list[NextWorkItem] = []
    for repo in repos:
        items.extend(repo.top_next_work(limit=3))
    items.sort(key=lambda i: (-i.priority, i.repo, i.task_id))
    return [asdict(x) for x in items[:limit]]


def is_stale_decision(decision: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Return True when a pending decision has aged beyond the watch threshold."""
    created_at = str(decision.get("created_at") or "").strip()
    if not created_at:
        return False
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    current = now or datetime.now(timezone.utc)
    return (current - created).total_seconds() >= 72 * 3600


def count_autonomous_closures(notification_ledger: list[dict[str, Any]]) -> int:
    """Count outcomes that indicate the factory closed work without operator intervention."""
    total = 0
    for row in notification_ledger:
        status = str(row.get("delivery_status") or "").strip().lower()
        if status == "autonomous_closed":
            total += 1
    return total


def build_operator_domains(
    *,
    snapshot: dict[str, Any],
    decisions: list[dict[str, Any]],
    notification_ledger: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the high-level control, gate, autonomy, and convergence domains."""
    north_star = ((snapshot.get("northstardrift") or {}).get("summary") or {})
    control_errors = list((snapshot.get("control_plane") or {}).get("errors") or [])
    pending = [decision for decision in decisions if str(decision.get("status") or "pending") == "pending"]
    return {
        "control_plane": {
            "error_count": len(control_errors),
            "confidence": "high" if not control_errors else "medium",
        },
        "gate": {
            "pending_count": len(pending),
            "stale_count": sum(1 for decision in pending if is_stale_decision(decision)),
        },
        "autonomy": {
            "closed_without_operator": count_autonomous_closures(notification_ledger),
        },
        "convergence": {
            "score": float(north_star.get("overall_score") or 0.0),
            "trend": str(north_star.get("overall_trend") or "flat"),
        },
    }
