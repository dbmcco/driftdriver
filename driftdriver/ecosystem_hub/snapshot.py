# ABOUTME: Snapshot collection and aggregation for individual repos and the full ecosystem.
# ABOUTME: Builds overviews, narratives, dependency graphs, security/quality summaries, north-star drift.
from __future__ import annotations

import json
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from driftdriver.northstardrift import (
    apply_northstardrift,
    emit_northstar_review_tasks,
    load_previous_northstardrift,
    read_northstardrift_history,
    write_northstardrift_artifacts,
)
from driftdriver.policy import load_drift_policy
from driftdriver.qadrift import run_program_quality_scan
from driftdriver.secdrift import run_secdrift_scan
from driftdriver.directives import DirectiveLog
from driftdriver.workgraph import load_workgraph

from .discovery import (
    _age_days,
    _collect_central_reports_summary,
    _collect_cross_repo_dependencies,
    _collect_repo_north_star,
    _compute_ready_tasks,
    _default_update_checker,
    _discover_active_workspace_repos,
    _git_default_ref,
    _iso_now,
    _load_ecosystem_repos,
    _normalize_dependencies,
    _path_age_seconds,
    _process_alive,
    _read_json,
    _safe_ts_for_file,
    _service_port_alive,
    _write_json,
    generate_upstream_candidates,
    resolve_central_repo_path,
    write_central_register,
)
from .models import (
    RepoSnapshot,
    UpstreamCandidate,
)

_STALE_OPEN_DAYS = 14.0
_STALE_IN_PROGRESS_DAYS = 3.0
_MAX_TASK_GRAPH_NODES = 140
_SUPERVISOR_DEFAULT_COOLDOWN_SECONDS = 180
_SUPERVISOR_DEFAULT_MAX_STARTS = 4
_SUPERVISOR_LAST_ATTEMPT: dict[str, float] = {}


def _run_cmd(cmd: list[str], **kwargs: object) -> tuple[int, str, str]:
    """Resolve ``_run`` through the package namespace so unittest.mock.patch works."""
    import driftdriver.ecosystem_hub as _hub

    return _hub._run(cmd, **kwargs)  # type: ignore[arg-type]


def _task_status_rank(status: str) -> int:
    norm = str(status or "").strip().lower()
    if norm == "in-progress":
        return 0
    if norm in ("open", "ready"):
        return 1
    if norm in ("blocked", "review", "waiting"):
        return 2
    if norm == "done":
        return 4
    return 3


def _service_agents_alive(service_status: dict[str, Any] | None) -> int | None:
    if not isinstance(service_status, dict):
        return None
    agents = service_status.get("agents")
    if not isinstance(agents, dict):
        return None
    raw = agents.get("alive")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _service_warning(service_status: dict[str, Any] | None) -> str:
    if not isinstance(service_status, dict):
        return ""
    return str(service_status.get("warning") or "").strip()


def _build_repo_narrative(snap: RepoSnapshot) -> str:
    if not snap.exists:
        return f"{snap.name}: repo missing from workspace."
    if snap.errors:
        return f"{snap.name}: attention required ({', '.join(snap.errors[:3])})."
    parts: list[str] = []
    in_progress = len(snap.in_progress)
    ready = len(snap.ready)
    open_count = int(snap.task_counts.get("open", 0)) + int(snap.task_counts.get("ready", 0))
    if in_progress > 0 and snap.activity_state == "active":
        parts.append(f"{in_progress} in progress")
    elif in_progress > 0:
        parts.append(f"{in_progress} marked in progress")
    runtime = snap.runtime if isinstance(snap.runtime, dict) else {}
    active_workers = runtime.get("active_workers") if isinstance(runtime.get("active_workers"), list) else []
    control = runtime.get("control") if isinstance(runtime.get("control"), dict) else {}
    control_mode = str(control.get("mode") or "").strip().lower()
    service_agents_alive = _service_agents_alive(snap.service_status)
    service_warning = _service_warning(snap.service_status)
    presence_actors = getattr(snap, 'presence_actors', None) or []
    if presence_actors:
        actor_names = [a.get("name", a.get("id", "?")) for a in presence_actors]
        parts.append(f"{len(presence_actors)} active via presence ({', '.join(actor_names[:3])})")
    if active_workers:
        parts.append(f"{len(active_workers)} runtime workers active")
    elif in_progress > 0 and snap.service_running and service_agents_alive == 0:
        parts.append("workgraph service has no live agents")
    elif control_mode in {"manual", "observe"} and open_count > 0:
        parts.append(f"control mode {control_mode}")
    waiting_count = int(snap.task_counts.get("waiting", 0))
    if ready > 0:
        parts.append(f"{ready} ready to start")
    if waiting_count > 0:
        parts.append(f"{waiting_count} waiting on conditions")
    if open_count > 0 and in_progress == 0:
        parts.append(f"{open_count} open without active execution")
    if snap.stalled and snap.stall_reasons:
        parts.append(f"stalled: {snap.stall_reasons[0]}")
    if snap.activity_state == "idle":
        parts.append("no open tasks currently tracked")
    if snap.activity_state == "untracked":
        parts.append("workgraph graph not found")
    if snap.blocked_open > 0:
        parts.append(f"{snap.blocked_open} open tasks blocked by dependencies")
    if snap.missing_dependencies > 0:
        parts.append(f"{snap.missing_dependencies} missing dependency references")
    if snap.stale_open:
        parts.append(f"{len(snap.stale_open)} aging open tasks")
    if snap.stale_in_progress:
        parts.append(f"{len(snap.stale_in_progress)} long-running in-progress tasks")
    if snap.workgraph_exists and not snap.service_running:
        parts.append("workgraph service not running")
    elif service_warning and in_progress > 0 and not active_workers:
        parts.append(service_warning)
    repo_ns = snap.repo_north_star if isinstance(snap.repo_north_star, dict) else {}
    if not bool(repo_ns.get("present")):
        parts.append("repo north star not defined")
    elif str(repo_ns.get("status") or "") == "weak":
        parts.append("repo north star needs canonicalization")
    if snap.behind > 0:
        parts.append(f"behind upstream by {snap.behind}")
    if snap.git_dirty:
        parts.append("working tree has local changes")
    if not parts:
        return f"{snap.name}: healthy, no immediate blockers."
    return f"{snap.name}: " + "; ".join(parts[:6]) + "."


