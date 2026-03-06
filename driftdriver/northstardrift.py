from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any


AXIS_NAMES = (
    "continuity",
    "autonomy",
    "quality",
    "coordination",
    "self_improvement",
)

AXIS_WEIGHTS: dict[str, float] = {
    "continuity": 0.25,
    "autonomy": 0.20,
    "quality": 0.20,
    "coordination": 0.20,
    "self_improvement": 0.15,
}


def default_northstardrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "emit_review_tasks": True,
        "emit_operator_prompts": True,
        "daily_rollup": True,
        "weekly_trends": True,
        "score_window": "1d",
        "comparison_window": "7d",
        "dirty_repo_blocks_auto_mutation": True,
        "max_auto_interventions_per_cycle": 3,
        "max_review_tasks_per_repo": 2,
        "require_metric_evidence": True,
        "effectiveness_ledger_min_interval_seconds": 3600,
        "regression_ledger_min_interval_seconds": 3600,
        "intervention_ledger_min_interval_seconds": 1800,
        "fresh_heartbeat_seconds": 21600,
        "history_points": 18,
        "latent_repo_floor_score": 68.0,
    }


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 1)


def _ratio_score(numerator: float, denominator: float, *, default: float = 100.0) -> float:
    if denominator <= 0:
        return _clamp_score(default)
    return _clamp_score((numerator / denominator) * 100.0)


def _penalty_inverse(value: float, *, scale: float = 100.0) -> float:
    return _clamp_score(100.0 - min(scale, max(0.0, value)))


def _tier(score: float) -> str:
    if score >= 80.0:
        return "healthy"
    if score >= 60.0:
        return "watch"
    return "at-risk"


def _trend(score: float, previous: float | None) -> tuple[str, float]:
    if previous is None:
        return "flat", 0.0
    delta = round(score - previous, 1)
    if delta >= 3.0:
        return "improving", delta
    if delta <= -2.0:
        return "worsening", delta
    return "flat", delta


def _parse_iso(raw: Any) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=False) + "\n")


def _read_last_jsonl(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return {}
    for line in reversed(lines):
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _should_append(path: Path, ts: str, *, min_interval_seconds: int) -> bool:
    if not path.exists():
        return True
    last = _read_last_jsonl(path)
    previous_dt = _parse_iso(last.get("generated_at") or last.get("ts"))
    current_dt = _parse_iso(ts)
    if previous_dt is None or current_dt is None:
        return True
    return (current_dt - previous_dt).total_seconds() >= max(0, int(min_interval_seconds))


def _read_recent_jsonl(path: Path, *, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, int(limit)) :]:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _fingerprint(parts: list[str]) -> str:
    key = "|".join(str(part or "").strip().lower() for part in parts)
    return sha1(key.encode("utf-8")).hexdigest()  # noqa: S324 - non-crypto identity hash


