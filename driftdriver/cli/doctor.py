# ABOUTME: Doctor, compact, and queue subcommands for driftdriver CLI.
# ABOUTME: Health auditing, drift queue compaction, and queue inspection.

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim
from driftdriver.health import (
    compute_scoreboard,
    find_duplicate_open_drift_groups,
    has_contract,
    is_active,
    is_drift_task,
    normalize_drift_key,
    rank_ready_drift_queue,
    redrift_depth,
)
from driftdriver.policy import load_drift_policy
from driftdriver.workgraph import find_workgraph_dir, load_workgraph

from .check import ExitCode
from ._helpers import _maybe_auto_ensure_contracts, _wrapper_commands_available
from .install_cmd import cmd_install


def _doctor_report(*, wg_dir: Path, policy: Any) -> dict[str, Any]:
    wg = load_workgraph(wg_dir)
    tasks = list(wg.tasks.values())
    wrappers = {
        "driftdriver": (wg_dir / "driftdriver").exists(),
        "drifts": (wg_dir / "drifts").exists(),
        "coredrift": (wg_dir / "coredrift").exists(),
    }
    commands = _wrapper_commands_available(wrapper=wg_dir / "drifts")
    score = compute_scoreboard(tasks)

    active_tasks = [t for t in tasks if is_active(t)]
    missing_contract_ids = [str(t.get("id") or "") for t in active_tasks if not has_contract(t)]

    issues: list[dict[str, str]] = []
    required_commands = {"check", "updates", "doctor", "queue", "run"}
    missing_commands = sorted(list(required_commands - set(commands)))
    if missing_commands:
        issues.append(
            {
                "severity": "high",
                "kind": "wrapper_outdated",
                "message": f"drifts wrapper misses commands: {', '.join(missing_commands)}",
            }
        )

    if score["active_contract_coverage"] < 0.9:
        issues.append(
            {
                "severity": "high" if score["active_contract_coverage"] < 0.7 else "medium",
                "kind": "contract_coverage",
                "message": f"active contract coverage is {score['active_contract_coverage']:.2f}",
            }
        )

    max_depth = int(getattr(policy, "loop_max_redrift_depth", 2))
    if int(score["max_redrift_depth"]) > max_depth:
        issues.append(
            {
                "severity": "high",
                "kind": "loop_depth",
                "message": f"max redrift depth {score['max_redrift_depth']} exceeds policy limit {max_depth}",
            }
        )

    max_ready = int(getattr(policy, "loop_max_ready_drift_followups", 20))
    if int(score["ready_drift"]) > max_ready:
        issues.append(
            {
                "severity": "high",
                "kind": "queue_pressure",
                "message": f"ready drift queue {score['ready_drift']} exceeds policy limit {max_ready}",
            }
        )

    duplicate_groups = score.get("duplicate_open_drift_groups") or []
    if duplicate_groups:
        issues.append(
            {
                "severity": "medium",
                "kind": "duplicate_followups",
                "message": f"{len(duplicate_groups)} duplicate open drift groups detected",
            }
        )

    status = "healthy"
    if any(i["severity"] == "high" for i in issues):
        status = "risk"
    elif issues:
        status = "watch"

    return {
        "status": status,
        "wrappers": wrappers,
        "commands_available": commands,
        "scoreboard": score,
        "active_missing_contract_count": len(missing_contract_ids),
        "active_missing_contract_sample": missing_contract_ids[:10],
        "issues": issues,
    }


