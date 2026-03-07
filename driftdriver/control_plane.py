# ABOUTME: Ecosystem control plane — dependency pressure scoring, chain analysis, priority suggestions.
# ABOUTME: Surfaces which repos should be worked on next based on cross-repo blocking relationships.
from __future__ import annotations

from typing import Any

from driftdriver.ecosystem_hub.models import RepoSnapshot


def _build_dependency_graph(repos: list[RepoSnapshot]) -> dict[str, list[tuple[str, int]]]:
    """Build adjacency list: for each repo, who depends on it (downstream).

    Returns {upstream_repo: [(downstream_repo, weight), ...]}.
    """
    graph: dict[str, list[tuple[str, int]]] = {repo.name: [] for repo in repos}
    for repo in repos:
        for dep in repo.cross_repo_dependencies:
            if not isinstance(dep, dict):
                continue
            upstream = str(dep.get("repo") or "").strip()
            if not upstream or upstream == repo.name:
                continue
            weight = max(1, int(dep.get("score") or 0))
            if upstream not in graph:
                graph[upstream] = []
            graph[upstream].append((repo.name, weight))
    return graph


def _repo_staleness_score(repo: RepoSnapshot) -> float:
    """Compute an internal staleness/blockage score for a single repo.

    Higher = more stalled/blocked, meaning more urgently in need of work.
    This is NOT the pressure score — it's a component of it.
    """
    score = 0.0

    if repo.stalled:
        score += 20.0

    # Blocked open tasks signal the repo can't make forward progress
    score += min(15.0, repo.blocked_open * 3.0)

    # Stale in-progress tasks signal work that's stuck
    stale_ip_count = len(repo.stale_in_progress) if repo.stale_in_progress else 0
    score += min(10.0, stale_ip_count * 2.5)

    # Open tasks with no in-progress work = idle capacity, mild signal
    open_count = int(repo.task_counts.get("open", 0)) + int(repo.task_counts.get("ready", 0))
    ip_count = int(repo.task_counts.get("in-progress", 0))
    if open_count > 0 and ip_count == 0:
        score += 5.0

    # Service not running when workgraph exists = potential neglect
    if repo.workgraph_exists and not repo.service_running:
        score += 4.0

    return score


def compute_repo_pressure(repos: list[RepoSnapshot]) -> dict[str, dict[str, Any]]:
    """Compute dependency pressure for every repo in the ecosystem.

    Pressure = how urgently a repo needs unblocking, considering:
      1. Its own staleness/blockage score
      2. How many downstream repos depend on it
      3. The combined dependency weight from downstream

    Returns {repo_name: {pressure, staleness, downstream_count, downstream_weight, reasons}}.
    """
    if not repos:
        return {}

    graph = _build_dependency_graph(repos)
    repo_map = {repo.name: repo for repo in repos}
    result: dict[str, dict[str, Any]] = {}

    for repo in repos:
        staleness = _repo_staleness_score(repo)
        downstream = graph.get(repo.name, [])
        downstream_count = len(downstream)
        downstream_weight = sum(w for _, w in downstream)

        # Pressure formula:
        #   base = staleness (how blocked/stalled the repo itself is)
        #   amplifier = downstream_count * downstream_weight_factor
        #   pressure = base * max(1, amplifier)
        #
        # A repo with zero staleness has zero pressure even with dependents.
        # A stalled repo with many dependents has multiplicatively higher pressure.
        if staleness > 0 and downstream_count > 0:
            amplifier = 1.0 + (downstream_count * 0.5) + (downstream_weight * 0.1)
            pressure = round(staleness * amplifier, 1)
        elif staleness > 0:
            # Stalled/blocked but nobody depends on it — lower urgency
            pressure = round(staleness * 0.5, 1)
        else:
            pressure = 0

        reasons: list[str] = []
        if repo.stalled:
            top = repo.stall_reasons[0] if repo.stall_reasons else "no active execution"
            reasons.append(f"stalled: {top}")
        if repo.blocked_open > 0:
            reasons.append(f"{repo.blocked_open} blocked open tasks")
        stale_ip = len(repo.stale_in_progress) if repo.stale_in_progress else 0
        if stale_ip > 0:
            reasons.append(f"{stale_ip} stale in-progress tasks")
        if repo.workgraph_exists and not repo.service_running:
            reasons.append("service not running")
        if downstream_count > 0:
            names = sorted(name for name, _ in downstream)
            reasons.append(f"blocks {downstream_count} repo(s): {', '.join(names[:5])}")

        result[repo.name] = {
            "pressure": pressure,
            "staleness": round(staleness, 1),
            "downstream_count": downstream_count,
            "downstream_weight": downstream_weight,
            "reasons": reasons,
        }

    return result


