# ABOUTME: Authority-gated drift task creation — dedup, budgets, quality, escalation.
# ABOUTME: Single path for all follow-up creation; actor identity always resolved.

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path
from typing import Any

from driftdriver.actor import Actor
from driftdriver.authority import Budget, can_do, check_budget, get_budget, load_authority_policy
from driftdriver.budget_ledger import recent_count, record_operation

# Default global ceiling — hard safety net across all lanes.
# Authority budgets handle per-actor limits; this prevents runaway across lanes.
DEFAULT_GLOBAL_CEILING = 50


def _record_escalation(
    wg_dir: Path,
    actor: Actor,
    lane_tag: str,
    task_id: str,
    title: str,
    reason: str,
) -> None:
    """Record a capped finding to the escalation log for human visibility.

    When a drift finding can't be created (budget exhausted), it goes here
    instead of silently evaporating. The ecosystem hub can surface these.
    """
    escalation_path = wg_dir / "escalations.jsonl"
    entry = {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "actor_id": actor.id,
        "actor_class": actor.actor_class,
        "lane": lane_tag,
        "task_id": task_id,
        "title": title,
        "reason": reason,
        "type": "budget_exhausted",
    }
    try:
        escalation_path.parent.mkdir(parents=True, exist_ok=True)
        with open(escalation_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _run_wg(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 40.0,
) -> tuple[int, str, str]:
    """Run a wg command with fallback path resolution."""
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
    except FileNotFoundError:
        if cmd and str(cmd[0]) == "wg":
            for candidate in [
                str(Path.home() / ".cargo" / "bin" / "wg"),
                "/opt/homebrew/bin/wg",
                "/usr/local/bin/wg",
            ]:
                if not Path(candidate).exists():
                    continue
                try:
                    proc = _invoke([candidate, *cmd[1:]])
                    return int(proc.returncode), str(proc.stdout or "").strip(), str(proc.stderr or "").strip()
                except FileNotFoundError:
                    continue
        return 127, "", "wg not found"
    except Exception as exc:
        return 1, "", str(exc)
    return int(proc.returncode), str(proc.stdout or "").strip(), str(proc.stderr or "").strip()


def _load_task_list(wg_dir: Path, *, cwd: Path | None = None) -> list[dict[str, Any]]:
    """Load the full task list from wg, returning [] on failure."""
    rc, out, _ = _run_wg(
        ["wg", "--dir", str(wg_dir), "--json", "list"],
        cwd=cwd,
        timeout=30.0,
    )
    if rc != 0:
        return []
    try:
        tasks = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return []
    return tasks if isinstance(tasks, list) else []


def count_active_drift_tasks(
    wg_dir: Path,
    lane_tag: str,
    *,
    cwd: Path | None = None,
    _tasks: list[dict[str, Any]] | None = None,
) -> int:
    """Count non-terminal tasks tagged with 'drift' and the given lane tag."""
    tasks = _tasks if _tasks is not None else _load_task_list(wg_dir, cwd=cwd)
    terminal = {"done", "abandoned", "failed"}
    count = 0
    for t in tasks:
        status = str(t.get("status", "")).lower()
        if status in terminal:
            continue
        tags = t.get("tags") or []
        if not isinstance(tags, list):
            continue
        if "drift" in tags and lane_tag in tags:
            count += 1
    return count


def count_all_active_drift_tasks(
    wg_dir: Path,
    *,
    cwd: Path | None = None,
    _tasks: list[dict[str, Any]] | None = None,
) -> int:
    """Count all non-terminal drift tasks across all lanes."""
    tasks = _tasks if _tasks is not None else _load_task_list(wg_dir, cwd=cwd)
    terminal = {"done", "abandoned", "failed"}
    count = 0
    for t in tasks:
        status = str(t.get("status", "")).lower()
        if status in terminal:
            continue
        tags = t.get("tags") or []
        if not isinstance(tags, list):
            continue
        if "drift" in tags:
            count += 1
    return count


def _apply_quality_modifier(
    wg_dir: Path,
    actor: Actor,
    policy: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Apply quality-based budget adjustment. Returns effective policy."""
    effective_policy = dict(policy) if policy else {}
    try:
        from driftdriver.quality_signal import compute_actor_quality, quality_budget_modifier
        outcomes_path = wg_dir / "drift-outcomes.jsonl"
        quality = compute_actor_quality(outcomes_path, actor.id, actor.actor_class)
        modifier = quality_budget_modifier(quality) if quality.total_outcomes > 0 else 1.0
        if modifier != 1.0:
            base_budget = get_budget(actor.actor_class, policy=policy)
            adjusted = Budget(
                max_active_tasks=max(1, int(base_budget.max_active_tasks * modifier)),
                max_creates_per_hour=max(1, int(base_budget.max_creates_per_hour * modifier)),
                max_dispatches_per_hour=max(0, int(base_budget.max_dispatches_per_hour * modifier)),
            )
            effective_budgets = dict(effective_policy.get("budgets", {}))
            effective_budgets[actor.actor_class] = adjusted
            effective_policy["budgets"] = effective_budgets
    except Exception:
        pass  # Quality signal unavailable — use base budgets
    return effective_policy or None


def get_global_ceiling(policy: dict[str, Any] | None) -> int:
    """Get the global drift task ceiling from authority policy."""
    if policy and "global_ceiling" in policy:
        return max(1, int(policy["global_ceiling"]))
    return DEFAULT_GLOBAL_CEILING


def guarded_add_drift_task(
    *,
    wg_dir: Path,
    task_id: str,
    title: str,
    description: str,
    lane_tag: str,
    actor: Actor | None = None,
    extra_tags: list[str] | None = None,
    after: str | None = None,
    cwd: Path | None = None,
    policy_path: Path | None = None,
    cap: int = 0,  # Deprecated — ignored. Authority budgets handle limits.
) -> str:
    """Create a drift follow-up task with authority-gated budgets.

    Single path for all follow-up creation. Actor defaults to a lane actor
    when not provided. Authority budgets (quality-adjusted) enforce limits.
    Global ceiling prevents runaway across all lanes.

    Returns:
        "created"      - new task added
        "existing"     - task_id already exists (dedup hit)
        "capped"       - budget exhausted or global ceiling hit
        "unauthorized" - actor lacks create permission
        "error"        - wg add failed
    """
    # 1. Resolve actor — always present.
    if actor is None:
        actor = Actor(id=f"lane-{lane_tag}", actor_class="lane", name=lane_tag)

    # 2. Load authority policy.
    if policy_path is None:
        candidate = wg_dir / "drift-policy.toml"
        if candidate.exists():
            policy_path = candidate
    policy = load_authority_policy(policy_path) if policy_path else None

    # 3. Check authority — does this actor class have 'create' permission?
    if not can_do(actor, "create", policy=policy):
        return "unauthorized"

    # 4. Dedup — if this exact task_id already exists in any state, skip.
    show_rc, _, _ = _run_wg(
        ["wg", "--dir", str(wg_dir), "show", task_id, "--json"],
        cwd=cwd,
        timeout=20.0,
    )
    if show_rc == 0:
        return "existing"

    # 5. Load task list once (used for per-lane and global counts).
    tasks = _load_task_list(wg_dir, cwd=cwd)

    # 6. Quality-adjusted budget check.
    effective_policy = _apply_quality_modifier(wg_dir, actor, policy)
    active_in_lane = count_active_drift_tasks(wg_dir, lane_tag, _tasks=tasks)
    ledger_path = wg_dir / "budget-ledger.jsonl"
    hourly_creates = recent_count(ledger_path, actor.id, "create", window_seconds=3600)
    allowed, reason = check_budget(
        actor, "create",
        current_count=active_in_lane,
        recent_count=hourly_creates,
        policy=effective_policy,
    )
    if not allowed:
        _record_escalation(wg_dir, actor, lane_tag, task_id, title, reason)
        return "capped"

    # 7. Global ceiling — safety net against runaway across all lanes.
    total_active_drift = count_all_active_drift_tasks(wg_dir, _tasks=tasks)
    ceiling = get_global_ceiling(effective_policy)
    if total_active_drift >= ceiling:
        reason = f"global_ceiling exceeded: {total_active_drift} >= {ceiling}"
        _record_escalation(wg_dir, actor, lane_tag, task_id, title, reason)
        return "capped"

    # 8. Emit directive (replaces direct wg add call).
    from driftdriver.directives import Action, Directive, DirectiveLog
    from driftdriver.executor_shim import ExecutorShim

    directive = Directive(
        source="drift_task_guard",
        repo=actor.repo,
        action=Action.CREATE_TASK,
        params={
            "task_id": task_id,
            "title": title,
            "description": description,
            "tags": ["drift", lane_tag] + (extra_tags or []),
            "after": [after] if after else [],
        },
        reason=f"drift follow-up from lane={lane_tag}",
        priority="normal",
    )

    directive_dir = wg_dir / "service" / "directives"
    log = DirectiveLog(directive_dir)
    shim = ExecutorShim(wg_dir=wg_dir, log=log)
    shim_result = shim.execute(directive)

    if shim_result != "completed":
        return "error"

    # 9. Record in budget ledger.
    record_operation(
        ledger_path,
        actor_id=actor.id,
        actor_class=actor.actor_class,
        operation="create",
        repo=actor.repo,
        detail=task_id,
    )
    return "created"


# Backward-compatible alias — callers that imported this name still work.
guarded_add_drift_task_with_authority = guarded_add_drift_task