def _derive_repo_activity_state(snap: RepoSnapshot) -> tuple[str, list[str]]:
    if not snap.exists:
        return "missing", ["repo missing from workspace"]
    if snap.errors:
        reason = ", ".join(snap.errors[:2])
        return "error", [f"errors present ({reason})"]
    if not snap.workgraph_exists:
        return "untracked", ["no .workgraph/graph.jsonl detected"]

    # Presence-based activity detection: if any actor has a live heartbeat,
    # the repo is active regardless of wg service state.
    presence_actors = getattr(snap, 'presence_actors', None) or []
    if presence_actors:
        return "active", []

    in_progress = len(snap.in_progress)
    ready = len(snap.ready)
    open_count = int(snap.task_counts.get("open", 0)) + int(snap.task_counts.get("ready", 0))
    waiting_count = int(snap.task_counts.get("waiting", 0))
    runtime = snap.runtime if isinstance(snap.runtime, dict) else {}
    active_workers = runtime.get("active_workers") if isinstance(runtime.get("active_workers"), list) else []
    stalled_task_ids = runtime.get("stalled_task_ids") if isinstance(runtime.get("stalled_task_ids"), list) else []
    next_action = str(runtime.get("next_action") or "").strip()
    control = runtime.get("control") if isinstance(runtime.get("control"), dict) else {}
    control_mode = str(control.get("mode") or "").strip().lower()
    service_agents_alive = _service_agents_alive(snap.service_status)
    service_warning = _service_warning(snap.service_status)

    if active_workers:
        stalled_workers = [
            row for row in active_workers
            if isinstance(row, dict) and str(row.get("state") or "").strip().lower() == "stalled"
        ]
        if stalled_workers and len(stalled_workers) == len(active_workers):
            reasons = [f"{len(stalled_workers)} runtime workers stalled"]
            if next_action:
                reasons.append(next_action)
            return "stalled", reasons[:6]
        return "active", []
    if in_progress > 0:
        if service_agents_alive and service_agents_alive > 0:
            return "active", []
        # Service running with in-progress tasks = assume active even if agents
        # haven't registered yet (wg coordinator is slow to update registry).
        if snap.service_running and not snap.stale_in_progress:
            return "active", []
        reasons: list[str] = []
        if snap.service_running:
            reasons.append("workgraph service running but no live agents")
        else:
            reasons.append("workgraph service not running")
        reasons.append(f"{in_progress} tasks marked in-progress without live execution")
        if snap.stale_in_progress:
            reasons.append(f"{len(snap.stale_in_progress)} in-progress tasks are aging")
        if stalled_task_ids:
            reasons.append(f"{len(stalled_task_ids)} tasks marked stalled by runtime supervisor")
        if next_action:
            reasons.append(next_action)
        if service_warning:
            reasons.append(service_warning)
        return "stalled", reasons[:6]
    if open_count <= 0 and waiting_count <= 0:
        return "idle", ["no open or ready tasks in graph"]
    if open_count <= 0 and waiting_count > 0:
        return "idle", [f"{waiting_count} task(s) parked in waiting status"]

    reasons: list[str] = [f"{open_count} open/ready tasks but none in-progress"]
    if control_mode in {"manual", "observe"}:
        reasons.insert(0, f"control mode {control_mode} prevents automatic dispatch")
    if stalled_task_ids:
        reasons.insert(0, f"{len(stalled_task_ids)} tasks marked stalled by runtime supervisor")
    if ready > 0:
        reasons.append(f"{ready} ready tasks not started")
    if snap.blocked_open >= open_count and open_count > 0:
        reasons.append("all open tasks are dependency blocked")
    elif snap.blocked_open > 0:
        reasons.append(f"{snap.blocked_open} open tasks are dependency blocked")
    if snap.missing_dependencies > 0:
        reasons.append(f"{snap.missing_dependencies} missing dependency references")
    if snap.workgraph_exists and not snap.service_running:
        reasons.append("workgraph service not running")
    if snap.stale_open:
        reasons.append(f"{len(snap.stale_open)} open tasks are aging")
    if len(reasons) == 1:
        reasons.append("no active executor currently claiming work")
    return "stalled", reasons[:6]