def dependency_chain(
    repo_name: str,
    repos: list[RepoSnapshot],
) -> dict[str, Any]:
    """Compute the full downstream dependency chain from a given repo.

    Returns {repo, downstream: [names], edges: [{from, to, weight}], depth}.
    Uses BFS to walk the graph and handle cycles.
    """
    graph = _build_dependency_graph(repos)
    known = {repo.name for repo in repos}

    if repo_name not in known:
        return {"repo": repo_name, "downstream": [], "edges": [], "depth": 0}

    visited: set[str] = set()
    edges: list[dict[str, Any]] = []
    queue: list[tuple[str, int]] = [(repo_name, 0)]
    max_depth = 0

    while queue:
        current, depth = queue.pop(0)
        for downstream_name, weight in graph.get(current, []):
            if downstream_name in visited:
                continue
            visited.add(downstream_name)
            edges.append({"from": current, "to": downstream_name, "weight": weight})
            next_depth = depth + 1
            if next_depth > max_depth:
                max_depth = next_depth
            queue.append((downstream_name, next_depth))

    downstream = sorted(visited)
    return {
        "repo": repo_name,
        "downstream": downstream,
        "edges": edges,
        "depth": max_depth,
    }


def _action_hint(repo: RepoSnapshot, downstream_count: int) -> str:
    """Generate a human-readable action hint for a priority suggestion."""
    parts: list[str] = []
    if repo.stalled:
        parts.append("unblock stalled execution")
    elif repo.blocked_open > 0:
        parts.append(f"resolve {repo.blocked_open} blocked task(s)")
    elif repo.workgraph_exists and not repo.service_running:
        parts.append("restart workgraph service")
    else:
        stale_ip = len(repo.stale_in_progress) if repo.stale_in_progress else 0
        if stale_ip > 0:
            parts.append(f"investigate {stale_ip} stale in-progress task(s)")
        else:
            parts.append("review open tasks")

    if downstream_count > 0:
        parts.append(f"unblocks {downstream_count} downstream repo(s)")

    return "; ".join(parts)


def suggest_priorities(
    repos: list[RepoSnapshot],
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Suggest which repos to work on next, ordered by dependency pressure.

    Only returns repos with pressure > 0 (i.e., repos that have issues AND
    matter to the ecosystem). Healthy repos are omitted.
    """
    pressure_map = compute_repo_pressure(repos)
    repo_map = {repo.name: repo for repo in repos}

    entries: list[dict[str, Any]] = []
    for name, data in pressure_map.items():
        pressure = data["pressure"]
        if pressure <= 0:
            continue
        repo = repo_map.get(name)
        if repo is None:
            continue
        entries.append({
            "repo": name,
            "pressure": pressure,
            "downstream_count": data["downstream_count"],
            "action": _action_hint(repo, data["downstream_count"]),
            "reasons": data["reasons"],
        })

    entries.sort(key=lambda e: (-e["pressure"], e["repo"]))
    return entries[:limit]


def build_pressure_payload(repos: list[RepoSnapshot]) -> dict[str, Any]:
    """Build the full /api/pressure response payload."""
    pressure_map = compute_repo_pressure(repos)
    suggestions = suggest_priorities(repos)

    max_pressure = 0.0
    repos_under_pressure = 0
    for data in pressure_map.values():
        p = data["pressure"]
        if p > max_pressure:
            max_pressure = p
        if p > 0:
            repos_under_pressure += 1

    return {
        "pressure_scores": pressure_map,
        "suggestions": suggestions,
        "summary": {
            "max_pressure": max_pressure,
            "repos_under_pressure": repos_under_pressure,
            "total_repos": len(repos),
        },
    }


def build_chain_payload(repo_name: str, repos: list[RepoSnapshot]) -> dict[str, Any]:
    """Build the /api/pressure/chain/<repo> response payload."""
    return dependency_chain(repo_name, repos)


def repos_from_snapshot(snapshot: dict[str, Any]) -> list[RepoSnapshot]:
    """Reconstruct minimal RepoSnapshot objects from a serialized ecosystem snapshot.

    The API stores snapshots as JSON dicts. This converts them back to
    RepoSnapshot instances with enough fields populated for pressure analysis.
    """
    raw_repos = snapshot.get("repos")
    if not isinstance(raw_repos, list):
        return []

    result: list[RepoSnapshot] = []
    for row in raw_repos:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        snap = RepoSnapshot(
            name=name,
            path=str(row.get("path") or ""),
            exists=bool(row.get("exists", True)),
            workgraph_exists=bool(row.get("workgraph_exists", False)),
            service_running=bool(row.get("service_running", False)),
            stalled=bool(row.get("stalled", False)),
            stall_reasons=list(row.get("stall_reasons") or []),
            blocked_open=int(row.get("blocked_open") or 0),
            in_progress=list(row.get("in_progress") or []),
            ready=list(row.get("ready") or []),
            task_counts=dict(row.get("task_counts") or {}),
            stale_in_progress=list(row.get("stale_in_progress") or []),
            cross_repo_dependencies=list(row.get("cross_repo_dependencies") or []),
        )
        result.append(snap)
    return result