def _compact_plan(*, tasks: list[dict[str, Any]], max_ready: int, max_redrift_depth: int) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        if not is_drift_task(task) or not is_active(task):
            continue
        key = normalize_drift_key(task)
        grouped.setdefault(key, []).append(task)

    def _created_epoch(task: dict[str, Any]) -> int:
        raw = str(task.get("created_at") or "").strip()
        if not raw:
            return 0
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except Exception:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    duplicate_groups: list[dict[str, Any]] = []
    abandon_ids: list[str] = []
    for key, rows in grouped.items():
        if len(rows) <= 1:
            continue
        ordered = sorted(
            rows,
            key=lambda t: (
                0 if str(t.get("status") or "") == "in-progress" else 1,
                _created_epoch(t),
                str(t.get("id") or ""),
            ),
        )
        keep = str(ordered[0].get("id") or "")
        drop = [str(t.get("id") or "") for t in ordered[1:] if str(t.get("id") or "")]
        if drop:
            duplicate_groups.append(
                {
                    "key": key,
                    "keep_task_id": keep,
                    "abandon_task_ids": drop,
                }
            )
            abandon_ids.extend(drop)

    depth_exceeded_ids: list[str] = []
    depth_limit = max(0, int(max_redrift_depth))
    for task in tasks:
        if not is_drift_task(task) or not is_active(task):
            continue
        task_id = str(task.get("id") or "")
        if (
            task_id.startswith("redrift-")
            and redrift_depth(task_id) > depth_limit
            and str(task.get("status") or "") != "in-progress"
        ):
            depth_exceeded_ids.append(task_id)

    if depth_exceeded_ids:
        abandon_ids.extend(depth_exceeded_ids)

    ready = rank_ready_drift_queue(tasks, limit=10_000)
    safe_max_ready = max(0, int(max_ready))
    overflow = ready[safe_max_ready:] if len(ready) > safe_max_ready else []
    overflow_ids = [str(item.get("task_id") or "") for item in overflow if str(item.get("task_id") or "")]

    # Don't defer tasks that are already being abandoned as duplicates.
    abandon_set = set(abandon_ids)
    overflow_ids = [tid for tid in overflow_ids if tid not in abandon_set]

    return {
        "duplicate_groups": duplicate_groups,
        "depth_exceeded_redrift_task_ids": sorted(set(depth_exceeded_ids)),
        "abandon_task_ids": sorted(set(abandon_ids)),
        "ready_drift_before": len(ready),
        "max_ready_drift": safe_max_ready,
        "max_redrift_depth": depth_limit,
        "defer_task_ids": overflow_ids,
    }


def _repair_wrappers(*, wg_dir: Path) -> int:
    include_ux = (wg_dir / "uxdrift").exists()
    include_therapy = (wg_dir / "therapydrift").exists()
    include_fix = (wg_dir / "fixdrift").exists()
    include_yagni = (wg_dir / "yagnidrift").exists()
    include_redrift = (wg_dir / "redrift").exists()
    args = argparse.Namespace(
        dir=str(wg_dir.parent),
        json=False,
        coredrift_bin=None,
        specdrift_bin=None,
        datadrift_bin=None,
        archdrift_bin=None,
        depsdrift_bin=None,
        with_uxdrift=include_ux,
        uxdrift_bin=None,
        with_therapydrift=include_therapy,
        therapydrift_bin=None,
        with_fixdrift=include_fix,
        fixdrift_bin=None,
        with_yagnidrift=include_yagni,
        yagnidrift_bin=None,
        with_redrift=include_redrift,
        redrift_bin=None,
        with_amplifier_executor=(wg_dir / "executors" / "amplifier.toml").exists(),
        with_claude_code_hooks=(wg_dir.parent / ".claude" / "hooks.json").exists(),
        wrapper_mode="portable",
        no_ensure_contracts=False,
    )
    return cmd_install(args)


def cmd_queue(args: argparse.Namespace) -> int:
    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    wg = load_workgraph(wg_dir)
    tasks = list(wg.tasks.values())

    limit = int(getattr(args, "limit", 10))
    if limit < 1:
        limit = 1
    ready = rank_ready_drift_queue(tasks, limit=limit)
    duplicates = find_duplicate_open_drift_groups(tasks)
    out = {
        "ready_drift": ready,
        "duplicate_open_drift_groups": duplicates,
        "scoreboard": compute_scoreboard(tasks),
    }

    as_json = bool(getattr(args, "json", False))
    if as_json:
        print(json.dumps(out, indent=2, sort_keys=False))
        return ExitCode.ok

    print(f"Ready drift queue: {len(ready)}")
    for item in ready:
        print(f"- {item['task_id']} [p={item['priority']}] {item['title']}")

    if duplicates:
        print(f"\nDuplicate drift groups: {len(duplicates)}")
        for group in duplicates[:5]:
            print(f"- {group['key']} ({group['count']}): {', '.join(group['task_ids'][:4])}")
    return ExitCode.ok