def _run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 40.0,
) -> tuple[int, str, str]:
    def _invoke(actual_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            actual_cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        proc = _invoke(cmd)
    except FileNotFoundError as exc:
        if cmd and str(cmd[0]) == "wg":
            candidates = [
                str(Path.home() / ".cargo" / "bin" / "wg"),
                "/opt/homebrew/bin/wg",
                "/usr/local/bin/wg",
            ]
            seen: set[str] = set()
            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                if not Path(candidate).exists():
                    continue
                try:
                    proc = _invoke([candidate, *cmd[1:]])
                    return int(proc.returncode), str(proc.stdout or "").strip(), str(proc.stderr or "").strip()
                except FileNotFoundError:
                    continue
        return 127, "", str(exc)
    except Exception as exc:
        return 1, "", str(exc)
    return int(proc.returncode), str(proc.stdout or "").strip(), str(proc.stderr or "").strip()


def _is_participating_repo(repo: dict[str, Any]) -> bool:
    return bool(repo.get("workgraph_exists")) or bool(repo.get("reporting"))


def _is_latent_repo(repo: dict[str, Any]) -> bool:
    if not bool(repo.get("exists")):
        return False
    if _is_participating_repo(repo):
        return False
    if bool(repo.get("git_dirty")) or int(repo.get("behind") or 0) > 0:
        return False
    errors = repo.get("errors")
    if isinstance(errors, list) and errors:
        return False
    return True


def _repo_reasons(repo: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    errors = repo.get("errors")
    if isinstance(errors, list) and errors:
        reasons.append(f"errors: {', '.join(str(item) for item in errors[:2])}")
    if not bool(repo.get("exists")):
        reasons.append("repo missing from workspace")
    if bool(repo.get("exists")) and not bool(repo.get("workgraph_exists")):
        reasons.append("no workgraph found")
    if bool(repo.get("stalled")):
        stall_reasons = repo.get("stall_reasons")
        if isinstance(stall_reasons, list) and stall_reasons:
            reasons.append(f"stalled: {str(stall_reasons[0])}")
        else:
            reasons.append("stalled without active execution")
    if bool(repo.get("workgraph_exists")) and not bool(repo.get("service_running")):
        ready = repo.get("ready")
        in_progress = repo.get("in_progress")
        open_count = int((repo.get("task_counts") or {}).get("open", 0)) + int((repo.get("task_counts") or {}).get("ready", 0))
        if (isinstance(ready, list) and ready) or (isinstance(in_progress, list) and in_progress) or open_count > 0:
            reasons.append("service stopped while work exists")
    missing_dependencies = int(repo.get("missing_dependencies") or 0)
    if missing_dependencies > 0:
        reasons.append(f"{missing_dependencies} missing dependencies")
    blocked_open = int(repo.get("blocked_open") or 0)
    if blocked_open > 0:
        reasons.append(f"{blocked_open} blocked open tasks")
    stale_open = repo.get("stale_open")
    if isinstance(stale_open, list) and stale_open:
        reasons.append(f"{len(stale_open)} aging open tasks")
    stale_in_progress = repo.get("stale_in_progress")
    if isinstance(stale_in_progress, list) and stale_in_progress:
        reasons.append(f"{len(stale_in_progress)} aging active tasks")
    behind = int(repo.get("behind") or 0)
    if behind > 0:
        reasons.append(f"behind upstream by {behind}")
    dirty_file_count = int(repo.get("dirty_file_count") or 0)
    if bool(repo.get("git_dirty")):
        reasons.append(
            f"dirty working tree ({dirty_file_count} files)" if dirty_file_count > 0 else "dirty working tree"
        )
    security = repo.get("security") if isinstance(repo.get("security"), dict) else {}
    quality = repo.get("quality") if isinstance(repo.get("quality"), dict) else {}
    sec_critical = int(security.get("critical") or 0)
    sec_high = int(security.get("high") or 0)
    qa_critical = int(quality.get("critical") or 0)
    qa_high = int(quality.get("high") or 0)
    qa_score = int(quality.get("quality_score") or 100)
    if sec_critical > 0:
        reasons.append(f"security critical={sec_critical}")
    if sec_high > 0:
        reasons.append(f"security high={sec_high}")
    if qa_critical > 0:
        reasons.append(f"quality critical={qa_critical}")
    if qa_high > 0:
        reasons.append(f"quality high={qa_high}")
    if qa_score < 90:
        reasons.append(f"quality score={qa_score}")
    return reasons[:6]


def _repo_prompt(repo_name: str, reasons: list[str], *, tool: str) -> str:
    joined = "; ".join(reasons[:3]) if reasons else "no reasons recorded"
    if tool == "claude":
        return (
            f"Review Speedrift north-star pressure for {repo_name}. Focus on {joined}. "
            "Preserve active work, update local Workgraph dependency/status state, add the smallest corrective tasks needed, "
            "and define verification plus loopback steps before resuming execution."
        )
    return (
        f"Investigate why {repo_name} is below north-star expectations. Focus on {joined}. "
        "Confirm the true blocker, repair Workgraph metadata or emit local corrective tasks, and return the next safe execution step with verification."
    )


def _score_repo(repo: dict[str, Any], previous: dict[str, Any] | None, *, config: dict[str, Any]) -> dict[str, Any]:
    penalty = 0.0
    reasons = _repo_reasons(repo)
    latent_repo = _is_latent_repo(repo)
    if not bool(repo.get("exists")):
        penalty += 85
    elif not bool(repo.get("workgraph_exists")):
        penalty += 18 if latent_repo else 28
    penalty += min(28.0, len(reasons) * 4.0)
    penalty += min(24.0, int(repo.get("missing_dependencies") or 0) * 6.0)
    penalty += min(18.0, int(repo.get("blocked_open") or 0) * 3.0)
    stale_open = repo.get("stale_open") if isinstance(repo.get("stale_open"), list) else []
    stale_in_progress = repo.get("stale_in_progress") if isinstance(repo.get("stale_in_progress"), list) else []
    penalty += min(16.0, len(stale_open) * 2.5)
    penalty += min(22.0, len(stale_in_progress) * 5.0)
    if bool(repo.get("stalled")):
        penalty += 16.0
    if bool(repo.get("workgraph_exists")) and not bool(repo.get("service_running")):
        ready = repo.get("ready") if isinstance(repo.get("ready"), list) else []
        in_progress = repo.get("in_progress") if isinstance(repo.get("in_progress"), list) else []
        open_count = int((repo.get("task_counts") or {}).get("open", 0)) + int((repo.get("task_counts") or {}).get("ready", 0))
        if ready or in_progress or open_count > 0:
            penalty += 16.0
    penalty += min(12.0, int(repo.get("behind") or 0) * 1.5)
    penalty += min(10.0, int(repo.get("dirty_file_count") or (2 if bool(repo.get("git_dirty")) else 0)))
    security = repo.get("security") if isinstance(repo.get("security"), dict) else {}
    quality = repo.get("quality") if isinstance(repo.get("quality"), dict) else {}
    penalty += min(36.0, int(security.get("critical") or 0) * 16.0)
    penalty += min(20.0, int(security.get("high") or 0) * 6.0)
    penalty += min(24.0, int(quality.get("critical") or 0) * 12.0)
    penalty += min(16.0, int(quality.get("high") or 0) * 5.0)
    qa_score = int(quality.get("quality_score") or 100)
    if qa_score < 90:
        penalty += min(12.0, (90 - qa_score) * 0.4)
    if isinstance(repo.get("in_progress"), list) and repo.get("in_progress"):
        penalty = max(0.0, penalty - 4.0)
    score = _clamp_score(100.0 - penalty)
    if latent_repo:
        score = max(score, float(config.get("latent_repo_floor_score") or 68.0))
    previous_score = None
    if isinstance(previous, dict):
        try:
            previous_score = float(previous.get("score"))
        except Exception:
            previous_score = None
    trend, delta = _trend(score, previous_score)
    tier = _tier(score)
    dirty_file_count = int(repo.get("dirty_file_count") or 0)
    dirty_state = "dirty" if bool(repo.get("git_dirty")) else "clean"
    reason = reasons[0] if reasons else "no immediate pressure recorded"
    prompt_claude = _repo_prompt(str(repo.get("name") or ""), reasons, tool="claude")
    prompt_codex = _repo_prompt(str(repo.get("name") or ""), reasons, tool="codex")
    return {
        "repo": str(repo.get("name") or ""),
        "score": score,
        "tier": tier,
        "trend": trend,
        "delta": delta,
        "priority_score": round(max(0.0, 100.0 - score), 1),
        "reason": reason,
        "reasons": reasons,
        "reporting": bool(repo.get("reporting")),
        "heartbeat_age_seconds": repo.get("heartbeat_age_seconds"),
        "dirty_state": dirty_state,
        "dirty_file_count": dirty_file_count,
        "prompts": {
            "claude": prompt_claude,
            "codex": prompt_codex,
        },
    }


def _average_quality_score(repos: list[dict[str, Any]]) -> float:
    scores: list[float] = []
    for repo in repos:
        qa = repo.get("quality") if isinstance(repo.get("quality"), dict) else {}
        scores.append(float(int(qa.get("quality_score") or 100)))
    if not scores:
        return 100.0
    return _clamp_score(sum(scores) / len(scores))


def _build_narrative(
    *,
    overall_score: float,
    overall_tier: str,
    overall_trend: str,
    active_repos: int,
    repos_total: int,
    weakest_axis: tuple[str, dict[str, Any]],
    strongest_axis: tuple[str, dict[str, Any]],
    worst_repo: dict[str, Any] | None,
    top_prompt: dict[str, Any] | None,
) -> str:
    weak_name, weak_axis = weakest_axis
    strong_name, strong_axis = strongest_axis
    parts = [
        f"Dark factory effectiveness is {overall_score:.1f} ({overall_tier}, {overall_trend}) across {repos_total} repos with {active_repos} actively advancing.",
        f"Strongest axis: {strong_name.replace('_', ' ')}={float(strong_axis.get('score') or 0.0):.1f}.",
        f"Weakest axis: {weak_name.replace('_', ' ')}={float(str(weak_axis.get('score') or 0.0)):.1f}.",
    ]
    if isinstance(worst_repo, dict) and str(worst_repo.get("repo") or "").strip():
        parts.append(
            f"Most pressured repo: {worst_repo['repo']} ({worst_repo.get('tier')}, {float(worst_repo.get('score') or 0.0):.1f}) because {worst_repo.get('reason') or 'pressure signals are elevated'}."
        )
    if isinstance(top_prompt, dict) and str(top_prompt.get("repo") or "").strip():
        parts.append(f"Next operator focus: {top_prompt['repo']}.")
    return " ".join(parts)


def compute_northstardrift(
    snapshot: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = default_northstardrift_cfg()
    if isinstance(config, dict):
        cfg.update({key: value for key, value in config.items()})

    repos = snapshot.get("repos") if isinstance(snapshot.get("repos"), list) else []
    overview = snapshot.get("overview") if isinstance(snapshot.get("overview"), dict) else {}
    repo_dep = snapshot.get("repo_dependency_overview") if isinstance(snapshot.get("repo_dependency_overview"), dict) else {}
    factory = snapshot.get("factory") if isinstance(snapshot.get("factory"), dict) else {}
    supervisor = snapshot.get("supervisor") if isinstance(snapshot.get("supervisor"), dict) else {}
    updates = snapshot.get("updates") if isinstance(snapshot.get("updates"), dict) else {}
    upstream_candidates = snapshot.get("upstream_candidates") if isinstance(snapshot.get("upstream_candidates"), list) else []

    previous_repo_scores: dict[str, dict[str, Any]] = {}
    if isinstance(previous, dict):
        rows = previous.get("repo_scores")
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("repo") or "").strip()
                if name:
                    previous_repo_scores[name] = row

    repo_scores = [
        _score_repo(
            repo,
            previous_repo_scores.get(str(repo.get("name") or "").strip()),
            config=cfg,
        )
        for repo in repos
        if isinstance(repo, dict)
    ]
    repo_scores.sort(key=lambda row: (float(row.get("score") or 0.0), str(row.get("repo") or "")))

    total_repos = len(repos)
    participating_repos = sum(1 for repo in repos if isinstance(repo, dict) and _is_participating_repo(repo))
    latent_repos = sum(1 for repo in repos if isinstance(repo, dict) and _is_latent_repo(repo))
    reporting_repos = sum(1 for repo in repos if isinstance(repo, dict) and bool(repo.get("reporting")))
    open_work_repos = 0
    service_healthy_repos = 0
    active_repos = 0
    fresh_repos = 0
    ready_total = int(overview.get("tasks_ready") or 0)
    active_total = int(overview.get("tasks_in_progress") or 0)
    blocked_total = int(overview.get("blocked_open") or 0)
    stale_open_total = int(overview.get("stale_open") or 0)
    stale_active_total = int(overview.get("stale_in_progress") or 0)
    stalled_repos = int(overview.get("repos_stalled") or 0)
    dirty_repos = int(overview.get("repos_dirty") or 0)
    missing_dependencies = int(overview.get("missing_dependencies") or 0)
    total_behind = int(overview.get("total_behind") or 0)
    edge_count = int((repo_dep.get("summary") or {}).get("edge_count") or 0)
    linked_repos = int((repo_dep.get("summary") or {}).get("linked_repos") or 0)

    for repo in repos:
        if not isinstance(repo, dict):
            continue
        open_count = int((repo.get("task_counts") or {}).get("open", 0)) + int((repo.get("task_counts") or {}).get("ready", 0))
        in_progress_count = len(repo.get("in_progress")) if isinstance(repo.get("in_progress"), list) else 0
        needs_service = open_count > 0 or in_progress_count > 0 or (isinstance(repo.get("ready"), list) and bool(repo.get("ready")))
        if needs_service:
            open_work_repos += 1
        if bool(repo.get("service_running")) or not needs_service:
            service_healthy_repos += 1
        if in_progress_count > 0:
            active_repos += 1
        heartbeat_age = repo.get("heartbeat_age_seconds")
        if isinstance(heartbeat_age, int) and heartbeat_age <= int(cfg.get("fresh_heartbeat_seconds") or 21600):
            fresh_repos += 1

    reporting_coverage = _ratio_score(reporting_repos, max(participating_repos, total_repos), default=0.0)
    daemon_uptime_score = _ratio_score(service_healthy_repos, total_repos, default=0.0)
    active_progress_coverage = _ratio_score(active_repos, open_work_repos, default=100.0)
    freshness_score = _ratio_score(fresh_repos, reporting_repos, default=0.0 if reporting_repos <= 0 else 100.0)
    ready_latency_score = _ratio_score(active_total, active_total + ready_total, default=100.0)
    stall_penalty_inverse = _penalty_inverse((stalled_repos * 12.0) + (stale_active_total * 5.0) + (stale_open_total * 2.0))
    continuity = _clamp_score(
        (0.25 * reporting_coverage)
        + (0.20 * daemon_uptime_score)
        + (0.20 * active_progress_coverage)
        + (0.15 * freshness_score)
        + (0.10 * ready_latency_score)
        + (0.10 * stall_penalty_inverse)
    )

    execution = factory.get("execution") if isinstance(factory.get("execution"), dict) else {}
    action_attempted = int(execution.get("attempted") or 0)
    action_succeeded = int(execution.get("succeeded") or 0)
    action_failed = int(execution.get("failed") or 0)
    autonomous_completion_rate = (
        _ratio_score(action_succeeded, action_attempted, default=active_progress_coverage if open_work_repos > 0 else 100.0)
    )
    restart_attempted = int(supervisor.get("attempted") or 0)
    restart_started = int(supervisor.get("started") or 0)
    restart_failed = int(supervisor.get("failed") or 0)
    recovery_success_rate = _ratio_score(restart_started, restart_attempted, default=100.0)
    execution_coverage = active_progress_coverage
    loop_penalty_inverse = _penalty_inverse((action_failed * 14.0) + (restart_failed * 12.0) + (stalled_repos * 6.0))
    autonomy = _clamp_score(
        (0.35 * autonomous_completion_rate)
        + (0.25 * recovery_success_rate)
        + (0.20 * execution_coverage)
        + (0.20 * loop_penalty_inverse)
    )

    quality_population = max(1, participating_repos)
    average_quality = _average_quality_score(
        [repo for repo in repos if isinstance(repo, dict) and _is_participating_repo(repo)]
        or [repo for repo in repos if isinstance(repo, dict)]
    )
    quality_risk_repos = int(overview.get("repos_quality_risk") or 0)
    security_risk_repos = int(overview.get("repos_security_risk") or 0)
    quality_critical = int(overview.get("quality_critical") or 0)
    quality_high = int(overview.get("quality_high") or 0)
    security_critical = int(overview.get("security_critical") or 0)
    security_high = int(overview.get("security_high") or 0)
    qadrift_pressure_inverse = _clamp_score(
        (0.55 * _ratio_score(quality_population - quality_risk_repos, quality_population, default=100.0))
        + (0.25 * _ratio_score(quality_population - min(quality_population, quality_high), quality_population, default=100.0))
        + (0.20 * _ratio_score(quality_population - min(quality_population, quality_critical), quality_population, default=100.0))
    )
    secdrift_pressure_inverse = _clamp_score(
        (0.55 * _ratio_score(quality_population - security_risk_repos, quality_population, default=100.0))
        + (0.25 * _ratio_score(quality_population - min(quality_population, security_high), quality_population, default=100.0))
        + (0.20 * _ratio_score(quality_population - min(quality_population, security_critical), quality_population, default=100.0))
    )
    regression_penalty_inverse = _penalty_inverse((stalled_repos * 8.0) + (blocked_total * 2.5) + (stale_active_total * 5.0))
    dirtiness_penalty_inverse = _ratio_score(total_repos - dirty_repos, total_repos, default=100.0)
    divergence_penalty_inverse = _penalty_inverse((total_behind / max(1, total_repos)) * 6.0)
    quality = _clamp_score(
        (0.35 * average_quality)
        + (0.18 * qadrift_pressure_inverse)
        + (0.17 * secdrift_pressure_inverse)
        + (0.15 * regression_penalty_inverse)
        + (0.10 * dirtiness_penalty_inverse)
        + (0.05 * divergence_penalty_inverse)
    )

    coordination_population = max(1, participating_repos)
    interrepo_reporting = _ratio_score(reporting_repos, coordination_population, default=0.0)
    handoff_success_rate = _ratio_score(
        coordination_population - min(coordination_population, max(missing_dependencies, blocked_total)),
        coordination_population,
        default=100.0,
    )
    dependency_age_inverse = _penalty_inverse(((blocked_total + stale_open_total) / coordination_population) * 18.0)
    dependency_metadata_score = _ratio_score(
        coordination_population - min(coordination_population, missing_dependencies),
        coordination_population,
        default=100.0,
    )
    blocked_repo_penalty_inverse = _penalty_inverse(stalled_repos * 10.0)
    linked_repo_ratio = _ratio_score(linked_repos, total_repos, default=100.0 if total_repos <= 1 else 0.0)
    coordination = _clamp_score(
        (0.25 * interrepo_reporting)
        + (0.25 * handoff_success_rate)
        + (0.20 * dependency_age_inverse)
        + (0.15 * dependency_metadata_score)
        + (0.10 * blocked_repo_penalty_inverse)
        + (0.05 * linked_repo_ratio)
    )

    improvement_change = 70.0
    if isinstance(previous, dict):
        prev_stalled = int((previous.get("counts") or {}).get("stalled_repos") or 0)
        prev_missing = int((previous.get("counts") or {}).get("missing_dependencies") or 0)
        if stalled_repos < prev_stalled or missing_dependencies < prev_missing:
            improvement_change = 85.0
        elif stalled_repos > prev_stalled or missing_dependencies > prev_missing:
            improvement_change = 55.0
    rollout_coverage = reporting_coverage
    throughput_score = _clamp_score(min(100.0, 55.0 + (len(upstream_candidates) * 10.0) + (10.0 if bool(updates.get("has_updates")) else 0.0) + (10.0 if bool(updates.get("has_discoveries")) else 0.0)))
    plan_integrity_coverage = _penalty_inverse((missing_dependencies * 9.0) + (blocked_total * 3.0) + (stale_active_total * 4.0))
    self_improvement = _clamp_score(
        (0.25 * improvement_change)
        + (0.25 * rollout_coverage)
        + (0.20 * throughput_score)
        + (0.30 * plan_integrity_coverage)
    )

    axis_raw = {
        "continuity": continuity,
        "autonomy": autonomy,
        "quality": quality,
        "coordination": coordination,
        "self_improvement": self_improvement,
    }
    previous_axes = previous.get("axes") if isinstance(previous, dict) and isinstance(previous.get("axes"), dict) else {}
    axes: dict[str, dict[str, Any]] = {}
    for name, score in axis_raw.items():
        previous_score = None
        if isinstance(previous_axes.get(name), dict):
            try:
                previous_score = float(previous_axes[name].get("score"))
            except Exception:
                previous_score = None
        trend, delta = _trend(score, previous_score)
        axes[name] = {
            "score": score,
            "tier": _tier(score),
            "trend": trend,
            "delta": delta,
        }

    overall_score = _clamp_score(sum(axis_raw[name] * AXIS_WEIGHTS[name] for name in AXIS_NAMES))
    previous_overall = None
    if isinstance(previous, dict):
        try:
            previous_overall = float((previous.get("summary") or {}).get("overall_score"))
        except Exception:
            previous_overall = None
    overall_trend, overall_delta = _trend(overall_score, previous_overall)
    overall_tier = _tier(overall_score)

    regressions: list[dict[str, Any]] = []
    improvements: list[dict[str, Any]] = []
    for name in AXIS_NAMES:
        row = axes[name]
        if str(row.get("trend")) == "worsening":
            regressions.append(
                {
                    "kind": "axis",
                    "axis": name,
                    "summary": f"{name.replace('_', ' ')} worsened by {row.get('delta')} to {row.get('score')}",
                }
            )
        elif str(row.get("trend")) == "improving":
            improvements.append(
                {
                    "kind": "axis",
                    "axis": name,
                    "summary": f"{name.replace('_', ' ')} improved by +{row.get('delta')} to {row.get('score')}",
                }
            )

    for row in repo_scores[:5]:
        if str(row.get("tier")) != "healthy":
            regressions.append(
                {
                    "kind": "repo",
                    "repo": row.get("repo"),
                    "summary": f"{row.get('repo')} is {row.get('tier')} at {row.get('score')} because {row.get('reason')}",
                }
            )
    for row in sorted(repo_scores, key=lambda item: (-float(item.get("score") or 0.0), str(item.get("repo") or "")))[:5]:
        if float(row.get("score") or 0.0) >= 80.0 or str(row.get("trend")) == "improving":
            improvements.append(
                {
                    "kind": "repo",
                    "repo": row.get("repo"),
                    "summary": f"{row.get('repo')} is {row.get('tier')} at {row.get('score')}",
                }
            )

    regressions = regressions[:6]
    improvements = improvements[:6]

    operator_prompts: list[dict[str, Any]] = []
    if bool(cfg.get("emit_operator_prompts", True)):
        for row in repo_scores[: max(1, int(cfg.get("max_auto_interventions_per_cycle") or 3))]:
            if str(row.get("tier")) == "healthy":
                continue
            operator_prompts.append(
                {
                    "priority": "high" if str(row.get("tier")) == "at-risk" else "medium",
                    "repo": str(row.get("repo") or ""),
                    "score": row.get("score"),
                    "reason": row.get("reason"),
                    "claude_prompt": ((row.get("prompts") or {}).get("claude") if isinstance(row.get("prompts"), dict) else ""),
                    "codex_prompt": ((row.get("prompts") or {}).get("codex") if isinstance(row.get("prompts"), dict) else ""),
                }
            )

    recommended_reviews: list[dict[str, Any]] = []
    for row in operator_prompts:
        repo_name = str(row.get("repo") or "").strip()
        if not repo_name:
            continue
        priority = str(row.get("priority") or "medium").lower()
        severity = "high" if priority == "high" else "medium"
        reason = str(row.get("reason") or "").strip() or "north-star pressure elevated"
        fingerprint = _fingerprint([repo_name, severity, reason])
        recommended_reviews.append(
            {
                "fingerprint": fingerprint,
                "repo": repo_name,
                "severity": severity,
                "category": "repo-attention",
                "title": f"North-star attention review for {repo_name}",
                "evidence": reason,
                "recommendation": "Review the local graph, preserve active work, and emit the smallest corrective tasks needed to reduce the north-star pressure.",
                "model_prompt": str(row.get("claude_prompt") or ""),
                "codex_prompt": str(row.get("codex_prompt") or ""),
                "score": row.get("score"),
            }
        )

    weakest_axis = min(axes.items(), key=lambda item: float(item[1].get("score") or 0.0)) if axes else ("continuity", {"score": 0})
    strongest_axis = max(axes.items(), key=lambda item: float(item[1].get("score") or 0.0)) if axes else ("continuity", {"score": 0})
    narrative = _build_narrative(
        overall_score=overall_score,
        overall_tier=overall_tier,
        overall_trend=overall_trend,
        active_repos=active_repos,
        repos_total=total_repos,
        weakest_axis=weakest_axis,
        strongest_axis=strongest_axis,
        worst_repo=repo_scores[0] if repo_scores else None,
        top_prompt=operator_prompts[0] if operator_prompts else None,
    )

    return {
        "schema": 1,
        "generated_at": str(snapshot.get("generated_at") or ""),
        "window": str(cfg.get("score_window") or "1d"),
        "comparison_window": str(cfg.get("comparison_window") or "7d"),
        "summary": {
            "overall_score": overall_score,
            "overall_tier": overall_tier,
            "overall_trend": overall_trend,
            "overall_delta": overall_delta,
            "narrative": narrative,
        },
        "axes": axes,
        "repo_scores": repo_scores,
        "counts": {
            "tracked_repos": total_repos,
            "participating_repos": participating_repos,
            "reporting_repos": reporting_repos,
            "latent_repos": latent_repos,
            "active_repos": active_repos,
            "stalled_repos": stalled_repos,
            "blocked_repos": sum(1 for repo in repos if isinstance(repo, dict) and int(repo.get("blocked_open") or 0) > 0),
            "missing_dependencies": missing_dependencies,
            "linked_repos": linked_repos,
            "dependency_edges": edge_count,
        },
        "regressions": regressions,
        "improvements": improvements,
        "operator_prompts": operator_prompts,
        "recommended_reviews": recommended_reviews,
        "calibration": {
            "quality_population": quality_population,
            "coordination_population": coordination_population,
            "latent_repo_floor_score": float(cfg.get("latent_repo_floor_score") or 68.0),
            "notes": [
                "quality/security pressure is normalized by participating repos rather than raw finding totals",
                "repos with no workgraph are capped at watch unless stronger risk signals are present",
            ],
        },
        "config": {
            "dirty_repo_blocks_auto_mutation": bool(cfg.get("dirty_repo_blocks_auto_mutation", True)),
            "require_metric_evidence": bool(cfg.get("require_metric_evidence", True)),
        },
    }


def apply_northstardrift(
    snapshot: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    northstar = compute_northstardrift(snapshot, previous=previous, config=config)
    repo_map = {
        str(row.get("repo") or ""): row
        for row in northstar.get("repo_scores", [])
        if isinstance(row, dict) and str(row.get("repo") or "").strip()
    }
    repos = snapshot.get("repos")
    if isinstance(repos, list):
        for repo in repos:
            if not isinstance(repo, dict):
                continue
            row = repo_map.get(str(repo.get("name") or ""))
            if row:
                repo["northstar"] = row
    snapshot["northstardrift"] = northstar
    return northstar


def artifacts_root(*, service_dir: Path, central_repo: Path | None) -> Path:
    if central_repo is not None:
        return central_repo / "northstardrift"
    return service_dir / "northstardrift"


def load_previous_northstardrift(*, service_dir: Path, central_repo: Path | None) -> dict[str, Any]:
    root = artifacts_root(service_dir=service_dir, central_repo=central_repo)
    current = root / "current.json"
    if not current.exists():
        return {}
    return _read_json(current)


def read_northstardrift_history(
    *,
    service_dir: Path,
    central_repo: Path | None,
    current: dict[str, Any] | None = None,
    limit: int = 18,
) -> dict[str, Any]:
    root = artifacts_root(service_dir=service_dir, central_repo=central_repo)
    ledger = root / "ledgers" / "effectiveness.jsonl"
    rows = _read_recent_jsonl(ledger, limit=max(1, int(limit)))
    points: list[dict[str, Any]] = []
    for row in rows:
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        axes = row.get("axes") if isinstance(row.get("axes"), dict) else {}
        points.append(
            {
                "generated_at": str(row.get("generated_at") or ""),
                "overall_score": float(summary.get("overall_score") or 0.0),
                "axes": {
                    name: {"score": float((axes.get(name) or {}).get("score") or 0.0)}
                    for name in AXIS_NAMES
                },
            }
        )

    current_row = current if isinstance(current, dict) else None
    current_ts = str((current_row or {}).get("generated_at") or "")
    if current_row and current_ts and (not points or str(points[-1].get("generated_at") or "") != current_ts):
        summary = current_row.get("summary") if isinstance(current_row.get("summary"), dict) else {}
        axes = current_row.get("axes") if isinstance(current_row.get("axes"), dict) else {}
        points.append(
            {
                "generated_at": current_ts,
                "overall_score": float(summary.get("overall_score") or 0.0),
                "axes": {
                    name: {"score": float((axes.get(name) or {}).get("score") or 0.0)}
                    for name in AXIS_NAMES
                },
            }
        )
    points = points[-max(1, int(limit)) :]
    return {
        "points": points,
        "summary": {
            "count": len(points),
            "window": "recent",
        },
    }


def emit_northstar_review_tasks(
    *,
    snapshot: dict[str, Any],
    report: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = default_northstardrift_cfg()
    if isinstance(config, dict):
        cfg.update({key: value for key, value in config.items()})

    out: dict[str, Any] = {
        "enabled": True,
        "attempted": 0,
        "created": 0,
        "existing": 0,
        "skipped": 0,
        "errors": [],
        "tasks": [],
    }
    repos = snapshot.get("repos") if isinstance(snapshot.get("repos"), list) else []
    repo_map = {
        str(repo.get("name") or ""): repo
        for repo in repos
        if isinstance(repo, dict) and str(repo.get("name") or "").strip()
    }
    review_rows = report.get("recommended_reviews") if isinstance(report.get("recommended_reviews"), list) else []
    per_repo_counts: dict[str, int] = {}
    per_repo_limit = max(1, int(cfg.get("max_review_tasks_per_repo") or 2))

    for row in review_rows:
        if not isinstance(row, dict):
            continue
        repo_name = str(row.get("repo") or "").strip()
        if not repo_name:
            out["skipped"] = int(out["skipped"]) + 1
            continue
        if per_repo_counts.get(repo_name, 0) >= per_repo_limit:
            out["skipped"] = int(out["skipped"]) + 1
            continue
        repo = repo_map.get(repo_name)
        if not isinstance(repo, dict):
            out["errors"].append(f"{repo_name}: repo missing from snapshot")
            continue
        if bool(cfg.get("dirty_repo_blocks_auto_mutation", True)) and bool(repo.get("git_dirty")):
            out["skipped"] = int(out["skipped"]) + 1
            out["tasks"].append({"repo": repo_name, "task_id": "", "status": "skipped-dirty"})
            continue
        repo_path = Path(str(repo.get("path") or "")).expanduser()
        wg_dir = repo_path / ".workgraph"
        if not wg_dir.exists():
            out["errors"].append(f"{repo_name}: .workgraph missing")
            continue
        fingerprint = str(row.get("fingerprint") or "").strip()
        if not fingerprint:
            out["skipped"] = int(out["skipped"]) + 1
            continue
        task_id = f"northstardrift-{fingerprint[:14]}"
        title = f"northstardrift: {str(row.get('severity') or 'medium')} {str(row.get('category') or 'repo-attention')}"
        prompt = str(row.get("model_prompt") or "")
        codex_prompt = str(row.get("codex_prompt") or "")
        desc = (
            "North-star effectiveness review task.\n\n"
            f"Finding: {row.get('title')}\n"
            f"Severity: {row.get('severity')}\n"
            f"Evidence: {row.get('evidence')}\n"
            f"Recommendation: {row.get('recommendation')}\n"
            f"North-star score: {row.get('score')}\n\n"
            f"Suggested Claude prompt:\n{prompt}\n\n"
            f"Suggested Codex prompt:\n{codex_prompt}\n"
        )
        out["attempted"] = int(out["attempted"]) + 1
        show_rc, _, show_err = _run_cmd(
            ["wg", "--dir", str(wg_dir), "show", task_id, "--json"],
            cwd=repo_path,
            timeout=20.0,
        )
        if show_rc == 0:
            out["existing"] = int(out["existing"]) + 1
            per_repo_counts[repo_name] = per_repo_counts.get(repo_name, 0) + 1
            out["tasks"].append({"repo": repo_name, "task_id": task_id, "status": "existing"})
            continue

        add_rc, add_out, add_err = _run_cmd(
            [
                "wg",
                "--dir",
                str(wg_dir),
                "add",
                title,
                "--id",
                task_id,
                "-d",
                desc,
                "-t",
                "drift",
                "-t",
                "northstardrift",
                "-t",
                "review",
            ],
            cwd=repo_path,
            timeout=30.0,
        )
        if add_rc == 0:
            out["created"] = int(out["created"]) + 1
            per_repo_counts[repo_name] = per_repo_counts.get(repo_name, 0) + 1
            out["tasks"].append({"repo": repo_name, "task_id": task_id, "status": "created"})
        else:
            err = (add_err or add_out or show_err or "").strip()
            out["errors"].append(f"{repo_name}: could not create {task_id}: {err[:220]}")

    out["tasks"] = list(out.get("tasks") or [])[:80]
    out["errors"] = list(out.get("errors") or [])[:80]
    return out


def write_northstardrift_artifacts(
    *,
    service_dir: Path,
    central_repo: Path | None,
    northstardrift: dict[str, Any],
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = default_northstardrift_cfg()
    if isinstance(config, dict):
        cfg.update({key: value for key, value in config.items()})
    root = artifacts_root(service_dir=service_dir, central_repo=central_repo)
    current_path = root / "current.json"
    effectiveness_path = root / "ledgers" / "effectiveness.jsonl"
    regressions_path = root / "ledgers" / "regressions.jsonl"
    interventions_path = root / "ledgers" / "interventions.jsonl"
    generated_at = str(northstardrift.get("generated_at") or "")
    day_token = generated_at[:10] if len(generated_at) >= 10 else "unknown-date"
    daily_path = root / "daily" / f"{day_token}.json"

    _write_json(current_path, northstardrift)
    _write_json(daily_path, northstardrift)

    if _should_append(
        effectiveness_path,
        generated_at,
        min_interval_seconds=int(cfg.get("effectiveness_ledger_min_interval_seconds") or 3600),
    ):
        _append_jsonl(effectiveness_path, northstardrift)

    regressions = northstardrift.get("regressions")
    if isinstance(regressions, list) and regressions and _should_append(
        regressions_path,
        generated_at,
        min_interval_seconds=int(cfg.get("regression_ledger_min_interval_seconds") or 3600),
    ):
        _append_jsonl(
            regressions_path,
            {
                "generated_at": generated_at,
                "regressions": regressions,
            },
        )

    interventions = northstardrift.get("operator_prompts")
    if isinstance(interventions, list) and interventions and _should_append(
        interventions_path,
        generated_at,
        min_interval_seconds=int(cfg.get("intervention_ledger_min_interval_seconds") or 1800),
    ):
        _append_jsonl(
            interventions_path,
            {
                "generated_at": generated_at,
                "operator_prompts": interventions,
            },
        )

    return {
        "root": str(root),
        "current_path": str(current_path),
        "effectiveness_ledger_path": str(effectiveness_path),
        "regressions_ledger_path": str(regressions_path),
        "interventions_ledger_path": str(interventions_path),
        "daily_path": str(daily_path),
    }