def _finalize_repo_snapshot(snap: RepoSnapshot) -> RepoSnapshot:
    activity_state, stall_reasons = _derive_repo_activity_state(snap)
    snap.activity_state = activity_state
    snap.stalled = activity_state == "stalled"
    snap.stall_reasons = stall_reasons[:6]
    snap.narrative = _build_repo_narrative(snap)
    return snap


def _attach_sec_qa_signals(
    snap: RepoSnapshot,
    *,
    repo_path: Path,
    secdrift_policy: dict[str, Any] | None = None,
    qadrift_policy: dict[str, Any] | None = None,
) -> RepoSnapshot:
    sec_cfg = dict(secdrift_policy) if isinstance(secdrift_policy, dict) else {}
    qa_cfg = dict(qadrift_policy) if isinstance(qadrift_policy, dict) else {}

    try:
        sec_report = run_secdrift_scan(
            repo_name=snap.name,
            repo_path=repo_path,
            policy_cfg=sec_cfg,
        )
        summary = sec_report.get("summary")
        snap.security = dict(summary) if isinstance(summary, dict) else {}
        rows = sec_report.get("top_findings")
        snap.security_findings = [row for row in rows if isinstance(row, dict)][:18] if isinstance(rows, list) else []
    except Exception as exc:
        snap.security = {
            "findings_total": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "at_risk": False,
            "risk_score": 0,
            "narrative": f"secdrift scan failed: {exc}",
        }
        snap.security_findings = []

    try:
        qa_report = run_program_quality_scan(
            repo_name=snap.name,
            repo_path=repo_path,
            repo_snapshot={
                "stalled": snap.stalled,
                "stall_reasons": list(snap.stall_reasons),
                "missing_dependencies": snap.missing_dependencies,
                "blocked_open": snap.blocked_open,
                "workgraph_exists": snap.workgraph_exists,
                "service_running": snap.service_running,
                "in_progress": list(snap.in_progress),
                "ready": list(snap.ready),
            },
            policy_cfg=qa_cfg,
        )
        summary = qa_report.get("summary")
        snap.quality = dict(summary) if isinstance(summary, dict) else {}
        rows = qa_report.get("top_findings")
        snap.quality_findings = [row for row in rows if isinstance(row, dict)][:18] if isinstance(rows, list) else []
    except Exception as exc:
        snap.quality = {
            "findings_total": 0,
            "critical": 0,
            "high": 0,
            "medium": 0,
            "low": 0,
            "at_risk": False,
            "quality_score": 100,
            "narrative": f"qadrift scan failed: {exc}",
        }
        snap.quality_findings = []

    return snap


