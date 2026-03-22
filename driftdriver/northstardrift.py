from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from driftdriver.drift_task_guard import guarded_add_drift_task
from driftdriver.governancedrift import score_operational_health


AXIS_NAMES = (
    "continuity",
    "autonomy",
    "product_quality",
    "coordination",
    "self_improvement",
    "operational_health",
)

AXIS_WEIGHTS: dict[str, float] = {
    "continuity": 0.22,
    "autonomy": 0.18,
    "product_quality": 0.18,
    "coordination": 0.18,
    "self_improvement": 0.12,
    "operational_health": 0.12,
}


def _default_targets_cfg() -> dict[str, Any]:
    return {
        "overall": 82.0,
        "axes": {
            "continuity": 85.0,
            "autonomy": 82.0,
            "product_quality": 80.0,
            "coordination": 78.0,
            "self_improvement": 76.0,
            "operational_health": 75.0,
        },
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
        "weekly_rollup_weeks": 8,
        "latent_repo_floor_score": 68.0,
        "target_gap_watch": 5.0,
        "target_gap_critical": 12.0,
        "dirty_repo_review_task_mode": "workgraph-only",
        "alignment": {
            "statement": "",
            "keywords": [],
            "anti_patterns": [],
            "last_reviewed": "",
            "review_interval_days": 30,
            "alignment_model": "haiku",
            "alignment_threshold_proceed": 0.7,
            "alignment_threshold_pause": 0.4,
            "decision_category": "alignment",
        },
        "targets": _default_targets_cfg(),
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


def _window_seconds(label: str) -> int:
    raw = str(label or "").strip().lower()
    if raw.endswith("h"):
        try:
            return max(1, int(float(raw[:-1]) * 3600))
        except Exception:
            return 0
    if raw.endswith("d"):
        try:
            return max(1, int(float(raw[:-1]) * 86400))
        except Exception:
            return 0
    return 0


def _history_point(row: dict[str, Any]) -> dict[str, Any]:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    axes = row.get("axes") if isinstance(row.get("axes"), dict) else {}
    return {
        "generated_at": str(row.get("generated_at") or ""),
        "overall_score": float(summary.get("overall_score") or row.get("overall_score") or 0.0),
        "axes": {
            name: {"score": float((axes.get(name) or {}).get("score") or ((row.get("axes") or {}).get(name) or {}).get("score") or 0.0)}
            for name in AXIS_NAMES
        },
    }


def _merge_history_points(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for group in groups:
        for row in group:
            if not isinstance(row, dict):
                continue
            ts = str(row.get("generated_at") or "").strip()
            if not ts:
                continue
            merged[ts] = row
    return sorted(merged.values(), key=lambda row: str(row.get("generated_at") or ""))


def _read_daily_history(root: Path, *, limit: int) -> list[dict[str, Any]]:
    daily_dir = root / "daily"
    if not daily_dir.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(daily_dir.glob("*.json"))[-max(1, int(limit)) :]:
        payload = _read_json(path)
        if not payload:
            continue
        point = _history_point(payload)
        point["day"] = path.stem
        rows.append(point)
    return rows


def _aggregate_weekly_history(points: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    buckets: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for row in points:
        ts = _parse_iso(row.get("generated_at"))
        if ts is None:
            continue
        iso = ts.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        buckets.setdefault(key, []).append((ts, row))

    weekly_rows: list[dict[str, Any]] = []
    for key in sorted(buckets):
        entries = buckets[key]
        if not entries:
            continue
        entries.sort(key=lambda item: item[0])
        overall_values = [float((item[1] or {}).get("overall_score") or 0.0) for item in entries]
        axis_values = {
            name: [
                float((((item[1] or {}).get("axes") or {}).get(name) or {}).get("score") or 0.0)
                for item in entries
            ]
            for name in AXIS_NAMES
        }
        weekly_rows.append(
            {
                "week": key,
                "start_date": entries[0][0].date().isoformat(),
                "end_date": entries[-1][0].date().isoformat(),
                "sample_count": len(entries),
                "overall_score": _clamp_score(sum(overall_values) / max(1, len(overall_values))),
                "axes": {
                    name: {"score": _clamp_score(sum(values) / max(1, len(values)))}
                    for name, values in axis_values.items()
                },
            }
        )

    weekly_rows = weekly_rows[-max(1, int(limit)) :]
    for idx, row in enumerate(weekly_rows):
        previous_score = float(weekly_rows[idx - 1].get("overall_score") or 0.0) if idx > 0 else None
        trend, delta = _trend(float(row.get("overall_score") or 0.0), previous_score)
        row["trend"] = trend
        row["delta"] = delta
    return weekly_rows


def _compute_window_trends(points: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for row in points:
        ts = _parse_iso(row.get("generated_at"))
        if ts is None:
            continue
        dated.append((ts, row))
    dated.sort(key=lambda item: item[0])
    if not dated:
        return {}

    latest_dt, latest_row = dated[-1]
    out: dict[str, dict[str, Any]] = {}
    for label in ("24h", "7d", "30d"):
        seconds = _window_seconds(label)
        cutoff = latest_dt - timedelta(seconds=seconds)
        candidates = [(ts, row) for ts, row in dated if ts >= cutoff]
        baseline_dt, baseline_row = (candidates[0] if len(candidates) >= 2 else dated[0])
        latest_score = float(latest_row.get("overall_score") or 0.0)
        baseline_score = float(baseline_row.get("overall_score") or 0.0)
        trend, delta = _trend(latest_score, baseline_score)
        out[label] = {
            "label": label,
            "trend": trend,
            "delta": delta,
            "coverage": "full" if baseline_dt <= cutoff else "partial",
            "point_count": len(candidates) if candidates else len(dated),
            "baseline_at": baseline_dt.isoformat(),
            "latest_at": latest_dt.isoformat(),
            "baseline_score": baseline_score,
            "latest_score": latest_score,
            "axis_deltas": {
                name: round(
                    float((((latest_row.get("axes") or {}).get(name) or {}).get("score") or 0.0))
                    - float((((baseline_row.get("axes") or {}).get(name) or {}).get("score") or 0.0)),
                    1,
                )
                for name in AXIS_NAMES
            },
        }
    return out


def _configured_targets(config: dict[str, Any]) -> dict[str, Any]:
    defaults = _default_targets_cfg()
    raw = config.get("targets") if isinstance(config.get("targets"), dict) else {}
    axes_raw = raw.get("axes") if isinstance(raw.get("axes"), dict) else raw
    targets = {
        "overall": defaults["overall"],
        "axes": dict(defaults["axes"]),
    }
    try:
        targets["overall"] = float(raw.get("overall", targets["overall"]))
    except Exception:
        targets["overall"] = defaults["overall"]
    for name in AXIS_NAMES:
        try:
            targets["axes"][name] = float(axes_raw.get(name, targets["axes"][name]))
        except Exception:
            targets["axes"][name] = defaults["axes"][name]
    return targets


def _evaluate_target(
    name: str,
    *,
    score: float,
    target: float,
    watch_gap: float,
    critical_gap: float,
) -> dict[str, Any]:
    gap = round(score - target, 1)
    if gap >= 0.0:
        status = "met"
    elif gap <= (-1.0 * critical_gap):
        status = "critical-gap"
    elif gap <= (-1.0 * watch_gap):
        status = "watch-gap"
    else:
        status = "near-target"
    return {
        "name": name,
        "score": score,
        "target": target,
        "gap": gap,
        "status": status,
    }


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


def _workgraph_review_mutation_allowed(repo_path: Path) -> bool:
    rc, out, _ = _run_cmd(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo_path, timeout=10.0)
    if rc != 0 or str(out).strip() != "true":
        return False
    for candidate in (".workgraph/graph.jsonl", ".workgraph"):
        rc, _, _ = _run_cmd(["git", "check-ignore", "-q", candidate], cwd=repo_path, timeout=10.0)
        if rc == 0:
            return True
    return False


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
    repo_north_star = repo.get("repo_north_star") if isinstance(repo.get("repo_north_star"), dict) else {}
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
    if not bool(repo_north_star.get("present")):
        reasons.append("repo north star missing")
    elif str(repo_north_star.get("status") or "") == "weak":
        reasons.append("repo north star weak")
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
    repo_north_star = repo.get("repo_north_star") if isinstance(repo.get("repo_north_star"), dict) else {}
    if not bool(repo.get("exists")):
        penalty += 85
    elif not bool(repo.get("workgraph_exists")):
        penalty += 18 if latent_repo else 28
    if not bool(repo_north_star.get("present")):
        penalty += 8.0
    elif str(repo_north_star.get("status") or "") == "weak":
        penalty += 3.0
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
    largest_gap: dict[str, Any] | None,
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
    if isinstance(largest_gap, dict) and str(largest_gap.get("name") or "").strip():
        gap = float(largest_gap.get("gap") or 0.0)
        if gap < 0.0:
            parts.append(
                f"Largest target gap: {largest_gap['name'].replace('_', ' ')} is {abs(gap):.1f} below target."
            )
    if isinstance(top_prompt, dict) and str(top_prompt.get("repo") or "").strip():
        parts.append(f"Next operator focus: {top_prompt['repo']}.")
    return " ".join(parts)


def compute_alignment_score(task: dict[str, Any], alignment_config: dict[str, Any]) -> float:
    """Score how well a task aligns with the declared North Star.

    Returns a float in [0.0, 1.0].  0.5 is neutral (empty config or no signal).
    """
    keywords: list[str] = alignment_config.get("keywords") or []
    anti_patterns: list[str] = alignment_config.get("anti_patterns") or []

    if not keywords and not anti_patterns:
        return 0.5

    text = " ".join([
        str(task.get("title") or ""),
        str(task.get("description") or ""),
    ]).lower()

    text_words = text.split()

    def _fuzzy_match(term: str, corpus: str, corpus_words: list[str]) -> bool:
        """Check if term appears in corpus, allowing prefix/stem overlap."""
        t = term.lower()
        if t in corpus:
            return True
        # Check if any word in the corpus shares a common stem (prefix match)
        for w in corpus_words:
            if w.startswith(t) or t.startswith(w):
                if min(len(w), len(t)) >= 3:  # avoid trivial prefix matches
                    return True
        return False

    # Keyword hits
    if keywords:
        keyword_hits = sum(1 for kw in keywords if _fuzzy_match(kw, text, text_words))
        alignment_ratio = keyword_hits / len(keywords)
    else:
        alignment_ratio = 0.5

    # Anti-pattern penalty (0.2 per hit, capped at 0.5)
    anti_hits = sum(1 for ap in anti_patterns if _fuzzy_match(ap, text, text_words))
    penalty = min(anti_hits * 0.2, 0.5)

    # When no signal in either direction, return neutral
    if keyword_hits == 0 and anti_hits == 0 and keywords:
        return 0.5

    score = max(0.0, min(1.0, alignment_ratio - penalty))
    return score


def _score_alignment_with_llm(
    statement: str,
    tasks: list[dict[str, Any]],
    model: str = "haiku",
    timeout: int = 30,
) -> tuple[float, list[str]]:
    """Score alignment using a Haiku LLM call.

    Returns (score 0-100, findings list).
    Raises RuntimeError on failure so the caller can fall back to keyword scoring.

    Security note: ``--dangerously-skip-permissions`` is intentional here.
    This subprocess call is read-only intelligence work — it sends a prompt
    containing only the North Star statement and task titles (no credentials,
    no filesystem paths, no write-capable tools).  The flag suppresses the
    interactive permission gate in non-terminal contexts (e.g. daemon mode).
    Risk accepted: the prompt is bounded by ``tasks[:20]`` titles + the repo
    north star text; neither source contains secrets.  The fallback to keyword
    scoring on any failure further limits blast radius.
    """
    if not tasks:
        return 50.0, []

    task_summary = "\n".join(
        f"- {str(t.get('title') or t.get('id') or 'unknown')}"
        for t in tasks[:20]
    )

    prompt = (
        f"You are evaluating how well a set of active tasks aligns with a project's North Star goal.\n\n"
        f"North Star:\n{statement}\n\n"
        f"Active Tasks:\n{task_summary}\n\n"
        f"Evaluate alignment and respond with JSON only:\n"
        f'{{"score": <integer 0-100 where 100 is perfect alignment>, '
        f'"findings": [<specific misalignment observations as strings, empty list if well aligned>]}}'
    )

    result = subprocess.run(
        ["claude", "--model", model, "--print", "--dangerously-skip-permissions"],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(f"LLM alignment call failed (exit {result.returncode}): {result.stderr[:200]}")

    raw = result.stdout.strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.lstrip("`").removeprefix("json").strip().rstrip("`").strip()
    # Find first { to last } in case of surrounding text
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]

    parsed = json.loads(raw)
    score = float(parsed.get("score", 50))
    findings = [str(f) for f in (parsed.get("findings") or [])]
    return max(0.0, min(100.0, score)), findings


def compute_northstardrift(
    snapshot: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
    alignment_config: dict[str, Any] | None = None,
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
    agency_eval_inputs = snapshot.get("agency_eval_inputs") if isinstance(snapshot.get("agency_eval_inputs"), dict) else {}

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
    participating_repos_active = sum(
        1 for repo in repos
        if isinstance(repo, dict) and _is_participating_repo(repo)
        and str(repo.get("lifecycle", "active")) == "active"
    )
    latent_repos = sum(1 for repo in repos if isinstance(repo, dict) and _is_latent_repo(repo))
    reporting_repos = sum(1 for repo in repos if isinstance(repo, dict) and bool(repo.get("reporting")))
    north_star_present_repos = sum(
        1
        for repo in repos
        if isinstance(repo, dict)
        and isinstance(repo.get("repo_north_star"), dict)
        and bool((repo.get("repo_north_star") or {}).get("present"))
    )
    missing_north_star_repos = sum(
        1
        for repo in repos
        if isinstance(repo, dict)
        and not bool(((repo.get("repo_north_star") or {}) if isinstance(repo.get("repo_north_star"), dict) else {}).get("present"))
    )
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
    product_quality = _clamp_score(
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
    north_star_coverage = _ratio_score(north_star_present_repos, total_repos, default=0.0)
    throughput_score = _clamp_score(min(100.0, 55.0 + (len(upstream_candidates) * 10.0) + (10.0 if bool(updates.get("has_updates")) else 0.0) + (10.0 if bool(updates.get("has_discoveries")) else 0.0)))
    plan_integrity_coverage = _penalty_inverse((missing_dependencies * 9.0) + (blocked_total * 3.0) + (stale_active_total * 4.0))
    agency_eval_score_raw = agency_eval_inputs.get("eval_score")
    agency_eval_contribution = float(agency_eval_score_raw) if isinstance(agency_eval_score_raw, (int, float)) else 0.0
    self_improvement = _clamp_score(
        (0.25 * improvement_change)
        + (0.20 * rollout_coverage)
        + (0.20 * throughput_score)
        + (0.20 * north_star_coverage)
        + (0.35 * plan_integrity_coverage)
        + (0.15 * agency_eval_contribution)  # agency evaluation feedback
    )

    op_health_inputs = overview.get("op_health_inputs") if isinstance(overview.get("op_health_inputs"), dict) else {}
    operational_health = _clamp_score(
        score_operational_health(
            zombie_ratio=float(op_health_inputs.get("zombie_ratio", 0.0)),
            failed_abandoned_ratio=float(op_health_inputs.get("failed_abandoned_ratio", 0.0)),
            posture_alignment_ratio=float(op_health_inputs.get("posture_alignment_ratio", 1.0)),
            abandoned_age_pressure=float(op_health_inputs.get("abandoned_age_pressure", 0.0)),
        )
    )

    axis_raw = {
        "continuity": continuity,
        "autonomy": autonomy,
        "product_quality": product_quality,
        "coordination": coordination,
        "self_improvement": self_improvement,
        "operational_health": operational_health,
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

    targets_cfg = _configured_targets(cfg)
    watch_gap = max(1.0, float(cfg.get("target_gap_watch") or 5.0))
    critical_gap = max(watch_gap, float(cfg.get("target_gap_critical") or 12.0))
    axis_targets = {
        name: _evaluate_target(
            name,
            score=float(axes[name].get("score") or 0.0),
            target=float(targets_cfg["axes"][name]),
            watch_gap=watch_gap,
            critical_gap=critical_gap,
        )
        for name in AXIS_NAMES
    }
    for name in AXIS_NAMES:
        axes[name]["target"] = axis_targets[name]["target"]
        axes[name]["target_gap"] = axis_targets[name]["gap"]
        axes[name]["target_status"] = axis_targets[name]["status"]

    overall_score = _clamp_score(sum(axis_raw[name] * AXIS_WEIGHTS[name] for name in AXIS_NAMES))
    previous_overall = None
    if isinstance(previous, dict):
        try:
            previous_overall = float((previous.get("summary") or {}).get("overall_score"))
        except Exception:
            previous_overall = None
    overall_trend, overall_delta = _trend(overall_score, previous_overall)
    overall_tier = _tier(overall_score)
    overall_target = _evaluate_target(
        "overall",
        score=overall_score,
        target=float(targets_cfg["overall"]),
        watch_gap=watch_gap,
        critical_gap=critical_gap,
    )

    target_rows = [overall_target, *[axis_targets[name] for name in AXIS_NAMES]]
    priority_gaps = [
        row
        for row in sorted(target_rows, key=lambda item: (float(item.get("gap") or 0.0), str(item.get("name") or "")))
        if float(row.get("gap") or 0.0) < 0.0
    ][:4]
    target_summary = {
        "met": sum(1 for row in target_rows if str(row.get("status")) == "met"),
        "near_target": sum(1 for row in target_rows if str(row.get("status")) == "near-target"),
        "watch_gap": sum(1 for row in target_rows if str(row.get("status")) == "watch-gap"),
        "critical_gap": sum(1 for row in target_rows if str(row.get("status")) == "critical-gap"),
        "largest_gap": priority_gaps[0] if priority_gaps else None,
    }

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

    for repo in repos:
        if not isinstance(repo, dict):
            continue
        repo_name = str(repo.get("name") or "").strip()
        if not repo_name or not bool(repo.get("workgraph_exists")):
            continue
        repo_north_star = repo.get("repo_north_star") if isinstance(repo.get("repo_north_star"), dict) else {}
        if bool(repo_north_star.get("present")):
            continue
        fingerprint = _fingerprint([repo_name, "missing-repo-north-star"])
        recommended_reviews.append(
            {
                "fingerprint": fingerprint,
                "repo": repo_name,
                "severity": "high",
                "category": "missing-repo-north-star",
                "title": f"Canonical repo North Star missing for {repo_name}",
                "evidence": "No canonical repo North Star signal found in README/docs/plans.",
                "recommendation": "Evaluate the repo purpose, current workgraph, and dependency context; draft a concise repo North Star for human approval and add it to a canonical doc.",
                "model_prompt": (
                    f"In `{repo_name}`, evaluate the repo purpose, current workgraph, active plans, and inter-repo dependencies. "
                    "Draft a concise but nuanced repo North Star for human approval, propose the canonical doc location, and emit exact workgraph tasks to adopt it without disrupting active work."
                ),
                "codex_prompt": (
                    f"In repo {repo_name}, determine the missing canonical North Star. Read README/docs/plans and current workgraph intent, draft a proposed North Star for human approval, and create the smallest safe task/doc plan to adopt it."
                ),
                "score": next(
                    (
                        row.get("score")
                        for row in repo_scores
                        if isinstance(row, dict) and str(row.get("repo") or "") == repo_name
                    ),
                    0.0,
                ),
                "human_approval_required": True,
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
        largest_gap=priority_gaps[0] if priority_gaps else None,
    )

    # -- Alignment v2 layer --------------------------------------------------
    alignment_configured = (
        isinstance(alignment_config, dict)
        and bool(str(alignment_config.get("statement") or "").strip())
    )
    if alignment_configured:
        assert alignment_config is not None  # narrow type for mypy
        all_tasks: list[dict[str, Any]] = []
        for repo in repos:
            if isinstance(repo, dict):
                all_tasks.extend(repo.get("in_progress") or [])
                all_tasks.extend(repo.get("ready") or [])

        # Try LLM scoring first; fall back to keyword matching on any failure.
        llm_model = str(alignment_config.get("alignment_model") or "haiku")
        alignment_findings: list[str] = []
        llm_used = False
        try:
            statement = str(alignment_config.get("statement") or "")
            raw_score, alignment_findings = _score_alignment_with_llm(
                statement, all_tasks, model=llm_model
            )
            # LLM returns 0-100; normalize to 0-1 for backwards-compat output.
            overall_alignment = raw_score / 100.0
            llm_used = True
        except Exception:
            # Keyword fallback
            task_scores_kw: list[dict[str, Any]] = []
            for t in all_tasks:
                ts_kw = compute_alignment_score(t, alignment_config)
                task_scores_kw.append({"task_id": str(t.get("id") or ""), "score": ts_kw})
            overall_alignment = (
                sum(ts["score"] for ts in task_scores_kw) / len(task_scores_kw)
                if task_scores_kw
                else 0.5
            )

        task_scores: list[dict[str, Any]] = []
        if not llm_used:
            for t in all_tasks:
                ts_val = compute_alignment_score(t, alignment_config)
                task_scores.append({"task_id": str(t.get("id") or ""), "score": ts_val})

        alignment_section: dict[str, Any] = {
            "overall_alignment": round(overall_alignment, 4),
            "configured": True,
            "task_scores": task_scores,
            "findings": alignment_findings,
            "llm_used": llm_used,
        }
    else:
        alignment_section = {
            "overall_alignment": 0.5,
            "configured": False,
            "task_scores": [],
            "findings": [],
            "llm_used": False,
        }

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
            "overall_target": overall_target["target"],
            "overall_target_gap": overall_target["gap"],
            "overall_target_status": overall_target["status"],
            "narrative": narrative,
        },
        "axes": axes,
        "repo_scores": repo_scores,
        "counts": {
            "tracked_repos": total_repos,
            "participating_repos": participating_repos,
            "participating_repos_active": participating_repos_active,
            "reporting_repos": reporting_repos,
            "repos_with_north_star": north_star_present_repos,
            "repos_missing_north_star": missing_north_star_repos,
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
        "targets": {
            "overall": overall_target,
            "axes": axis_targets,
            "summary": target_summary,
            "priority_gaps": priority_gaps,
        },
        "alignment": alignment_section,
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
            "dirty_repo_review_task_mode": str(cfg.get("dirty_repo_review_task_mode") or "workgraph-only"),
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
    weekly_limit: int = 8,
) -> dict[str, Any]:
    root = artifacts_root(service_dir=service_dir, central_repo=central_repo)
    ledger = root / "ledgers" / "effectiveness.jsonl"
    rows = _read_recent_jsonl(ledger, limit=max(24, int(limit) * 12))
    points = [_history_point(row) for row in rows if isinstance(row, dict)]

    current_row = current if isinstance(current, dict) else None
    current_ts = str((current_row or {}).get("generated_at") or "")
    if current_row and current_ts and (not points or str(points[-1].get("generated_at") or "") != current_ts):
        points.append(_history_point(current_row))
    daily_points = _read_daily_history(root, limit=max(14, int(limit) * 2))
    merged_points = _merge_history_points(points, daily_points)
    recent_points = merged_points[-max(1, int(limit)) :]
    weekly_points = _aggregate_weekly_history(daily_points or merged_points, limit=max(4, int(weekly_limit)))
    windows = _compute_window_trends(merged_points)
    return {
        "points": recent_points,
        "daily_points": daily_points,
        "weekly_points": weekly_points,
        "windows": windows,
        "summary": {
            "count": len(recent_points),
            "daily_count": len(daily_points),
            "weekly_count": len(weekly_points),
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
    dirty_mode = str(cfg.get("dirty_repo_review_task_mode") or "workgraph-only").strip().lower()
    if dirty_mode not in {"block", "workgraph-only", "allow"}:
        dirty_mode = "workgraph-only"

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
        repo_path = Path(str(repo.get("path") or "")).expanduser()
        wg_dir = repo_path / ".workgraph"
        if not wg_dir.exists():
            out["errors"].append(f"{repo_name}: .workgraph missing")
            continue
        if bool(cfg.get("dirty_repo_blocks_auto_mutation", True)) and bool(repo.get("git_dirty")):
            allow_dirty = dirty_mode == "allow" or (
                dirty_mode == "workgraph-only" and _workgraph_review_mutation_allowed(repo_path)
            )
            if not allow_dirty:
                out["skipped"] = int(out["skipped"]) + 1
                status = "skipped-dirty-workgraph" if dirty_mode == "workgraph-only" else "skipped-dirty"
                out["tasks"].append({"repo": repo_name, "task_id": "", "status": status})
                continue
        fingerprint = str(row.get("fingerprint") or "").strip()
        if not fingerprint:
            out["skipped"] = int(out["skipped"]) + 1
            continue
        task_id = f"northstardrift-{fingerprint[:14]}"
        title = f"northstardrift: {str(row.get('severity') or 'medium')} {str(row.get('category') or 'repo-attention')}"
        prompt = str(row.get("model_prompt") or "")
        codex_prompt = str(row.get("codex_prompt") or "")
        approval = "yes" if bool(row.get("human_approval_required")) else "no"
        desc = (
            "North-star effectiveness review task.\n\n"
            f"Finding: {row.get('title')}\n"
            f"Severity: {row.get('severity')}\n"
            f"Evidence: {row.get('evidence')}\n"
            f"Recommendation: {row.get('recommendation')}\n"
            f"North-star score: {row.get('score')}\n\n"
            f"Human approval required: {approval}\n\n"
            f"Suggested Claude prompt:\n{prompt}\n\n"
            f"Suggested Codex prompt:\n{codex_prompt}\n"
        )
        out["attempted"] = int(out["attempted"]) + 1
        result = guarded_add_drift_task(
            wg_dir=wg_dir,
            task_id=task_id,
            title=title,
            description=desc,
            lane_tag="northstardrift",
            extra_tags=["review"],
            cwd=repo_path,
        )
        if result == "created":
            out["created"] = int(out["created"]) + 1
            per_repo_counts[repo_name] = per_repo_counts.get(repo_name, 0) + 1
            out["tasks"].append({"repo": repo_name, "task_id": task_id, "status": "created"})
        elif result == "existing":
            out["existing"] = int(out["existing"]) + 1
            per_repo_counts[repo_name] = per_repo_counts.get(repo_name, 0) + 1
            out["tasks"].append({"repo": repo_name, "task_id": task_id, "status": "existing"})
        elif result == "capped":
            out["skipped"] = int(out.get("skipped", 0)) + 1
            out["tasks"].append({"repo": repo_name, "task_id": task_id, "status": "capped"})
        else:
            out["errors"].append(f"{repo_name}: could not create {task_id}: {result}")

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


def _minimal_northstar_snapshot(project_dir: Path) -> dict[str, Any]:
    """Build a lightweight single-repo snapshot for lane-contract scoring."""
    repo_name = project_dir.name
    wg_dir = project_dir / ".workgraph"
    wg_exists = wg_dir.is_dir()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "overview": {},
        "repos": [
            {
                "name": repo_name,
                "path": str(project_dir),
                "exists": True,
                "workgraph_exists": wg_exists,
                "service_running": False,
                "reporting": False,
                "stalled": False,
                "missing_dependencies": 0,
                "blocked_open": 0,
                "stale_open": [],
                "stale_in_progress": [],
                "behind": 0,
                "git_dirty": False,
                "dirty_file_count": 0,
                "ready": [],
                "in_progress": [],
                "task_counts": {},
                "quality": {},
                "security": {},
                "repo_north_star": {},
                "errors": [],
            }
        ],
        "repo_dependency_overview": {},
        "factory": {},
        "supervisor": {},
        "updates": {},
        "upstream_candidates": [],
    }


def _load_alignment_config(project_dir: Path) -> dict[str, Any] | None:
    """Load alignment config from drift-policy.toml if present and configured."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python < 3.11
        return None

    policy_path = project_dir / ".workgraph" / "drift-policy.toml"
    if not policy_path.exists():
        return None

    try:
        data = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    ns_cfg = data.get("northstardrift")
    if not isinstance(ns_cfg, dict):
        return None

    alignment = ns_cfg.get("alignment")
    if not isinstance(alignment, dict):
        return None

    if not str(alignment.get("statement") or "").strip():
        return None

    return alignment


def run_as_lane(project_dir: Path) -> "LaneResult":
    """Run northstardrift and return results in the standard lane contract format.

    Wraps ``compute_northstardrift`` so that northstardrift can be invoked
    through the unified ``LaneResult`` interface used by all drift lanes.
    The adapter builds a minimal single-repo snapshot from *project_dir*
    and converts regressions, operator prompts, and recommended reviews
    into ``LaneFinding`` objects.
    """
    from driftdriver.lane_contract import LaneFinding, LaneResult

    try:
        snapshot = _minimal_northstar_snapshot(project_dir)
        alignment_config = _load_alignment_config(project_dir)
        report = compute_northstardrift(snapshot, alignment_config=alignment_config)
    except Exception as exc:
        return LaneResult(
            lane="northstardrift",
            findings=[LaneFinding(message=f"northstardrift error: {exc}", severity="error")],
            exit_code=1,
            summary=f"northstardrift failed: {exc}",
        )

    findings: list[LaneFinding] = []

    # Map alignment to findings when configured and below threshold
    alignment_data = report.get("alignment") if isinstance(report.get("alignment"), dict) else {}
    if alignment_data.get("configured"):
        threshold = float(
            (alignment_config or {}).get("alignment_threshold_proceed")
            or default_northstardrift_cfg()["alignment"]["alignment_threshold_proceed"]
        )
        overall_alignment = float(alignment_data.get("overall_alignment") or 0.0)
        if overall_alignment < threshold:
            findings.append(LaneFinding(
                message=f"alignment score {overall_alignment:.2f} is below threshold {threshold:.2f}",
                severity="warning",
                tags=["alignment", "low-alignment"],
            ))

    # Map regressions to findings
    for reg in report.get("regressions", []):
        if not isinstance(reg, dict):
            continue
        kind = str(reg.get("kind") or "unknown")
        summary_text = str(reg.get("summary") or "")
        findings.append(LaneFinding(
            message=summary_text,
            severity="warning",
            tags=["regression", kind],
        ))

    # Map operator prompts to findings
    for prompt in report.get("operator_prompts", []):
        if not isinstance(prompt, dict):
            continue
        priority = str(prompt.get("priority") or "medium")
        severity = "error" if priority == "high" else "warning"
        repo_name = str(prompt.get("repo") or "")
        reason = str(prompt.get("reason") or "")
        findings.append(LaneFinding(
            message=f"[{repo_name}] {reason}" if reason else f"[{repo_name}] north-star pressure",
            severity=severity,
            tags=["operator-prompt", repo_name],
        ))

    # Map recommended reviews to findings
    for review in report.get("recommended_reviews", []):
        if not isinstance(review, dict):
            continue
        category = str(review.get("category") or "review")
        sev = str(review.get("severity") or "medium")
        severity = "error" if sev == "high" else "warning"
        title = str(review.get("title") or "")
        findings.append(LaneFinding(
            message=title,
            severity=severity,
            tags=["review", category],
        ))

    summary_data = report.get("summary", {})
    overall_score = summary_data.get("overall_score", 0.0)
    overall_tier = summary_data.get("overall_tier", "unknown")
    summary_text = f"northstardrift: score={overall_score} tier={overall_tier}"

    exit_code = 1 if findings else 0
    return LaneResult(
        lane="northstardrift",
        findings=findings,
        exit_code=exit_code,
        summary=summary_text,
    )
