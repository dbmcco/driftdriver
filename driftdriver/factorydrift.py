from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.qadrift import emit_quality_review_tasks, run_program_quality_scan
from driftdriver.secdrift import emit_security_review_tasks, run_secdrift_scan


_DRIFTDRIVER_ROOT = Path(__file__).resolve().parents[1]


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    current = str(env.get("PYTHONPATH") or "").strip()
    parts = [str(_DRIFTDRIVER_ROOT)]
    if current:
        for chunk in current.split(os.pathsep):
            value = chunk.strip()
            if value and value not in parts:
                parts.append(value)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    return env


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_ts_for_file(iso_ts: str) -> str:
    return iso_ts.replace(":", "-").replace("+00:00", "Z")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 60.0,
) -> tuple[int, str, str]:
    def _invoke(actual_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            actual_cmd,
            cwd=str(cwd) if cwd else None,
            env=_subprocess_env(),
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
            users_root = Path("/Users")
            if users_root.exists():
                for discovered in users_root.glob("*/.cargo/bin/wg"):
                    candidates.append(str(discovered))
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


def _as_repo_map(repos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in repos:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        out[name] = row
    return out


def _attention_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    overview = snapshot.get("overview")
    if not isinstance(overview, dict):
        return {}
    rows = overview.get("attention_repos")
    if not isinstance(rows, list):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("repo") or "").strip()
        if name:
            out[name] = row
    return out


def _upstream_count(snapshot: dict[str, Any]) -> dict[str, int]:
    rows = snapshot.get("upstream_candidates")
    if not isinstance(rows, list):
        return {}
    out: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        repo = str(row.get("repo") or "").strip()
        if not repo:
            continue
        out[repo] = out.get(repo, 0) + 1
    return out


def _update_count(snapshot: dict[str, Any]) -> dict[str, int]:
    updates = snapshot.get("updates")
    if not isinstance(updates, dict):
        return {}
    raw = updates.get("raw")
    if not isinstance(raw, dict):
        return {}
    rows = raw.get("updates")
    if not isinstance(rows, list):
        return {}
    out: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        repo = str(row.get("tool") or "").strip()
        if not repo:
            continue
        out[repo] = out.get(repo, 0) + 1
    return out


def _repo_security_summary(repo: dict[str, Any]) -> dict[str, Any]:
    row = repo.get("security")
    return dict(row) if isinstance(row, dict) else {}


def _repo_quality_summary(repo: dict[str, Any]) -> dict[str, Any]:
    row = repo.get("quality")
    return dict(row) if isinstance(row, dict) else {}


def _repo_priority(
    *,
    repo: dict[str, Any],
    attention: dict[str, Any] | None,
    upstream_candidates: int,
    update_hits: int,
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []

    if isinstance(attention, dict):
        attn_score = max(0, int(attention.get("score") or 0))
        if attn_score > 0:
            score += min(40, attn_score)
            reasons.append(f"attention_score={attn_score}")

    activity = str(repo.get("activity_state") or "").strip().lower()
    if activity == "stalled":
        score += 30
        reasons.append("repo_stalled")
    elif activity == "error":
        score += 26
        reasons.append("repo_error")
    elif activity == "active":
        score += 8
        reasons.append("active_execution")

    if bool(repo.get("workgraph_exists")) and not bool(repo.get("service_running")):
        ready = repo.get("ready")
        in_progress = repo.get("in_progress")
        has_work = (isinstance(ready, list) and bool(ready)) or (isinstance(in_progress, list) and bool(in_progress))
        if has_work:
            score += 22
            reasons.append("service_down_with_work")

    missing_deps = max(0, int(repo.get("missing_dependencies") or 0))
    if missing_deps > 0:
        score += min(18, missing_deps * 5)
        reasons.append(f"missing_dependencies={missing_deps}")

    blocked_open = max(0, int(repo.get("blocked_open") or 0))
    if blocked_open > 0:
        score += min(14, blocked_open * 2)
        reasons.append(f"blocked_open={blocked_open}")

    stale_open = repo.get("stale_open")
    stale_in_progress = repo.get("stale_in_progress")
    stale_open_count = len(stale_open) if isinstance(stale_open, list) else 0
    stale_in_progress_count = len(stale_in_progress) if isinstance(stale_in_progress, list) else 0
    if stale_in_progress_count > 0:
        score += min(16, stale_in_progress_count * 4)
        reasons.append(f"aging_in_progress={stale_in_progress_count}")
    if stale_open_count > 0:
        score += min(12, stale_open_count * 2)
        reasons.append(f"aging_open={stale_open_count}")

    behind = max(0, int(repo.get("behind") or 0))
    if behind > 0:
        score += min(10, behind)
        reasons.append(f"behind_upstream={behind}")

    if upstream_candidates > 0:
        score += min(8, upstream_candidates * 2)
        reasons.append(f"upstream_candidates={upstream_candidates}")

    if update_hits > 0:
        score += min(8, update_hits * 2)
        reasons.append(f"upstream_deltas={update_hits}")

    if bool(repo.get("git_dirty")):
        score += 3
        reasons.append("dirty_worktree")

    sec = _repo_security_summary(repo)
    sec_critical = max(0, int(sec.get("critical") or 0))
    sec_high = max(0, int(sec.get("high") or 0))
    sec_total = max(0, int(sec.get("findings_total") or 0))
    if sec_critical > 0:
        score += min(34, sec_critical * 14)
        reasons.append(f"security_critical={sec_critical}")
    if sec_high > 0:
        score += min(22, sec_high * 8)
        reasons.append(f"security_high={sec_high}")
    if sec_total > 0 and sec_critical <= 0 and sec_high <= 0:
        score += min(10, sec_total * 2)
        reasons.append(f"security_findings={sec_total}")

    quality = _repo_quality_summary(repo)
    quality_high = max(0, int(quality.get("high") or 0))
    quality_score = max(0, int(quality.get("quality_score") or 100))
    quality_at_risk = bool(quality.get("at_risk"))
    if quality_at_risk:
        score += min(16, 8 + quality_high * 3)
        reasons.append("quality_at_risk")
    if quality_score < 85:
        score += min(10, max(1, (85 - quality_score) // 4))
        reasons.append(f"quality_score={quality_score}")

    return score, reasons


def resolve_repo_autonomy(policy: Any, repo_name: str) -> dict[str, Any]:
    defaults_raw = getattr(policy, "autonomy_default", {})
    defaults = dict(defaults_raw) if isinstance(defaults_raw, dict) else {}
    out = {
        "level": str(defaults.get("level") or "observe"),
        "can_push": bool(defaults.get("can_push")),
        "can_open_pr": bool(defaults.get("can_open_pr")),
        "can_merge": bool(defaults.get("can_merge")),
        "max_actions_per_cycle": max(0, int(defaults.get("max_actions_per_cycle") or 1)),
    }
    repo_rows = getattr(policy, "autonomy_repos", [])
    if not isinstance(repo_rows, list):
        return out
    target = str(repo_name or "").strip()
    for row in repo_rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("name") or "").strip() != target:
            continue
        out["level"] = str(row.get("level") or out["level"])
        out["can_push"] = bool(row.get("can_push", out["can_push"]))
        out["can_open_pr"] = bool(row.get("can_open_pr", out["can_open_pr"]))
        out["can_merge"] = bool(row.get("can_merge", out["can_merge"]))
        out["max_actions_per_cycle"] = max(0, int(row.get("max_actions_per_cycle") or out["max_actions_per_cycle"]))
        break
    return out


def _make_prompt(repo_name: str, body: str) -> str:
    return (
        f"In `{repo_name}`, run a bounded speedrift pass. "
        f"{body} Return: findings, the smallest safe next action, and exact wg task updates."
    )


def _plan_repo_actions(
    *,
    repo: dict[str, Any],
    repo_name: str,
    autonomy: dict[str, Any],
    policy: Any,
    upstream_candidates: int,
    update_hits: int,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []

    def add(
        *,
        module: str,
        kind: str,
        priority: int,
        reason: str,
        prompt: str,
        requires: list[str] | None = None,
        gate: bool = True,
    ) -> None:
        action_id = f"{repo_name}:{kind}:{len(actions) + 1}"
        actions.append(
            {
                "id": action_id,
                "repo": repo_name,
                "module": module,
                "kind": kind,
                "priority": max(1, int(priority)),
                "reason": reason,
                "prompt": prompt,
                "requires": list(requires or []),
                "automation_allowed": bool(gate),
                "autonomy_level": str(autonomy.get("level") or "observe"),
            }
        )

    ready = repo.get("ready")
    in_progress = repo.get("in_progress")
    has_ready = isinstance(ready, list) and len(ready) > 0
    has_in_progress = isinstance(in_progress, list) and len(in_progress) > 0

    servicedrift_cfg = getattr(policy, "servicedrift", {})
    if (
        isinstance(servicedrift_cfg, dict)
        and bool(servicedrift_cfg.get("enabled", True))
        and bool(repo.get("workgraph_exists"))
        and not bool(repo.get("service_running"))
        and (has_ready or has_in_progress)
    ):
        add(
            module="servicedrift",
            kind="restart_workgraph_service",
            priority=96,
            reason="repo has executable work but daemon is not running",
            prompt=_make_prompt(
                repo_name,
                "Start the local workgraph service and verify heartbeat/socket health. "
                "If restart fails, create escalation follow-up task.",
            ),
            requires=["workgraph service access"],
            gate=True,
        )

    activity_state = str(repo.get("activity_state") or "").strip().lower()
    stalledrift_cfg = getattr(policy, "stalledrift", {})
    if isinstance(stalledrift_cfg, dict) and bool(stalledrift_cfg.get("enabled", True)):
        if activity_state == "stalled":
            add(
                module="stalledrift",
                kind="unblock_stalled_execution",
                priority=90,
                reason="repo stalled with open/ready work and no active execution",
                prompt=_make_prompt(
                    repo_name,
                    "Diagnose stall reasons, pick one unblock action, and update dependencies so a concrete next task can start.",
                ),
                gate=True,
            )
        missing_dependencies = max(0, int(repo.get("missing_dependencies") or 0))
        blocked_open = max(0, int(repo.get("blocked_open") or 0))
        if missing_dependencies > 0 or blocked_open > 0:
            add(
                module="stalledrift",
                kind="repair_dependency_chain",
                priority=88,
                reason=f"dependency issues detected (missing={missing_dependencies}, blocked={blocked_open})",
                prompt=_make_prompt(
                    repo_name,
                    "Repair broken dependency links in workgraph tasks and produce an updated ready queue.",
                ),
                gate=True,
            )

    syncdrift_cfg = getattr(policy, "syncdrift", {})
    if isinstance(syncdrift_cfg, dict) and bool(syncdrift_cfg.get("enabled", True)):
        behind = max(0, int(repo.get("behind") or 0))
        if behind > 0:
            add(
                module="syncdrift",
                kind="sync_with_upstream",
                priority=74,
                reason=f"repo is behind upstream by {behind}",
                prompt=_make_prompt(
                    repo_name,
                    "Fetch upstream and propose the safest sync strategy under policy. "
                    "Do not use destructive history rewrites.",
                ),
                requires=["git fetch"],
                gate=bool(syncdrift_cfg.get("allow_rebase", True) or syncdrift_cfg.get("allow_merge", True)),
            )
        if bool(repo.get("git_dirty")):
            add(
                module="syncdrift",
                kind="triage_dirty_worktree",
                priority=56,
                reason="working tree has uncommitted changes",
                prompt=_make_prompt(
                    repo_name,
                    "Classify dirty changes as intentional work vs drift. Park, commit, or discard via explicit tasked decision.",
                ),
                gate=True,
            )

    if has_in_progress:
        add(
            module="factorydrift",
            kind="verify_active_tasks",
            priority=62,
            reason=f"{len(in_progress)} tasks in progress",
            prompt=_make_prompt(
                repo_name,
                "Run drifts checks for each active task and create follow-up tasks for unresolved findings.",
            ),
            gate=True,
        )

    sourcedrift_cfg = getattr(policy, "sourcedrift", {})
    if (
        isinstance(sourcedrift_cfg, dict)
        and bool(sourcedrift_cfg.get("enabled", True))
        and update_hits > 0
    ):
        add(
            module="sourcedrift",
            kind="review_upstream_deltas",
            priority=66,
            reason=f"{update_hits} upstream delta signals",
            prompt=_make_prompt(
                repo_name,
                "Review upstream deltas and decide: ignore, track, integrate-now, or queue-for-pr.",
            ),
            gate=True,
        )

    federatedrift_cfg = getattr(policy, "federatedrift", {})
    if (
        isinstance(federatedrift_cfg, dict)
        and bool(federatedrift_cfg.get("enabled", True))
        and upstream_candidates > 0
    ):
        add(
            module="federatedrift",
            kind="prepare_upstream_draft_prs",
            priority=58,
            reason=f"{upstream_candidates} local contribution candidates",
            prompt=_make_prompt(
                repo_name,
                "Prepare upstream draft PR packets for candidate changes with scoped descriptions and verification notes.",
            ),
            requires=["gh cli", "push permission"],
            gate=bool(
                federatedrift_cfg.get("open_draft_prs", True)
                and autonomy.get("can_open_pr")
            ),
        )

    secdrift_cfg = getattr(policy, "secdrift", {})
    sec_summary = _repo_security_summary(repo)
    sec_findings = max(0, int(sec_summary.get("findings_total") or 0))
    sec_critical = max(0, int(sec_summary.get("critical") or 0))
    sec_high = max(0, int(sec_summary.get("high") or 0))
    if isinstance(secdrift_cfg, dict) and bool(secdrift_cfg.get("enabled", True)):
        if sec_findings > 0 or sec_critical > 0 or sec_high > 0:
            sec_priority = 98 if sec_critical > 0 else (84 if sec_high > 0 else 66)
            add(
                module="secdrift",
                kind="run_security_scan",
                priority=sec_priority,
                reason=(
                    "security findings require model triage "
                    f"(critical={sec_critical}, high={sec_high}, total={sec_findings})"
                ),
                prompt=_make_prompt(
                    repo_name,
                    "Run secdrift triage. Keep analysis model-mediated, classify root cause, and create/update "
                    "repo-local security review tasks with exact remediation prompts and verification steps.",
                ),
                gate=True,
            )

    qadrift_cfg = getattr(policy, "qadrift", {})
    quality = _repo_quality_summary(repo)
    quality_findings = max(0, int(quality.get("findings_total") or 0))
    quality_score = max(0, int(quality.get("quality_score") or 100))
    quality_at_risk = bool(quality.get("at_risk"))
    if isinstance(qadrift_cfg, dict) and bool(qadrift_cfg.get("enabled", True)):
        if quality_at_risk or quality_findings > 0 or quality_score < 85:
            qa_priority = 82 if quality_score < 70 else (74 if quality_at_risk else 62)
            add(
                module="qadrift",
                kind="run_quality_audit",
                priority=qa_priority,
                reason=(
                    "quality signals require model triage "
                    f"(score={quality_score}, findings={quality_findings}, at_risk={quality_at_risk})"
                ),
                prompt=_make_prompt(
                    repo_name,
                    "Run qadrift quality triage, preserve active work, and emit targeted review tasks "
                    "for test/ux/dependency remediation in local workgraph.",
                ),
                gate=True,
            )

    actions.sort(key=lambda row: (-int(row.get("priority") or 0), str(row.get("id") or "")))
    return actions


def build_factory_cycle(
    *,
    snapshot: dict[str, Any],
    policy: Any,
    project_name: str,
    plan_only_override: bool | None = None,
) -> dict[str, Any]:
    factory_cfg = getattr(policy, "factory", {})
    if not isinstance(factory_cfg, dict):
        factory_cfg = {}

    enabled = bool(factory_cfg.get("enabled", False))
    requested_plan_only = bool(factory_cfg.get("plan_only", True))
    if plan_only_override is not None:
        requested_plan_only = bool(plan_only_override)

    max_repos = max(1, int(factory_cfg.get("max_repos_per_cycle") or 5))
    max_actions = max(1, int(factory_cfg.get("max_actions_per_cycle") or 12))

    repos_raw = snapshot.get("repos")
    repos = [row for row in repos_raw if isinstance(row, dict)] if isinstance(repos_raw, list) else []
    repo_by_name = _as_repo_map(repos)
    attention = _attention_map(snapshot)
    upstream_counts = _upstream_count(snapshot)
    update_counts = _update_count(snapshot)

    ranked_repos: list[dict[str, Any]] = []
    for repo_name in sorted(repo_by_name.keys()):
        repo = repo_by_name[repo_name]
        score, reasons = _repo_priority(
            repo=repo,
            attention=attention.get(repo_name),
            upstream_candidates=upstream_counts.get(repo_name, 0),
            update_hits=update_counts.get(repo_name, 0),
        )
        autonomy = resolve_repo_autonomy(policy, repo_name)
        ranked_repos.append(
            {
                "repo": repo_name,
                "score": score,
                "reasons": reasons,
                "autonomy": autonomy,
                "activity_state": str(repo.get("activity_state") or ""),
                "service_running": bool(repo.get("service_running")),
                "workgraph_exists": bool(repo.get("workgraph_exists")),
            }
        )

    ranked_repos.sort(key=lambda row: (-int(row.get("score") or 0), str(row.get("repo") or "")))
    selected = ranked_repos[:max_repos]
    selected_names = {str(row.get("repo") or "") for row in selected}

    action_plan: list[dict[str, Any]] = []
    skipped_actions: list[dict[str, Any]] = []
    repo_action_counts: dict[str, int] = {}
    module_counts: dict[str, int] = {}

    for repo_info in selected:
        repo_name = str(repo_info.get("repo") or "")
        if not repo_name:
            continue
        repo = repo_by_name.get(repo_name, {})
        autonomy = repo_info.get("autonomy")
        if not isinstance(autonomy, dict):
            autonomy = resolve_repo_autonomy(policy, repo_name)
        per_repo_budget = max(0, int(autonomy.get("max_actions_per_cycle") or 0))
        if per_repo_budget <= 0:
            skipped_actions.append(
                {
                    "repo": repo_name,
                    "reason": "repo autonomy max_actions_per_cycle=0",
                    "kind": "repo_budget_exhausted",
                }
            )
            continue

        repo_actions = _plan_repo_actions(
            repo=repo,
            repo_name=repo_name,
            autonomy=autonomy,
            policy=policy,
            upstream_candidates=upstream_counts.get(repo_name, 0),
            update_hits=update_counts.get(repo_name, 0),
        )
        used_repo = 0
        for action in repo_actions:
            if len(action_plan) >= max_actions:
                skipped_actions.append(
                    {
                        "repo": repo_name,
                        "kind": str(action.get("kind") or ""),
                        "reason": "global max_actions_per_cycle reached",
                    }
                )
                continue
            if used_repo >= per_repo_budget:
                skipped_actions.append(
                    {
                        "repo": repo_name,
                        "kind": str(action.get("kind") or ""),
                        "reason": "repo max_actions_per_cycle reached",
                    }
                )
                continue

            action["order"] = len(action_plan) + 1
            action["execution_mode"] = "plan_only" if requested_plan_only else "execute"
            action_plan.append(action)
            used_repo += 1
            repo_action_counts[repo_name] = repo_action_counts.get(repo_name, 0) + 1
            module = str(action.get("module") or "unknown")
            module_counts[module] = module_counts.get(module, 0) + 1

    selected_repos = [row for row in selected if str(row.get("repo") or "") in selected_names]

    model_cfg = getattr(policy, "model", {})
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    generated_at = _iso_now()
    cycle_id = f"factory-{project_name}-{_safe_ts_for_file(generated_at)}"
    hints: list[str] = []
    if not enabled:
        hints.append("factory disabled in policy; cycle generated in manual mode")
    if requested_plan_only:
        hints.append("plan_only active; prompts are ready for Claude/Codex execution")
    else:
        hints.append("execution mode active; deterministic safe-action handlers will run automatically")
    if skipped_actions:
        hints.append("some actions skipped due to budgets; raise per-cycle limits if needed")
    if not action_plan:
        hints.append("no actions selected from current snapshot signals")

    return {
        "schema": 1,
        "cycle_id": cycle_id,
        "generated_at": generated_at,
        "project": project_name,
        "enabled": enabled,
        "execution_mode": "plan_only" if requested_plan_only else "execute",
        "execution_status": "planned_only",
        "policy": {
            "factory": {
                "enabled": enabled,
                "cycle_seconds": max(5, int(factory_cfg.get("cycle_seconds") or 90)),
                "plan_only": requested_plan_only,
                "max_repos_per_cycle": max_repos,
                "max_actions_per_cycle": max_actions,
                "emit_followups": bool(factory_cfg.get("emit_followups", False)),
                "max_followups_per_repo": max(1, int(factory_cfg.get("max_followups_per_repo") or 2)),
                "write_decision_ledger": bool(factory_cfg.get("write_decision_ledger", True)),
                "hard_stop_on_failed_verification": bool(
                    factory_cfg.get("hard_stop_on_failed_verification", True)
                ),
            },
            "model": {
                "planner_profile": str(model_cfg.get("planner_profile") or "default"),
                "worker_profile": str(model_cfg.get("worker_profile") or "default"),
                "temperature": float(model_cfg.get("temperature") or 0.2),
                "max_tool_rounds": max(1, int(model_cfg.get("max_tool_rounds") or 6)),
            },
            "secdrift": (
                dict(getattr(policy, "secdrift"))
                if isinstance(getattr(policy, "secdrift", {}), dict)
                else {}
            ),
            "qadrift": (
                dict(getattr(policy, "qadrift"))
                if isinstance(getattr(policy, "qadrift", {}), dict)
                else {}
            ),
        },
        "inputs": {
            "repo_count": len(repos),
            "overview": snapshot.get("overview") if isinstance(snapshot.get("overview"), dict) else {},
            "updates": snapshot.get("updates") if isinstance(snapshot.get("updates"), dict) else {},
            "upstream_candidates": len(snapshot.get("upstream_candidates") or []),
        },
        "decision_trace": ranked_repos[: max_repos * 2],
        "selected_repos": selected_repos,
        "action_plan": action_plan,
        "skipped_actions": skipped_actions[:40],
        "module_counts": module_counts,
        "repo_action_counts": repo_action_counts,
        "outcomes": {
            "planned_actions": len(action_plan),
            "executed_actions": 0,
            "selected_repos": len(selected_repos),
            "total_ranked_repos": len(ranked_repos),
            "skipped_actions": len(skipped_actions),
        },
        "next_cycle_hints": hints,
    }


def summarize_factory_cycle(cycle: dict[str, Any]) -> dict[str, Any]:
    actions = cycle.get("action_plan")
    selected = cycle.get("selected_repos")
    return {
        "cycle_id": str(cycle.get("cycle_id") or ""),
        "generated_at": str(cycle.get("generated_at") or ""),
        "execution_mode": str(cycle.get("execution_mode") or "plan_only"),
        "execution_status": str(cycle.get("execution_status") or "planned_only"),
        "planned_actions": len(actions) if isinstance(actions, list) else 0,
        "selected_repos": len(selected) if isinstance(selected, list) else 0,
        "module_counts": cycle.get("module_counts") if isinstance(cycle.get("module_counts"), dict) else {},
        "executed_actions": int((cycle.get("execution") or {}).get("executed", 0)) if isinstance(cycle.get("execution"), dict) else 0,
        "failed_actions": int((cycle.get("execution") or {}).get("failed", 0)) if isinstance(cycle.get("execution"), dict) else 0,
        "next_cycle_hints": cycle.get("next_cycle_hints") if isinstance(cycle.get("next_cycle_hints"), list) else [],
    }


def _slug(text: str) -> str:
    value = str(text or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = value.strip("-")
    return value or "task"


def _repo_row_map(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = snapshot.get("repos")
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        out[name] = row
    return out


def _repo_path_map(snapshot: dict[str, Any]) -> dict[str, Path]:
    rows = snapshot.get("repos")
    out: dict[str, Path] = {}
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        repo = str(row.get("name") or "").strip()
        path_raw = str(row.get("path") or "").strip()
        if not repo or not path_raw:
            continue
        out[repo] = Path(path_raw).expanduser()
    return out


def _followup_description(action: dict[str, Any], cycle: dict[str, Any]) -> str:
    cycle_id = str(cycle.get("cycle_id") or "")
    generated_at = str(cycle.get("generated_at") or "")
    reason = str(action.get("reason") or "")
    prompt = str(action.get("prompt") or "")
    module = str(action.get("module") or "")
    kind = str(action.get("kind") or "")
    priority = int(action.get("priority") or 0)
    return (
        "Factory auditor corrective follow-up.\n\n"
        f"Cycle: {cycle_id}\n"
        f"Generated: {generated_at}\n"
        f"Module: {module}\n"
        f"Kind: {kind}\n"
        f"Priority: {priority}\n"
        f"Reason: {reason}\n\n"
        "Suggested local agent prompt:\n"
        f"{prompt}\n"
    )


def execute_factory_cycle(
    *,
    cycle: dict[str, Any],
    snapshot: dict[str, Any],
    policy: Any,
    project_dir: Path,
    emit_followups: bool,
    max_followups_per_repo: int = 2,
    allow_execute_draft_prs: bool = False,
) -> dict[str, Any]:
    actions = cycle.get("action_plan")
    action_rows = [row for row in actions if isinstance(row, dict)] if isinstance(actions, list) else []
    hard_stop = bool((cycle.get("policy") or {}).get("factory", {}).get("hard_stop_on_failed_verification", True))
    repo_paths = _repo_path_map(snapshot)
    repo_rows = _repo_row_map(snapshot)

    followups = {
        "enabled": bool(emit_followups),
        "attempted": 0,
        "created": 0,
        "existing": 0,
        "skipped": 0,
        "errors": [],
        "tasks": [],
    }
    if emit_followups:
        followups = emit_factory_followups(
            cycle=cycle,
            snapshot=snapshot,
            max_followups_per_repo=max(1, int(max_followups_per_repo)),
        )

    attempts: list[dict[str, Any]] = []
    attempted = 0
    executed = 0
    succeeded = 0
    failed = 0
    skipped = 0
    stopped = False
    stop_reason = ""

    for action in action_rows:
        action_id = str(action.get("id") or "")
        repo = str(action.get("repo") or "")
        kind = str(action.get("kind") or "")
        module = str(action.get("module") or "")
        row: dict[str, Any] = {
            "id": action_id,
            "repo": repo,
            "module": module,
            "kind": kind,
            "started_at": _iso_now(),
            "status": "skipped",
            "reason": "",
            "exit_code": 0,
        }
        attempted += 1

        if stopped:
            row["status"] = "skipped"
            row["reason"] = f"cycle stopped early: {stop_reason or 'hard stop'}"
            skipped += 1
            row["finished_at"] = _iso_now()
            attempts.append(row)
            continue

        if not bool(action.get("automation_allowed", False)):
            row["status"] = "skipped"
            row["reason"] = "automation not allowed for action under autonomy policy"
            skipped += 1
            row["finished_at"] = _iso_now()
            attempts.append(row)
            continue

        repo_path = repo_paths.get(repo)
        if repo_path is None:
            row["status"] = "failed"
            row["reason"] = "repo path missing"
            row["exit_code"] = 1
            failed += 1
            row["finished_at"] = _iso_now()
            attempts.append(row)
            if hard_stop:
                stopped = True
                stop_reason = "repo path missing"
            continue

        def _done(status: str, *, reason: str = "", exit_code: int = 0, details: Any | None = None) -> None:
            nonlocal executed, succeeded, failed, skipped, stopped, stop_reason
            row["status"] = status
            row["reason"] = reason
            row["exit_code"] = int(exit_code)
            if details is not None:
                row["details"] = details
            row["finished_at"] = _iso_now()
            if status in ("succeeded", "delegated"):
                executed += 1
                succeeded += 1
            elif status == "failed":
                executed += 1
                failed += 1
                if hard_stop:
                    stopped = True
                    stop_reason = reason or f"{kind} failed"
            else:
                skipped += 1
            attempts.append(row)

        if kind == "restart_workgraph_service":
            rc, out, err = _run_cmd(
                ["wg", "--dir", str(repo_path / ".workgraph"), "service", "start"],
                cwd=repo_path,
                timeout=20.0,
            )
            text = f"{out}\n{err}".lower()
            ok = rc == 0 or "already running" in text
            if ok:
                _done("succeeded", reason="service start ok", exit_code=0)
            else:
                _done("failed", reason=(err or out or "service start failed")[:220], exit_code=rc)
            continue

        if kind == "sync_with_upstream":
            rc, out, err = _run_cmd(["git", "fetch", "--all", "--prune"], cwd=repo_path, timeout=60.0)
            if rc == 0:
                _done("succeeded", reason="git fetch completed", exit_code=0, details={"stdout": out[:220]})
            else:
                _done("failed", reason=(err or out or "git fetch failed")[:220], exit_code=rc)
            continue

        if kind == "verify_active_tasks":
            repo_row = repo_rows.get(repo, {})
            in_progress = repo_row.get("in_progress") if isinstance(repo_row.get("in_progress"), list) else []
            task_ids = [str(item.get("id") or "") for item in in_progress if isinstance(item, dict)][:8]
            task_ids = [tid for tid in task_ids if tid]
            if not task_ids:
                _done("skipped", reason="no in-progress tasks found")
                continue
            checks: list[dict[str, Any]] = []
            ok = True
            for task_id in task_ids:
                rc, out, err = _run_cmd(
                    [
                        sys.executable,
                        "-m",
                        "driftdriver.cli",
                        "--dir",
                        str(repo_path),
                        "check",
                        "--task",
                        task_id,
                        "--write-log",
                        "--create-followups",
                        "--json",
                    ],
                    cwd=repo_path,
                    timeout=180.0,
                )
                checks.append({"task_id": task_id, "exit_code": rc})
                if rc not in (0, 3):
                    ok = False
                    checks[-1]["error"] = (err or out or "")[:220]
            if ok:
                _done("succeeded", reason=f"verified {len(task_ids)} active task(s)", details={"checks": checks})
            else:
                _done("failed", reason="one or more active task checks failed", exit_code=1, details={"checks": checks})
            continue

        if kind == "review_upstream_deltas":
            rc, out, err = _run_cmd(
                [sys.executable, "-m", "driftdriver.cli", "--dir", str(repo_path), "updates", "--json"],
                cwd=repo_path,
                timeout=180.0,
            )
            if rc in (0, 3):
                _done("succeeded", reason="upstream delta review completed", details={"exit_code": rc, "stdout": out[:220]})
            else:
                _done("failed", reason=(err or out or "updates command failed")[:220], exit_code=rc)
            continue

        if kind == "run_security_scan":
            sec_cfg = getattr(policy, "secdrift", {})
            sec_cfg = dict(sec_cfg) if isinstance(sec_cfg, dict) else {}
            report = run_secdrift_scan(
                repo_name=repo,
                repo_path=repo_path,
                policy_cfg=sec_cfg,
            )
            emit_cfg = bool(sec_cfg.get("emit_review_tasks", True))
            max_tasks = max(1, int(sec_cfg.get("max_review_tasks_per_repo", 3)))
            task_emit = {
                "enabled": False,
                "attempted": 0,
                "created": 0,
                "existing": 0,
                "skipped": 0,
                "errors": [],
                "tasks": [],
            }
            if emit_cfg:
                task_emit = emit_security_review_tasks(
                    repo_path=repo_path,
                    report=report,
                    max_tasks=max_tasks,
                )
            summary_row = report.get("summary") if isinstance(report.get("summary"), dict) else {}
            critical = int(summary_row.get("critical") or 0)
            total = int(summary_row.get("findings_total") or 0)
            hard_stop_sec = bool(sec_cfg.get("hard_stop_on_critical", False))
            details = {
                "summary": summary_row,
                "recommended_reviews": list(report.get("recommended_reviews") or [])[:8],
                "task_emission": task_emit,
                "model_contract": report.get("model_contract") if isinstance(report.get("model_contract"), dict) else {},
            }
            if hard_stop_sec and critical > 0:
                _done(
                    "failed",
                    reason=f"secdrift critical findings={critical}",
                    exit_code=1,
                    details=details,
                )
            else:
                _done(
                    "succeeded",
                    reason=f"secdrift findings={total} critical={critical}",
                    details=details,
                )
            continue

        if kind == "run_quality_audit":
            qa_cfg = getattr(policy, "qadrift", {})
            qa_cfg = dict(qa_cfg) if isinstance(qa_cfg, dict) else {}
            repo_row = repo_rows.get(repo, {})
            report = run_program_quality_scan(
                repo_name=repo,
                repo_path=repo_path,
                repo_snapshot=repo_row if isinstance(repo_row, dict) else {},
                policy_cfg=qa_cfg,
            )
            emit_cfg = bool(qa_cfg.get("emit_review_tasks", True))
            max_tasks = max(1, int(qa_cfg.get("max_review_tasks_per_repo", 3)))
            task_emit = {
                "enabled": False,
                "attempted": 0,
                "created": 0,
                "existing": 0,
                "skipped": 0,
                "errors": [],
                "tasks": [],
            }
            if emit_cfg:
                task_emit = emit_quality_review_tasks(
                    repo_path=repo_path,
                    report=report,
                    max_tasks=max_tasks,
                )
            summary_row = report.get("summary") if isinstance(report.get("summary"), dict) else {}
            quality_score = int(summary_row.get("quality_score") or 100)
            total = int(summary_row.get("findings_total") or 0)
            _done(
                "succeeded",
                reason=f"qadrift findings={total} quality_score={quality_score}",
                details={
                    "summary": summary_row,
                    "recommended_reviews": list(report.get("recommended_reviews") or [])[:8],
                    "task_emission": task_emit,
                    "model_contract": report.get("model_contract") if isinstance(report.get("model_contract"), dict) else {},
                },
            )
            continue

        if kind == "prepare_upstream_draft_prs":
            if not allow_execute_draft_prs:
                _done("skipped", reason="draft PR execution disabled for this cycle")
                continue
            rc, out, err = _run_cmd(
                [
                    sys.executable,
                    "-m",
                    "driftdriver.ecosystem_hub",
                    "--project-dir",
                    str(project_dir),
                    "open-draft-pr",
                    "--repo",
                    repo,
                    "--execute",
                ],
                cwd=project_dir,
                timeout=180.0,
            )
            if rc == 0:
                _done("succeeded", reason="draft PR execution completed", details={"stdout": out[:220]})
            else:
                _done("failed", reason=(err or out or "draft PR execution failed")[:220], exit_code=rc)
            continue

        if kind in ("unblock_stalled_execution", "repair_dependency_chain", "triage_dirty_worktree"):
            if not emit_followups:
                _done("skipped", reason="safe executor delegates this action to follow-up task emission")
                continue
            task_id = f"factory-{_slug(module)}-{_slug(kind)}"
            matched = [
                row_item
                for row_item in (followups.get("tasks") if isinstance(followups.get("tasks"), list) else [])
                if isinstance(row_item, dict)
                and str(row_item.get("repo") or "") == repo
                and str(row_item.get("task_id") or "") == task_id
            ]
            if matched:
                state = str(matched[0].get("status") or "existing")
                _done("delegated", reason=f"delegated to local task {task_id} ({state})")
            else:
                _done("skipped", reason=f"expected delegated task {task_id} not found")
            continue

        _done("skipped", reason=f"no deterministic executor for action kind '{kind}'")

    execution_status = "executed"
    if failed > 0 and succeeded > 0:
        execution_status = "partial_failed"
    elif failed > 0:
        execution_status = "failed"
    elif executed == 0:
        execution_status = "noop"

    summary = {
        "attempted": attempted,
        "executed": executed,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "hard_stop": hard_stop,
        "stopped_early": stopped,
        "stop_reason": stop_reason,
        "attempts": attempts[:120],
        "followups": followups,
    }

    cycle["execution_status"] = execution_status
    cycle["execution"] = summary
    outcomes = cycle.get("outcomes")
    if isinstance(outcomes, dict):
        outcomes["executed_actions"] = executed
        outcomes["failed_actions"] = failed
        outcomes["skipped_actions_runtime"] = skipped

    return summary


def emit_factory_followups(
    *,
    cycle: dict[str, Any],
    snapshot: dict[str, Any],
    max_followups_per_repo: int = 2,
) -> dict[str, Any]:
    actions = cycle.get("action_plan")
    if not isinstance(actions, list):
        return {
            "enabled": True,
            "attempted": 0,
            "created": 0,
            "existing": 0,
            "skipped": 0,
            "errors": [],
            "tasks": [],
        }

    repo_paths = _repo_path_map(snapshot)
    per_repo_counts: dict[str, int] = {}
    out: dict[str, Any] = {
        "enabled": True,
        "attempted": 0,
        "created": 0,
        "existing": 0,
        "skipped": 0,
        "errors": [],
        "tasks": [],
    }
    per_repo_limit = max(1, int(max_followups_per_repo))

    for action in actions:
        if not isinstance(action, dict):
            continue
        repo = str(action.get("repo") or "").strip()
        if not repo:
            continue
        if per_repo_counts.get(repo, 0) >= per_repo_limit:
            out["skipped"] = int(out["skipped"]) + 1
            continue
        repo_path = repo_paths.get(repo)
        if repo_path is None:
            out["errors"].append(f"{repo}: repo path missing in snapshot")
            continue
        wg_dir = repo_path / ".workgraph"
        if not wg_dir.exists():
            out["errors"].append(f"{repo}: .workgraph missing")
            continue

        module = str(action.get("module") or "factorydrift")
        kind = str(action.get("kind") or "corrective_action")
        task_id = f"factory-{_slug(module)}-{_slug(kind)}"
        title = f"factory: {kind.replace('_', ' ')}"
        desc = _followup_description(action, cycle)

        out["attempted"] = int(out["attempted"]) + 1
        show_rc, _, show_err = _run_cmd(
            ["wg", "--dir", str(wg_dir), "show", task_id, "--json"],
            cwd=repo_path,
            timeout=20.0,
        )

        if show_rc == 0:
            out["existing"] = int(out["existing"]) + 1
            per_repo_counts[repo] = per_repo_counts.get(repo, 0) + 1
            out["tasks"].append(
                {
                    "repo": repo,
                    "task_id": task_id,
                    "status": "existing",
                }
            )
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
                "factory",
                "-t",
                _slug(module),
            ],
            cwd=repo_path,
            timeout=30.0,
        )

        if add_rc == 0:
            out["created"] = int(out["created"]) + 1
            per_repo_counts[repo] = per_repo_counts.get(repo, 0) + 1
            out["tasks"].append(
                {
                    "repo": repo,
                    "task_id": task_id,
                    "status": "created",
                }
            )
        else:
            err = (add_err or add_out or show_err or "").strip()
            out["errors"].append(f"{repo}: could not create {task_id}: {err[:220]}")

    out["tasks"] = list(out.get("tasks") or [])[:80]
    out["errors"] = list(out.get("errors") or [])[:80]
    return out


def write_factory_ledger(
    *,
    project_dir: Path,
    cycle: dict[str, Any],
    central_repo: Path | None,
    write_decision_ledger: bool,
) -> dict[str, Any]:
    local_root = project_dir / ".workgraph" / "service" / "factoryd"
    stamp = _safe_ts_for_file(str(cycle.get("generated_at") or _iso_now()))
    local_latest = local_root / "latest.json"
    local_history = local_root / "history" / f"{stamp}.json"

    _write_json(local_latest, cycle)
    _write_json(local_history, cycle)

    out: dict[str, Any] = {
        "local_latest": str(local_latest),
        "local_history": str(local_history),
        "central_latest": "",
        "central_history": "",
        "central_written": False,
    }
    if not write_decision_ledger or central_repo is None:
        return out

    project_name = str(project_dir.name)
    central_root = central_repo / "ecosystem-hub" / "factory"
    central_latest = central_root / "register" / f"{project_name}.json"
    central_history = central_root / "history" / project_name / f"{stamp}.json"
    _write_json(central_latest, cycle)
    _write_json(central_history, cycle)

    out["central_latest"] = str(central_latest)
    out["central_history"] = str(central_history)
    out["central_written"] = True
    return out