def _build_repo_task_graph(tasks: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not tasks:
        return [], []

    now = datetime.now(timezone.utc)
    normalized: dict[str, dict[str, Any]] = {}
    for task in tasks.values():
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        status = str(task.get("status") or "unknown").strip().lower()
        created_at = str(task.get("created_at") or "")
        deps = _normalize_dependencies(task.get("after"))
        age = _age_days(created_at, now=now)
        blocked = False
        for dep in deps:
            dep_row = tasks.get(dep)
            if dep_row and str(dep_row.get("status") or "").strip().lower() != "done":
                blocked = True
                break
        normalized[task_id] = {
            "id": task_id,
            "title": str(task.get("title") or ""),
            "status": status,
            "created_at": created_at,
            "after": deps,
            "age_days": age,
            "blocked": blocked,
        }

    ranked = sorted(
        normalized.values(),
        key=lambda row: (
            _task_status_rank(str(row.get("status") or "")),
            -(float(row.get("age_days") or 0.0)),
            str(row.get("id") or ""),
        ),
    )
    selected_ids = [str(row.get("id") or "") for row in ranked[:_MAX_TASK_GRAPH_NODES]]
    selected: set[str] = {item for item in selected_ids if item}

    # Pull in direct dependencies for selected nodes so relationships are visible.
    for row in ranked:
        row_id = str(row.get("id") or "")
        if row_id not in selected:
            continue
        for dep in row.get("after") or []:
            dep_id = str(dep).strip()
            if dep_id and dep_id in normalized and len(selected) < _MAX_TASK_GRAPH_NODES:
                selected.add(dep_id)

    nodes = []
    for task_id in sorted(selected):
        row = normalized.get(task_id)
        if not row:
            continue
        nodes.append(
            {
                "id": task_id,
                "label": str(row.get("title") or task_id),
                "status": str(row.get("status") or "unknown"),
                "age_days": row.get("age_days"),
                "blocked": bool(row.get("blocked")),
            }
        )

    edges: list[dict[str, Any]] = []
    for task_id in sorted(selected):
        row = normalized.get(task_id)
        if not row:
            continue
        for dep in row.get("after") or []:
            dep_id = str(dep).strip()
            if dep_id and dep_id in selected:
                edges.append({"source": dep_id, "target": task_id})
    return nodes, edges


def collect_repo_snapshot(
    repo_name: str,
    repo_path: Path,
    *,
    max_next: int = 5,
    known_repo_names: set[str] | None = None,
    secdrift_policy: dict[str, Any] | None = None,
    qadrift_policy: dict[str, Any] | None = None,
) -> RepoSnapshot:
    snap = RepoSnapshot(name=repo_name, path=str(repo_path), exists=repo_path.exists())
    if not snap.exists:
        snap.errors.append("repo_missing")
        return _finalize_repo_snapshot(snap)
    snap.repo_north_star = _collect_repo_north_star(repo_path)
    if not (repo_path / ".git").exists():
        snap.errors.append("not_a_git_repo")
        return _finalize_repo_snapshot(snap)

    rc, branch, err = _run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_path)
    if rc != 0:
        snap.errors.append(f"git_branch_error:{err or 'unknown'}")
    else:
        snap.git_branch = branch

    rc, porcelain, err = _run_cmd(["git", "status", "--porcelain"], cwd=repo_path)
    if rc != 0:
        snap.errors.append(f"git_status_error:{err or 'unknown'}")
    else:
        lines = [line for line in porcelain.splitlines() if line.strip()]
        snap.git_dirty = bool(lines)
        snap.dirty_file_count = len(lines)
        snap.untracked_file_count = sum(1 for line in lines if line.startswith("??"))

    base_ref = _git_default_ref(repo_path)
    rc, counts, _ = _run_cmd(["git", "rev-list", "--left-right", "--count", f"{base_ref}...HEAD"], cwd=repo_path)
    if rc == 0 and counts:
        parts = counts.split()
        if len(parts) >= 2:
            try:
                snap.behind = int(parts[0])
                snap.ahead = int(parts[1])
            except ValueError:
                pass

    wg_dir = repo_path / ".workgraph"
    if not (wg_dir / "graph.jsonl").exists():
        snap = _finalize_repo_snapshot(snap)
        return _attach_sec_qa_signals(
            snap,
            repo_path=repo_path,
            secdrift_policy=secdrift_policy,
            qadrift_policy=qadrift_policy,
        )

    snap.workgraph_exists = True
    snap.reporting = True
    snap.heartbeat_age_seconds = _path_age_seconds(wg_dir / "graph.jsonl")

    # Read presence heartbeat records for activity detection.
    try:
        from driftdriver.presence import active_actors as _active_actors

        presence_records = _active_actors(repo_path)
        snap.presence_actors = [
            {
                "id": r.actor.id,
                "name": r.actor.name,
                "class": r.actor.actor_class,
                "task": r.current_task,
                "status": r.status,
            }
            for r in presence_records
        ]
    except Exception:
        snap.presence_actors = []
    snap.runtime = _read_json(wg_dir / "service" / "runtime" / "current.json")
    if not snap.runtime:
        try:
            from driftdriver.speedriftd_state import load_control_state

            snap.runtime = {"control": load_control_state(repo_path)}
        except Exception:
            snap.runtime = {}

    # Continuation intent from control.json.
    control_data = _read_json(wg_dir / "service" / "runtime" / "control.json")
    if isinstance(control_data, dict) and isinstance(control_data.get("continuation_intent"), dict):
        snap.continuation_intent = control_data["continuation_intent"]

    # Service status is best-effort; missing wg is non-fatal.
    rc, status_json, _ = _run_cmd(["wg", "--dir", str(wg_dir), "service", "status", "--json"], cwd=repo_path)
    if rc == 0 and status_json:
        try:
            status = json.loads(status_json)
        except Exception:
            status = {}
        if isinstance(status, dict):
            snap.service_status = status
            state = str(status.get("status") or "")
            running = bool(status.get("running")) or state == "running"
            snap.service_running = running

    wg = load_workgraph(wg_dir)
    policy_order: list[str] = []
    try:
        policy_order = list(load_drift_policy(wg_dir).order)
    except Exception:
        policy_order = []
    counts: dict[str, int] = {}
    in_progress: list[dict[str, str]] = []
    stale_open: list[dict[str, Any]] = []
    stale_in_progress: list[dict[str, Any]] = []
    dependency_issues: list[dict[str, Any]] = []
    blocked_open = 0
    missing_dependencies = 0
    now = datetime.now(timezone.utc)

    for task in wg.tasks.values():
        task_id = str(task.get("id") or "")
        task_title = str(task.get("title") or "")
        status = str(task.get("status") or "unknown").lower()
        created_at = str(task.get("created_at") or "")
        age = _age_days(created_at, now=now)
        deps = _normalize_dependencies(task.get("after"))
        counts[status] = counts.get(status, 0) + 1
        if status == "in-progress":
            in_progress.append({"id": task_id, "title": task_title})
            if age is not None and age >= _STALE_IN_PROGRESS_DAYS:
                stale_in_progress.append(
                    {
                        "id": task_id,
                        "title": task_title,
                        "status": status,
                        "age_days": age,
                        "created_at": created_at,
                    }
                )
            continue

        # Waiting tasks are intentionally parked — count them but skip
        # stale/blocked analysis so they don't trigger false alarms.
        if status == "waiting":
            continue

        if status in ("open", "ready"):
            blocking: list[dict[str, str]] = []
            for dep in deps:
                dep_id = str(dep).strip()
                if not dep_id:
                    continue
                dep = wg.tasks.get(dep_id)
                if dep is None:
                    missing_dependencies += 1
                    dependency_issues.append(
                        {
                            "kind": "missing_dependency",
                            "task_id": task_id,
                            "task_title": task_title,
                            "dependency": dep_id,
                        }
                    )
                    continue
                dep_status = str(dep.get("status") or "").strip().lower()
                if dep_status != "done":
                    blocking.append({"dependency": dep_id, "status": dep_status})

            if blocking:
                blocked_open += 1
                dependency_issues.append(
                    {
                        "kind": "blocked_dependency",
                        "task_id": task_id,
                        "task_title": task_title,
                        "blocking": blocking[:4],
                    }
                )

            if age is not None and age >= _STALE_OPEN_DAYS:
                stale_open.append(
                    {
                        "id": task_id,
                        "title": task_title,
                        "status": status,
                        "age_days": age,
                        "created_at": created_at,
                    }
                )

    stale_open.sort(key=lambda row: (-float(row.get("age_days") or 0.0), str(row.get("id") or "")))
    stale_in_progress.sort(key=lambda row: (-float(row.get("age_days") or 0.0), str(row.get("id") or "")))

    snap.task_counts = counts
    snap.in_progress = in_progress
    snap.ready = _compute_ready_tasks(wg.tasks)[:max_next]
    snap.blocked_open = blocked_open
    snap.missing_dependencies = missing_dependencies
    snap.stale_open = stale_open[:20]
    snap.stale_in_progress = stale_in_progress[:20]
    snap.dependency_issues = dependency_issues[:30]
    task_graph_nodes, task_graph_edges = _build_repo_task_graph(wg.tasks)
    snap.task_graph_nodes = task_graph_nodes
    snap.task_graph_edges = task_graph_edges
    snap.cross_repo_dependencies = _collect_cross_repo_dependencies(
        repo_name=repo_name,
        tasks=wg.tasks,
        known_repo_names=known_repo_names or set(),
        policy_order=policy_order,
    )
    snap = _finalize_repo_snapshot(snap)
    return _attach_sec_qa_signals(
        snap,
        repo_path=repo_path,
        secdrift_policy=secdrift_policy,
        qadrift_policy=qadrift_policy,
    )


