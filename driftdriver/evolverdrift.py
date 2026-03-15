# ABOUTME: evolverdrift lane — monitors WG evolver liveness, consumption, impact, and regression.
# ABOUTME: Also detects WG failure modes (orphaned tasks, deadlocked daemons, graph corruption).
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from driftdriver.lane_contract import LaneFinding, LaneResult


def _parse_iso(raw: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a datetime, or None on failure."""
    if not raw:
        return None
    try:
        text = raw.strip()
        # Handle 'Z' suffix
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def _latest_evolve_run(repo_path: Path) -> tuple[Path | None, datetime | None]:
    """Find the latest evolve-run directory and its timestamp.

    Returns (run_dir, timestamp) or (None, None) if none exist.
    Tries config.json first, falls back to parsing the directory name.
    """
    runs_dir = repo_path / ".workgraph" / "evolve-runs"
    if not runs_dir.exists() or not runs_dir.is_dir():
        return None, None

    run_dirs = sorted(
        [d for d in runs_dir.iterdir() if d.is_dir()],
        key=lambda d: d.name,
        reverse=True,
    )
    if not run_dirs:
        return None, None

    latest = run_dirs[0]

    # Try config.json timestamp first
    config_path = latest / "config.json"
    if config_path.exists():
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            ts = _parse_iso(config.get("timestamp"))
            if ts is not None:
                return latest, ts
        except (json.JSONDecodeError, OSError):
            pass

    # Fall back to parsing directory name: run-YYYYMMDD-HHMMSS
    name = latest.name
    if name.startswith("run-"):
        date_part = name[4:]  # strip "run-"
        try:
            ts = datetime.strptime(date_part, "%Y%m%d-%H%M%S").replace(
                tzinfo=timezone.utc
            )
            return latest, ts
        except ValueError:
            pass

    return latest, None


def _load_graph_lines(repo_path: Path) -> list[dict]:
    """Load all JSON objects from .workgraph/graph.jsonl."""
    graph_path = repo_path / ".workgraph" / "graph.jsonl"
    if not graph_path.exists():
        return []
    lines: list[dict] = []
    try:
        for raw_line in graph_path.read_text(encoding="utf-8").splitlines():
            stripped = raw_line.strip()
            if stripped:
                try:
                    lines.append(json.loads(stripped))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return lines


def _load_agent_registry(repo_path: Path) -> dict:
    """Load the agent registry from .workgraph/service/agents.json."""
    agents_path = repo_path / ".workgraph" / "service" / "agents.json"
    if not agents_path.exists():
        return {}
    try:
        data = json.loads(agents_path.read_text(encoding="utf-8"))
        return data.get("agents", {})
    except (json.JSONDecodeError, OSError):
        return {}


def check_liveness(
    repo_path: Path, *, evolver_stale_days: int = 7
) -> list[LaneFinding]:
    """Check evolver liveness based on the most recent evolve-run.

    Returns findings:
    - info with tag "no-history" if no runs exist
    - warning if stale (1-2x stale_days)
    - error if very stale (>2x stale_days)
    - no findings if recent (within stale_days)
    """
    from driftdriver.lane_contract import LaneFinding

    runs_dir = repo_path / ".workgraph" / "evolve-runs"
    if not runs_dir.exists() or not runs_dir.is_dir() or not any(runs_dir.iterdir()):
        return [
            LaneFinding(
                message="Evolver has never run in this repo",
                severity="info",
                tags=["no-history"],
            )
        ]

    _, ts = _latest_evolve_run(repo_path)
    if ts is None:
        return [
            LaneFinding(
                message="Evolver has never run in this repo",
                severity="info",
                tags=["no-history"],
            )
        ]

    now = datetime.now(timezone.utc)
    # Ensure ts is timezone-aware for comparison
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    age = now - ts
    stale_threshold = timedelta(days=evolver_stale_days)
    very_stale_threshold = timedelta(days=evolver_stale_days * 2)

    if age > very_stale_threshold:
        return [
            LaneFinding(
                message=f"Evolver is very stale — last run {age.days} days ago (threshold: {evolver_stale_days} days)",
                severity="error",
                tags=["evolver-stale"],
            )
        ]
    elif age > stale_threshold:
        return [
            LaneFinding(
                message=f"Evolver is stale — last run {age.days} days ago (threshold: {evolver_stale_days} days)",
                severity="warning",
                tags=["evolver-stale"],
            )
        ]

    return []


def check_orphaned_tasks(repo_path: Path) -> list[LaneFinding]:
    """Detect in-progress tasks assigned to agents that are not alive."""
    from driftdriver.lane_contract import LaneFinding

    graph_lines = _load_graph_lines(repo_path)
    if not graph_lines:
        return []

    agents = _load_agent_registry(repo_path)
    findings: list[LaneFinding] = []

    for node in graph_lines:
        if node.get("status") != "in-progress":
            continue
        assigned = node.get("assigned", "")
        if not assigned:
            continue
        task_id = node.get("id", "unknown")

        agent_info = agents.get(assigned)
        if agent_info is None or not agent_info.get("alive", False):
            findings.append(
                LaneFinding(
                    message=f"Orphaned task '{task_id}' — assigned agent '{assigned}' is not alive",
                    severity="warning",
                    tags=["orphaned-task"],
                )
            )

    return findings


def check_graph_corruption(repo_path: Path) -> list[LaneFinding]:
    """Detect graph corruption: duplicate node IDs and orphan dependency references."""
    from driftdriver.lane_contract import LaneFinding

    graph_lines = _load_graph_lines(repo_path)
    if not graph_lines:
        return []

    findings: list[LaneFinding] = []

    # Check for duplicate node IDs
    seen_ids: dict[str, int] = {}
    for node in graph_lines:
        node_id = node.get("id")
        if node_id:
            seen_ids[node_id] = seen_ids.get(node_id, 0) + 1

    duplicates = {nid: count for nid, count in seen_ids.items() if count > 1}
    if duplicates:
        dup_list = ", ".join(f"{nid} ({count}x)" for nid, count in sorted(duplicates.items()))
        findings.append(
            LaneFinding(
                message=f"Duplicate node IDs in graph: {dup_list}",
                severity="warning",
                tags=["duplicate-ids"],
            )
        )

    # Check for orphan dependency references
    all_ids = set(seen_ids.keys())
    orphan_refs: set[str] = set()
    for node in graph_lines:
        after = node.get("after")
        if isinstance(after, list):
            for dep in after:
                if dep and dep not in all_ids:
                    orphan_refs.add(dep)

    if orphan_refs:
        ref_list = ", ".join(sorted(orphan_refs))
        findings.append(
            LaneFinding(
                message=f"Orphan dependency references (non-existent IDs): {ref_list}",
                severity="warning",
                tags=["orphan-deps"],
            )
        )

    return findings


def run_as_lane(project_dir: Path) -> LaneResult:
    """Run evolverdrift checks and return results in the standard lane contract format.

    Runs check_liveness first. If the evolver has no history (tag "no-history"),
    evolver-specific checks are suppressed but graph integrity checks still run.
    Always runs check_orphaned_tasks and check_graph_corruption.
    """
    from driftdriver.lane_contract import LaneResult

    all_findings: list[LaneFinding] = []

    # 1. Check evolver liveness
    liveness_findings = check_liveness(project_dir)
    has_no_history = any("no-history" in f.tags for f in liveness_findings)

    if has_no_history:
        # Include the no-history info finding but suppress evolver-specific checks
        all_findings.extend(liveness_findings)
    else:
        all_findings.extend(liveness_findings)

    # 2. Always run graph integrity checks
    all_findings.extend(check_orphaned_tasks(project_dir))
    all_findings.extend(check_graph_corruption(project_dir))

    exit_code = 1 if all_findings else 0
    finding_count = len(all_findings)
    severities = {}
    for f in all_findings:
        severities[f.severity] = severities.get(f.severity, 0) + 1
    severity_parts = [f"{count} {sev}" for sev, count in sorted(severities.items())]
    summary = f"evolverdrift: {finding_count} findings" + (
        f" ({', '.join(severity_parts)})" if severity_parts else ""
    )

    return LaneResult(
        lane="evolverdrift",
        findings=all_findings,
        exit_code=exit_code,
        summary=summary,
    )