def cmd_compact(args: argparse.Namespace) -> int:
    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    policy = load_drift_policy(wg_dir)
    wg = load_workgraph(wg_dir)
    tasks = list(wg.tasks.values())

    max_ready_default = int(getattr(policy, "loop_max_ready_drift_followups", 20))
    max_ready_raw = getattr(args, "max_ready", None)
    max_ready = max_ready_default if max_ready_raw is None else int(max_ready_raw)
    if max_ready < 0:
        max_ready = 0
    max_redrift_depth = int(getattr(policy, "loop_max_redrift_depth", 2))
    if max_redrift_depth < 0:
        max_redrift_depth = 0
    defer_hours = int(getattr(args, "defer_hours", 24))
    if defer_hours < 1:
        defer_hours = 1

    plan = _compact_plan(tasks=tasks, max_ready=max_ready, max_redrift_depth=max_redrift_depth)
    applied_abandoned: list[str] = []
    applied_deferred: list[str] = []
    errors: list[str] = []

    if bool(getattr(args, "apply", False)):
        log = DirectiveLog(wg_dir / "directives")
        shim = ExecutorShim(wg_dir=wg_dir, log=log)
        repo_name = wg_dir.parent.name

        for task_id in plan["abandon_task_ids"]:
            directive = Directive(
                source="doctor/compact",
                repo=repo_name,
                action=Action.ABANDON_TASK,
                params={"task_id": task_id},
                reason="compact: duplicate or depth-exceeded drift task",
            )
            result = shim.execute(directive)
            if result == "completed":
                applied_abandoned.append(task_id)
            else:
                errors.append(f"abandon {task_id}: directive {directive.id} {result}")

        for task_id in plan["defer_task_ids"]:
            directive = Directive(
                source="doctor/compact",
                repo=repo_name,
                action=Action.RESCHEDULE_TASK,
                params={"task_id": task_id, "after_hours": str(defer_hours)},
                reason=f"compact: defer overflow task by {defer_hours}h",
            )
            result = shim.execute(directive)
            if result == "completed":
                applied_deferred.append(task_id)
            else:
                errors.append(f"reschedule {task_id}: directive {directive.id} {result}")

    after_tasks = list(load_workgraph(wg_dir).tasks.values())
    score_before = compute_scoreboard(tasks)
    score_after = compute_scoreboard(after_tasks)

    report = {
        "applied": bool(getattr(args, "apply", False)),
        "defer_hours": defer_hours,
        "plan": plan,
        "applied_abandoned": applied_abandoned,
        "applied_deferred": applied_deferred,
        "errors": errors,
        "scoreboard_before": score_before,
        "scoreboard_after": score_after,
    }

    as_json = bool(getattr(args, "json", False))
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(f"Applied: {report['applied']}")
        print(
            f"Plan: abandon={len(plan['abandon_task_ids'])} defer={len(plan['defer_task_ids'])} "
            f"(ready {plan['ready_drift_before']} -> target {plan['max_ready_drift']})"
        )
        if report["applied"]:
            print(f"Applied abandon={len(applied_abandoned)} defer={len(applied_deferred)}")
        if errors:
            print("Errors:")
            for item in errors[:8]:
                print(f"- {item}")
        print(
            "Scoreboard: "
            f"{score_before.get('status')} -> {score_after.get('status')}, "
            f"ready_drift {score_before.get('ready_drift')} -> {score_after.get('ready_drift')}"
        )

    if errors:
        return ExitCode.usage
    return ExitCode.ok


def cmd_doctor(args: argparse.Namespace) -> int:
    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent
    policy = load_drift_policy(wg_dir)
    notes: list[str] = []

    if bool(getattr(args, "fix", False)):
        rc = _repair_wrappers(wg_dir=wg_dir)
        if rc != ExitCode.ok:
            notes.append("wrapper repair failed")
        ensured = _maybe_auto_ensure_contracts(wg_dir=wg_dir, project_dir=project_dir, policy=policy)
        if ensured.get("error"):
            notes.append("contract auto-ensure failed during fix")

    report = _doctor_report(wg_dir=wg_dir, policy=policy)
    if notes:
        report["notes"] = notes

    as_json = bool(getattr(args, "json", False))
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(f"Doctor status: {report['status']}")
        score = report.get("scoreboard") or {}
        print(
            "Scoreboard: "
            f"active={score.get('active_tasks', 0)} "
            f"active_drift={score.get('active_drift', 0)} "
            f"ready_drift={score.get('ready_drift', 0)} "
            f"contract_coverage={float(score.get('active_contract_coverage', 0.0)):.2f}"
        )
        issues = report.get("issues") or []
        if issues:
            print("Issues:")
            for issue in issues:
                print(f"- [{issue['severity']}] {issue['kind']}: {issue['message']}")
        else:
            print("Issues: none")

    return ExitCode.findings if report.get("status") != "healthy" else ExitCode.ok