def rank_next_work(repos: list[RepoSnapshot], *, limit: int = 20) -> list[dict[str, Any]]:
    from driftdriver.hub_analytics import rank_next_work as _impl

    return _impl(repos, limit=limit)


def _repo_attention_entry(repo: RepoSnapshot) -> dict[str, Any] | None:
    from driftdriver.hub_analytics import repo_attention_entry

    return repo_attention_entry(repo)


def build_repo_dependency_overview(repos: list[RepoSnapshot]) -> dict[str, Any]:
    from driftdriver.hub_analytics import build_repo_dependency_overview as _impl

    return _impl(repos)


def build_ecosystem_overview(
    repos: list[RepoSnapshot],
    *,
    upstream_candidates: int,
    updates: dict[str, Any],
    central_reports: list[dict[str, Any]],
) -> dict[str, Any]:
    from driftdriver.hub_analytics import build_ecosystem_overview as _impl

    return _impl(
        repos,
        upstream_candidates=upstream_candidates,
        updates=updates,
        central_reports=central_reports,
    )


def build_ecosystem_narrative(overview: dict[str, Any]) -> str:
    from driftdriver.hub_analytics import build_ecosystem_narrative as _impl

    return _impl(overview)


def build_secdrift_overview(repos: list[RepoSnapshot]) -> dict[str, Any]:
    from driftdriver.hub_analytics import build_secdrift_overview as _impl

    return _impl(repos)


def build_qadrift_overview(repos: list[RepoSnapshot]) -> dict[str, Any]:
    from driftdriver.hub_analytics import build_qadrift_overview as _impl

    return _impl(repos)


def collect_ecosystem_snapshot(
    *,
    project_dir: Path,
    workspace_root: Path,
    ecosystem_toml: Path | None = None,
    max_next: int = 5,
    include_updates: bool = True,
    central_repo: Path | None = None,
    update_checker: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    ecosystem_file = ecosystem_toml or (workspace_root / "speedrift-ecosystem" / "ecosystem.toml")
    repo_map = _load_ecosystem_repos(ecosystem_file, workspace_root)
    repo_sources: dict[str, str] = {name: "ecosystem-toml" for name in repo_map}
    if project_dir.name not in repo_map:
        repo_map[project_dir.name] = project_dir
        repo_sources[project_dir.name] = "project-dir"

    discovered = _discover_active_workspace_repos(workspace_root, existing=set(repo_map.keys()))
    for name, path in discovered.items():
        repo_map[name] = path
        repo_sources[name] = "autodiscovered"

    known_repo_names = {str(name).strip() for name in repo_map.keys() if str(name).strip()}
    try:
        hub_policy = load_drift_policy(project_dir / ".workgraph")
    except Exception:
        hub_policy = None
    secdrift_policy = (
        dict(getattr(hub_policy, "secdrift"))
        if hub_policy is not None and isinstance(getattr(hub_policy, "secdrift", {}), dict)
        else {}
    )
    qadrift_policy = (
        dict(getattr(hub_policy, "qadrift"))
        if hub_policy is not None and isinstance(getattr(hub_policy, "qadrift", {}), dict)
        else {}
    )

    repos: list[RepoSnapshot] = []
    upstream: list[UpstreamCandidate] = []
    for name, path in sorted(repo_map.items()):
        repo_snap = collect_repo_snapshot(
            name,
            path,
            max_next=max_next,
            known_repo_names=known_repo_names,
            secdrift_policy=secdrift_policy,
            qadrift_policy=qadrift_policy,
        )
        repo_snap.source = repo_sources.get(name, "ecosystem-toml")
        repos.append(repo_snap)
        upstream.extend(generate_upstream_candidates(name, path))

    updates: dict[str, Any] = {"has_updates": False, "has_discoveries": False, "summary": ""}
    if include_updates:
        checker = update_checker or _default_update_checker
        try:
            updates = checker(project_dir=project_dir, repo_map=repo_map)
        except Exception as exc:
            updates = {
                "has_updates": False,
                "has_discoveries": False,
                "summary": f"Update check failed: {exc}",
                "raw": {},
            }

    central_reports = _collect_central_reports_summary(central_repo) if central_repo else []
    overview = build_ecosystem_overview(
        repos,
        upstream_candidates=len(upstream),
        updates=updates,
        central_reports=central_reports,
    )
    # Persist overview for trend analysis — never break the hub
    try:
        from driftdriver.reporting import record_ecosystem_snapshot

        record_ecosystem_snapshot(overview)
    except Exception:
        pass

    repo_dependency_overview = build_repo_dependency_overview(repos)
    narrative = build_ecosystem_narrative(overview)

    snapshot = {
        "schema": 1,
        "generated_at": _iso_now(),
        "project_dir": str(project_dir),
        "workspace_root": str(workspace_root),
        "repo_count": len(repos),
        "repos": [asdict(r) for r in repos],
        "next_work": rank_next_work(repos, limit=max_next * max(1, len(repos))),
        "updates": updates,
        "upstream_candidates": [asdict(c) for c in upstream],
        "central_reports": central_reports,
        "repo_sources": repo_sources,
        "overview": overview,
        "repo_dependency_overview": repo_dependency_overview,
        "narrative": narrative,
        "secdrift": build_secdrift_overview(repos),
        "qadrift": build_qadrift_overview(repos),
    }
    return snapshot


def service_paths(project_dir: Path) -> dict[str, Path]:
    base = project_dir / ".workgraph" / "service" / "ecosystem-hub"
    return {
        "dir": base,
        "pid": base / "pid",
        "state": base / "state.json",
        "heartbeat": base / "heartbeat.json",
        "snapshot": base / "snapshot.json",
        "log": base / "hub.log",
    }


def _northstardrift_config(project_dir: Path) -> dict[str, Any]:
    try:
        policy = load_drift_policy(project_dir / ".workgraph")
    except Exception:
        return {}
    return dict(policy.northstardrift) if isinstance(getattr(policy, "northstardrift", {}), dict) else {}


def _decorate_snapshot_with_northstardrift(
    *,
    project_dir: Path,
    snapshot: dict[str, Any],
    central_repo: Path | None,
) -> dict[str, Any]:
    cfg = _northstardrift_config(project_dir)
    enabled = bool(cfg.get("enabled", True))
    if not enabled:
        snapshot["northstardrift"] = {
            "schema": 1,
            "generated_at": str(snapshot.get("generated_at") or ""),
            "enabled": False,
            "summary": {
                "overall_score": 0.0,
                "overall_tier": "watch",
                "overall_trend": "flat",
                "overall_delta": 0.0,
                "narrative": "northstardrift disabled by policy",
            },
            "axes": {},
            "repo_scores": [],
            "counts": {},
            "regressions": [],
            "improvements": [],
            "operator_prompts": [],
            "recommended_reviews": [],
            "targets": {"overall": {}, "axes": {}, "summary": {}, "priority_gaps": []},
            "history": {
                "points": [],
                "daily_points": [],
                "weekly_points": [],
                "windows": {},
                "summary": {"count": 0, "daily_count": 0, "weekly_count": 0, "window": "recent"},
            },
            "task_emit": {"enabled": False, "attempted": 0, "created": 0, "existing": 0, "skipped": 0, "errors": [], "tasks": []},
        }
        return snapshot["northstardrift"]

    paths = service_paths(project_dir)
    previous = load_previous_northstardrift(service_dir=paths["dir"], central_repo=central_repo)
    northstar = apply_northstardrift(snapshot, previous=previous or None, config=cfg)
    artifacts = write_northstardrift_artifacts(
        service_dir=paths["dir"],
        central_repo=central_repo,
        northstardrift=northstar,
        config=cfg,
    )
    if isinstance(snapshot.get("northstardrift"), dict):
        snapshot["northstardrift"]["history"] = read_northstardrift_history(
            service_dir=paths["dir"],
            central_repo=central_repo,
            current=snapshot["northstardrift"],
            limit=max(6, int(cfg.get("history_points") or 18)),
            weekly_limit=max(4, int(cfg.get("weekly_rollup_weeks") or 8)),
        )
        snapshot["northstardrift"]["artifacts"] = artifacts
    return snapshot.get("northstardrift") if isinstance(snapshot.get("northstardrift"), dict) else {}


def write_snapshot_once(
    *,
    project_dir: Path,
    workspace_root: Path,
    ecosystem_toml: Path | None,
    include_updates: bool,
    max_next: int,
    central_repo: Path | None = None,
) -> dict[str, Any]:
    paths = service_paths(project_dir)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    snapshot = collect_ecosystem_snapshot(
        project_dir=project_dir,
        workspace_root=workspace_root,
        ecosystem_toml=ecosystem_toml,
        include_updates=include_updates,
        max_next=max_next,
        central_repo=central_repo,
    )
    _decorate_snapshot_with_northstardrift(
        project_dir=project_dir,
        snapshot=snapshot,
        central_repo=central_repo,
    )
    _write_json(paths["snapshot"], snapshot)
    _write_json(paths["heartbeat"], {"last_tick_at": _iso_now()})
    if central_repo is not None:
        meta = write_central_register(
            central_repo=central_repo,
            project_name=project_dir.name,
            snapshot=snapshot,
        )
        _write_json(paths["dir"] / "central-register.json", meta)
    return snapshot


def supervise_repo_services(
    *,
    repos_payload: list[dict[str, Any]],
    cooldown_seconds: int,
    max_starts: int,
    directive_log: DirectiveLog | None = None,
) -> dict[str, Any]:
    now = time.time()
    attempted = 0
    started = 0
    failed = 0
    cooldown_skipped = 0
    checked = 0
    candidates = 0
    attempt_rows: list[dict[str, Any]] = []

    for row in repos_payload:
        if attempted >= max(1, max_starts):
            break
        if not isinstance(row, dict):
            continue
        checked += 1
        repo_name = str(row.get("name") or "")
        repo_path_raw = str(row.get("path") or "")
        if not repo_name or not repo_path_raw:
            continue
        if not bool(row.get("exists")):
            continue
        if not bool(row.get("workgraph_exists")):
            continue
        if bool(row.get("service_running")):
            continue

        # Only start services in repos whose speedriftd mode permits it.
        repo_path = Path(repo_path_raw).expanduser()
        try:
            from driftdriver.speedriftd_state import load_control_state
            ctrl = load_control_state(repo_path)
            repo_mode = str(ctrl.get("mode") or "observe").strip().lower()
        except Exception:
            repo_mode = "observe"
        if repo_mode not in ("supervise", "autonomous"):
            continue

        in_progress = row.get("in_progress") if isinstance(row.get("in_progress"), list) else []
        ready = row.get("ready") if isinstance(row.get("ready"), list) else []
        if not in_progress and not ready:
            continue
        candidates += 1

        key = str(repo_path.resolve())
        last_attempt = _SUPERVISOR_LAST_ATTEMPT.get(key, 0.0)
        if now - last_attempt < max(1, cooldown_seconds):
            cooldown_skipped += 1
            continue

        _SUPERVISOR_LAST_ATTEMPT[key] = now
        attempted += 1

        if directive_log is not None:
            from driftdriver.directives import Action, Directive
            from driftdriver.executor_shim import ExecutorShim

            directive = Directive(
                source="ecosystem_hub",
                repo=repo_name,
                action=Action.START_SERVICE,
                params={"repo": str(repo_path / ".workgraph")},
                reason=f"service not running with {len(in_progress)} in-progress, {len(ready)} ready tasks",
            )
            wg_dir = repo_path / ".workgraph"
            shim = ExecutorShim(wg_dir=wg_dir, log=directive_log, timeout=15.0)
            shim_result = shim.execute(directive)
            rc = 0 if shim_result == "completed" else 1
            out = shim_result
            err = ""
        else:
            rc, out, err = _run_cmd(
                ["wg", "--dir", str(repo_path / ".workgraph"), "service", "start"],
                cwd=repo_path,
                timeout=15.0,
            )
        text = f"{out}\n{err}".strip().lower()
        ok = rc == 0 or "already running" in text
        if ok:
            started += 1
        else:
            failed += 1
        attempt_rows.append(
            {
                "repo": repo_name,
                "path": str(repo_path),
                "ok": ok,
                "exit_code": rc,
                "stdout": out,
                "stderr": err,
            }
        )

    return {
        "enabled": True,
        "cooldown_seconds": max(1, cooldown_seconds),
        "max_starts_per_cycle": max(1, max_starts),
        "checked_repos": checked,
        "restart_candidates": candidates,
        "attempted": attempted,
        "started": started,
        "failed": failed,
        "cooldown_skipped": cooldown_skipped,
        "last_tick_at": _iso_now(),
        "attempts": attempt_rows[:20],
    }


def read_service_status(project_dir: Path) -> dict[str, Any]:
    paths = service_paths(project_dir)
    pid = 0
    if paths["pid"].exists():
        raw = paths["pid"].read_text(encoding="utf-8").strip()
        try:
            pid = int(raw)
        except ValueError:
            pid = 0
    running = _process_alive(pid)
    heartbeat = _read_json(paths["heartbeat"]) if paths["heartbeat"].exists() else {}
    supervisor = heartbeat.get("supervisor") if isinstance(heartbeat.get("supervisor"), dict) else {}
    state = _read_json(paths["state"]) if paths["state"].exists() else {}
    host = str(state.get("host") or "")
    port = int(state.get("port") or 0)
    if not running and _service_port_alive(host, port):
        running = True
    snapshot_exists = paths["snapshot"].exists()
    central = _read_json(paths["dir"] / "central-register.json")
    upstream_actions = _read_json(paths["dir"] / "upstream-actions.json")
    northstar = _read_json(paths["dir"] / "northstardrift" / "current.json")
    northstar_summary = northstar.get("summary") if isinstance(northstar.get("summary"), dict) else {}
    return {
        "running": running,
        "pid": pid if running else None,
        "service_dir": str(paths["dir"]),
        "last_tick_at": str(heartbeat.get("last_tick_at") or ""),
        "last_error": str(heartbeat.get("error") or ""),
        "supervisor": supervisor,
        "started_at": str(state.get("started_at") or ""),
        "host": host,
        "port": port,
        "central_repo": str(state.get("central_repo") or ""),
        "snapshot_path": str(paths["snapshot"]),
        "snapshot_exists": snapshot_exists,
        "websocket_path": "/ws/status",
        "central_register_latest": str(central.get("latest_path") or ""),
        "upstream_action_count": int(upstream_actions.get("request_count") or 0),
        "upstream_execute_mode": bool(upstream_actions.get("execute_draft_prs", False)),
        "northstardrift_score": float(northstar_summary.get("overall_score") or 0.0),
        "northstardrift_tier": str(northstar_summary.get("overall_tier") or ""),
        "northstardrift_path": str(paths["dir"] / "northstardrift" / "current.json"),
        "log_path": str(paths["log"]),
    }
