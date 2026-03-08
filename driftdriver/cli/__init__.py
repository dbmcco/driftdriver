# ABOUTME: CLI subpackage entrypoint for driftdriver.
# ABOUTME: Re-exports all public names, argparse setup, main() entrypoint, and thin command handlers.

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from driftdriver import wire
from driftdriver.speedriftd import (
    run_runtime_cycle,
    run_runtime_loop,
)
from driftdriver.speedriftd_state import (
    load_runtime_snapshot,
    write_control_state,
)
from driftdriver.workgraph import find_workgraph_dir

# -- Re-export everything that was previously importable from driftdriver.cli --

from .check import (
    COMPLEXITY_KEYWORDS,
    ExitCode,
    FULL_SUITE_TRIGGER_FENCES,
    FULL_SUITE_TRIGGER_PHRASES,
    INTERNAL_LANES,
    LANE_STRATEGIES,
    OPTIONAL_PLUGINS,
    _count_contract_compliance,
    _ensure_breaker_task,
    _ensure_wg_init,
    _extract_contract_int,
    _load_task,
    _mode_flags,
    _ordered_optional_plugins,
    _plugin_cmd,
    _plugin_supports_json,
    _run,
    _run_internal_lane,
    _run_optional_plugin_json,
    _run_optional_plugin_text,
    _select_optional_plugins,
    _should_run_full_suite,
    _task_has_fence,
    _task_text,
    cmd_check,
    cmd_updates,
)
from ._helpers import (
    _collect_findings,
    _compute_loop_safety,
    _dedupe_strings,
    _ensure_update_followup_task,
    _maybe_auto_ensure_contracts,
    _normalize_actions,
    _parse_watch_repo,
    _parse_watch_report,
    _resolve_update_sources,
    _run_update_preflight,
    _update_errors,
    _wg_log_message,
    _wrapper_commands_available,
)
from .doctor import (
    _compact_plan,
    _doctor_report,
    _repair_wrappers,
    cmd_compact,
    cmd_doctor,
    cmd_queue,
)
from .install_cmd import cmd_install
from .run import (
    _invoke_check_json,
    cmd_factory,
    cmd_orchestrate,
    cmd_run,
)


# ---------------------------------------------------------------------------
# Wire subcommands (thin wrappers delegating to driftdriver.wire)
# ---------------------------------------------------------------------------

def cmd_wire_verify(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_verify(project_dir)
    print(json.dumps(result))
    return 0 if result.get("passed") else 1


def cmd_wire_loop_check(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_loop_check(project_dir, args.tool_name, args.tool_input)
    print(json.dumps(result))
    return 1 if result.get("detected") else 0


def cmd_wire_enrich(args: argparse.Namespace) -> int:
    result = wire.cmd_enrich(args.task_id, args.task_description, args.project, [])
    print(json.dumps(result))
    return 0


def cmd_wire_bridge(args: argparse.Namespace) -> int:
    result = wire.cmd_bridge(Path(args.events_file), args.session_id, args.project)
    print(json.dumps(result))
    return 0


def cmd_wire_distill(args: argparse.Namespace) -> int:
    result = wire.cmd_distill([], [])
    print(json.dumps(result))
    return 0


def cmd_wire_rollback_eval(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_rollback_eval(args.drift_score, args.task_id, project_dir)
    print(json.dumps(result))
    return 0


def cmd_wire_outcome(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_outcome(
        project_dir,
        args.task_id,
        args.lane,
        args.finding_key,
        args.recommendation,
        args.action_taken,
        args.outcome,
    )
    print(json.dumps(result))
    return 0 if result.get("recorded") else 1


def cmd_save_check_snapshot(args: argparse.Namespace) -> int:
    from driftdriver.outcome_feedback import save_check_snapshot

    project_dir = Path(args.dir) if args.dir else Path.cwd()
    wg_dir = project_dir / ".workgraph"

    raw = sys.stdin.read()
    try:
        check_data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON on stdin: {exc}"}))
        return 1

    path = save_check_snapshot(wg_dir, args.task_id, check_data)
    print(json.dumps({"saved": True, "task_id": args.task_id, "path": str(path)}))
    return 0


def cmd_outcome_from_check(args: argparse.Namespace) -> int:
    from driftdriver.outcome_feedback import (
        load_check_snapshot,
        record_outcomes_from_check,
    )

    project_dir = Path(args.dir) if args.dir else Path.cwd()
    wg_dir = project_dir / ".workgraph"

    raw = sys.stdin.read()
    try:
        post_check = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON on stdin: {exc}"}))
        return 1

    pre_check = load_check_snapshot(wg_dir, args.task_id)
    if pre_check is None:
        print(json.dumps({"recorded": 0, "reason": "no pre-check snapshot found"}))
        return 0

    actor_id = getattr(args, "actor_id", "") or ""
    results = record_outcomes_from_check(
        project_dir=project_dir,
        task_id=args.task_id,
        pre_check=pre_check,
        post_check=post_check,
        actor_id=actor_id,
    )
    print(json.dumps({"recorded": len(results), "outcomes": results}))
    return 0


def cmd_wire_record_event(args: argparse.Namespace) -> int:
    result = wire.cmd_record_event(
        args.event_type,
        args.content,
        session_id=args.session_id or "",
        project=args.project or "",
    )
    print(json.dumps(result))
    return 0 if result.get("recorded") else 1


def cmd_quality(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    outcomes_path = project_dir / ".workgraph" / "drift-outcomes.jsonl"
    action = args.action

    try:
        from driftdriver.quality_signal import (
            compute_actor_quality,
            compute_all_actor_qualities,
            format_quality_briefing,
        )
    except ImportError:
        print(json.dumps({"error": "quality_signal module not available"}))
        return 1

    if action == "briefing":
        actor_id = args.actor_id or ""
        quality = compute_actor_quality(outcomes_path, actor_id, window_days=args.window_days)
        briefing = format_quality_briefing(quality)
        if args.json if hasattr(args, "json") else False:
            print(json.dumps({
                "actor_id": quality.actor_id,
                "score": quality.quality_score,
                "trend": quality.trend,
                "total_outcomes": quality.total_outcomes,
                "briefing": briefing,
            }))
        else:
            print(briefing)
        return 0

    if action in ("scores", "all"):
        qualities = compute_all_actor_qualities(outcomes_path, window_days=args.window_days)
        entries = [
            {
                "actor_id": q.actor_id,
                "actor_class": q.actor_class,
                "score": round(q.quality_score, 3),
                "trend": q.trend,
                "total_outcomes": q.total_outcomes,
                "resolved": round(q.resolved_rate, 3),
                "ignored": round(q.ignored_rate, 3),
                "worsened": round(q.worsened_rate, 3),
            }
            for q in qualities
        ]
        print(json.dumps(entries, indent=2))
        return 0

    return 1


def cmd_presence(args: argparse.Namespace) -> int:
    from driftdriver.actor import Actor
    from driftdriver.presence import (
        active_actors,
        gc_stale_presence,
        read_all_presence,
        remove_presence,
        write_heartbeat,
    )

    project_dir = Path(args.dir) if args.dir else Path.cwd()
    action = args.action

    if action == "register":
        actor_id = args.actor_id or f"session-{os.getpid()}"
        actor = Actor(
            id=actor_id,
            actor_class=args.actor_class,
            name=args.name or args.actor_class,
            repo=project_dir.name,
        )
        rec = write_heartbeat(project_dir, actor, current_task=args.task)
        print(json.dumps({"registered": True, "actor_id": actor.id, "started_at": rec.started_at}))
        return 0

    if action == "heartbeat":
        actor_id = args.actor_id or f"session-{os.getpid()}"
        actor = Actor(
            id=actor_id,
            actor_class=args.actor_class,
            name=args.name or args.actor_class,
            repo=project_dir.name,
        )
        rec = write_heartbeat(project_dir, actor, current_task=args.task)
        print(json.dumps({"updated": True, "actor_id": actor.id, "last_heartbeat": rec.last_heartbeat}))
        return 0

    if action == "deregister":
        actor_id = args.actor_id or f"session-{os.getpid()}"
        removed = remove_presence(project_dir, actor_id)
        print(json.dumps({"deregistered": removed, "actor_id": actor_id}))
        return 0

    if action == "list":
        records = active_actors(project_dir, max_age_seconds=args.max_age)
        entries = [
            {"id": r.actor.id, "name": r.actor.name, "class": r.actor.actor_class,
             "task": r.current_task, "status": r.status, "last_heartbeat": r.last_heartbeat}
            for r in records
        ]
        print(json.dumps(entries, indent=2))
        return 0

    if action == "gc":
        removed = gc_stale_presence(project_dir, max_age_seconds=args.max_age)
        print(json.dumps({"removed": removed}))
        return 0

    return 1


def cmd_profile(args: argparse.Namespace) -> int:
    print("Profile command will be rebuilt in the Learning service.")
    return 0


def _parse_ready_output(stdout: str) -> list[dict]:
    """Parse the text output of 'wg ready' into task dicts."""
    tasks: list[dict] = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("Ready tasks:"):
            continue
        parts = line.split(" - ", 1)
        if len(parts) == 2:
            task_id = parts[0].strip()
            title = parts[1].strip()
            tasks.append({"id": task_id, "title": title, "description": ""})
    return tasks


def _get_ready_tasks(project_dir: Path) -> list[dict]:
    """Run ``wg ready`` and return list of task dicts with id, title, description."""
    result = subprocess.run(
        ["wg", "ready"],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
    )
    if result.returncode != 0:
        return []
    return _parse_ready_output(result.stdout)


def cmd_ready(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    tasks = _get_ready_tasks(project_dir)
    if args.json:
        print(json.dumps(tasks))
    else:
        for t in tasks:
            print(f"  {t.get('id', '?')}  {t.get('title', '')}")
    return 0


def cmd_wire_prime(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_prime(project_dir)
    print(result)
    return 0


def cmd_wire_recover(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_recover(project_dir)
    print(json.dumps([r.__dict__ if hasattr(r, "__dict__") else r for r in result]))
    return 0


def cmd_wire_scope_check(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    patterns = args.allowed_patterns.split(",") if args.allowed_patterns else []
    result = wire.cmd_scope_check(project_dir, patterns)
    print(result)
    return 0


def cmd_wire_reflect(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_reflect(project_dir)
    print(result)
    return 0


def cmd_report_cli(args: argparse.Namespace) -> int:
    """Generate session report, flush events, export knowledge."""
    from driftdriver.wire import cmd_report

    project_dir = Path(args.dir) if args.dir else Path.cwd()
    session_id = args.session_id or os.environ.get("CLAUDE_SESSION_ID", "unknown")
    project = args.project or project_dir.name
    result = cmd_report(project_dir, session_id, project, flush=args.flush, push=args.push)
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    else:
        print(f"Session: {result['session_id']}")
        print(f"Events: {result['events_read']} read, {result['events_written']} written, {result['duplicates_skipped']} dupes")
        if result.get('drift_findings_read'):
            print(f"Drift findings: {result['drift_findings_read']} read, {result['drift_findings_written']} written")
        if result.get('chat_messages_read'):
            print(f"Chat history: {result['chat_messages_read']} read, {result['chat_messages_written']} written")
        if result['knowledge_exported']:
            print(f"Knowledge: {result['knowledge_exported']} entries exported")
        if result['pushed_to_central']:
            print("Pushed to central repo")
    return 0


def cmd_run_validation_gates(args: argparse.Namespace) -> int:
    from driftdriver.directives import DirectiveLog
    from driftdriver.validation_gates import check_validation_gates

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    log = DirectiveLog(wg_dir / "service" / "directives")

    # Load the task via wg show --json
    task_id = args.task_id
    result_proc = subprocess.run(
        ["wg", "show", task_id, "--json"],
        capture_output=True,
        text=True,
        cwd=str(wg_dir.parent),
    )
    if result_proc.returncode != 0:
        print(json.dumps({"error": f"could not load task {task_id}"}))
        return 1
    try:
        task = json.loads(result_proc.stdout)
    except json.JSONDecodeError:
        print(json.dumps({"error": f"invalid JSON from wg show {task_id}"}))
        return 1

    result = check_validation_gates(task=task, wg_dir=wg_dir, directive_log=log)
    print(json.dumps(result))
    return 0


def cmd_decompose(args: argparse.Namespace) -> int:
    from driftdriver.decompose import decompose_goal
    from driftdriver.directives import DirectiveLog

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    log = DirectiveLog(wg_dir / "service" / "directives")
    result = decompose_goal(
        goal=args.goal,
        wg_dir=wg_dir,
        directive_log=log,
        repo=args.repo,
        context=args.context,
    )
    if getattr(args, "json", False):
        print(json.dumps(result))
    else:
        print(f"Decomposed into {result['task_count']} tasks")
    return 0


def cmd_ecosystem_hub_proxy(args: argparse.Namespace) -> int:
    from driftdriver.ecosystem_hub import main as ecosystem_hub_main

    forwarded = list(getattr(args, "ecosystem_hub_args", []) or [])
    if not forwarded:
        forwarded = ["--help"]
    return int(ecosystem_hub_main(forwarded))


# ---------------------------------------------------------------------------
# Speedriftd command (kept here for test patch compatibility --
# tests patch driftdriver.cli.write_control_state etc.)
# ---------------------------------------------------------------------------

def cmd_speedriftd(args: argparse.Namespace) -> int:
    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent
    from driftdriver.policy import load_drift_policy
    policy = load_drift_policy(wg_dir)
    cfg = dict(getattr(policy, "speedriftd", {}) or {})
    control_changed = False

    if (
        getattr(args, "set_mode", None) is not None
        or getattr(args, "lease_owner", None) is not None
        or bool(getattr(args, "release_lease", False))
        or getattr(args, "lease_ttl_seconds", None) is not None
    ):
        write_control_state(
            project_dir,
            policy=policy,
            mode=getattr(args, "set_mode", None),
            lease_owner=getattr(args, "lease_owner", None),
            lease_ttl_seconds=getattr(args, "lease_ttl_seconds", None),
            release_lease=bool(getattr(args, "release_lease", False)),
            source="cli",
            reason=str(getattr(args, "reason", "") or ""),
        )
        control_changed = True

    action = str(getattr(args, "action", "status") or "status")
    if action == "status":
        snapshot = load_runtime_snapshot(project_dir)
        if not snapshot or bool(getattr(args, "refresh", False)) or control_changed:
            snapshot = run_runtime_cycle(project_dir, policy=policy)
    elif action == "once":
        snapshot = run_runtime_cycle(project_dir, policy=policy)
    else:
        snapshot = run_runtime_loop(
            project_dir,
            interval_seconds=max(1, int(getattr(args, "interval_seconds", cfg.get("interval_seconds", 30)))),
            max_cycles=max(0, int(getattr(args, "max_cycles", 0))),
            policy=policy,
        )

    if bool(getattr(args, "json", False)):
        print(json.dumps(snapshot, indent=2, sort_keys=False))
        return ExitCode.ok

    print(f"speedriftd repo: {snapshot.get('repo', project_dir.name)}")
    print(f"Daemon state: {snapshot.get('daemon_state', 'unknown')}")
    control = snapshot.get("control") if isinstance(snapshot.get("control"), dict) else {}
    print(f"Control mode: {control.get('mode', 'observe')}")
    if control.get("lease_owner"):
        print(f"Lease owner: {control.get('lease_owner')}")
    print(f"Active workers: {len(snapshot.get('active_workers') or [])}")
    print(f"Ready tasks: {len(snapshot.get('ready_tasks') or [])}")
    stalled = snapshot.get("stalled_task_ids") or []
    print(f"Stalled tasks: {len(stalled)}")
    if stalled:
        print(f"- {', '.join(str(item) for item in stalled[:6])}")
    print(f"Next action: {snapshot.get('next_action', '')}")
    return ExitCode.ok


# ---------------------------------------------------------------------------
# Autopilot command
# ---------------------------------------------------------------------------

def _autopilot_dir(project_dir: Path) -> Path:
    """Get the autopilot state directory."""
    return project_dir / ".workgraph" / ".autopilot"


def _ensure_autopilot_dir(project_dir: Path) -> Path:
    """Ensure autopilot state directory exists and return it."""
    d = _autopilot_dir(project_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_worker_event(project_dir: Path, worker: Any, event: str) -> None:
    """Append a worker event to workers.jsonl."""
    import time as _time

    d = _ensure_autopilot_dir(project_dir)
    entry = {
        "ts": _time.time(),
        "event": event,
        "task_id": worker.task_id,
        "task_title": worker.task_title,
        "worker_name": worker.worker_name,
        "session_id": worker.session_id,
        "started_at": worker.started_at,
        "status": worker.status,
        "drift_fail_count": worker.drift_fail_count,
        "drift_findings": worker.drift_findings,
    }
    with open(d / "workers.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")


def _save_run_state(project_dir: Path, run: Any) -> None:
    """Save current run state as JSON snapshot."""
    import time as _time

    d = _ensure_autopilot_dir(project_dir)
    state = {
        "ts": _time.time(),
        "goal": run.config.goal,
        "loop_count": run.loop_count,
        "completed_tasks": sorted(run.completed_tasks),
        "failed_tasks": sorted(run.failed_tasks),
        "escalated_tasks": sorted(run.escalated_tasks),
        "started_at": run.started_at,
        "workers": {
            tid: {
                "task_id": ctx.task_id,
                "task_title": ctx.task_title,
                "worker_name": ctx.worker_name,
                "session_id": ctx.session_id,
                "started_at": ctx.started_at,
                "status": ctx.status,
                "drift_fail_count": ctx.drift_fail_count,
                "drift_findings": ctx.drift_findings,
            }
            for tid, ctx in run.workers.items()
        },
    }
    (d / "run-state.json").write_text(json.dumps(state, indent=2))


def _clear_run_state(project_dir: Path) -> None:
    """Remove run state files (for fresh start)."""
    d = _autopilot_dir(project_dir)
    for name in ("run-state.json", "workers.jsonl"):
        f = d / name
        if f.exists():
            f.unlink()


def cmd_autopilot(args: argparse.Namespace) -> int:
    """Run the project autopilot."""
    from driftdriver.project_autopilot import (
        AutopilotConfig,
        AutopilotRun,
        decompose_goal,
        discover_session_driver,
        generate_report,
        run_autopilot_loop,
        run_milestone_review,
    )

    project_dir = Path(args.dir) if args.dir else Path.cwd()
    wg_dir = project_dir / ".workgraph"
    if not wg_dir.exists():
        print("Error: no .workgraph found. Run `wg init` first.", file=sys.stderr)
        return 1

    config = AutopilotConfig(
        project_dir=project_dir,
        max_parallel=args.max_parallel,
        worker_timeout=args.worker_timeout,
        dry_run=args.dry_run,
        goal=args.goal,
        no_peer_dispatch=args.no_peer_dispatch,
    )

    # Step 1: Decompose goal into workgraph tasks (unless --skip-decompose)
    if not args.skip_decompose:
        print(f"[autopilot] Decomposing goal: {args.goal}")
        scripts_dir = discover_session_driver()
        response = decompose_goal(args.goal, project_dir, scripts_dir)
        print(f"[autopilot] Decomposition complete:\n{response[:500]}")

        # Ensure contracts on new tasks
        coredrift = wg_dir / "coredrift"
        if coredrift.exists():
            subprocess.run(
                [str(coredrift), "ensure-contracts", "--apply"],
                capture_output=True,
                text=True,
                cwd=str(project_dir),
            )

    # Clear previous state for fresh run
    _clear_run_state(project_dir)

    # Step 2: Run autopilot loop
    run = AutopilotRun(config=config)
    print("[autopilot] Starting execution loop...")
    run = run_autopilot_loop(run)

    # Persist worker events for completed workers
    for tid, ctx in run.workers.items():
        _save_worker_event(project_dir, ctx, ctx.status)

    # Save final run state
    _save_run_state(project_dir, run)

    # Step 3: Milestone review -- evidence-based verification
    if run.completed_tasks and not args.skip_review:
        scripts_dir = discover_session_driver()
        review = run_milestone_review(run, scripts_dir)
        review_file = (wg_dir / ".autopilot" / "milestone-review.md")
        review_file.parent.mkdir(parents=True, exist_ok=True)
        review_file.write_text(review)
        print(f"[autopilot] Milestone review saved to: {review_file}")

    # Step 4: Generate report
    report = generate_report(run)
    report_path = wg_dir / ".autopilot"
    report_path.mkdir(parents=True, exist_ok=True)
    report_file = report_path / "latest-report.md"
    report_file.write_text(report)

    print(f"\n{report}")
    print(f"Report saved to: {report_file}")

    if run.escalated_tasks:
        print("\n[autopilot] Some tasks need human judgment. Review the report above.")
        return 3

    if run.failed_tasks:
        return 1

    return 0


# ---------------------------------------------------------------------------
# Peer federation commands
# ---------------------------------------------------------------------------

def cmd_peer_list_cli(args: argparse.Namespace) -> int:
    """List workgraph peers."""
    from driftdriver.wire import cmd_peer_list

    project_dir = Path(args.dir) if args.dir else Path.cwd()
    peers = cmd_peer_list(project_dir)
    if not peers:
        print("No peers discovered.")
        return 0

    # Table header
    print(f"{'Name':<20} {'Path':<40} {'Service':<10} {'Description'}")
    print("-" * 90)
    for p in peers:
        svc = "running" if p["service_running"] else "stopped"
        print(f"{p['name']:<20} {p['path']:<40} {svc:<10} {p['description']}")
    return 0


def cmd_peer_health_cli(args: argparse.Namespace) -> int:
    """Check health of all peers."""
    from driftdriver.wire import cmd_peer_health

    project_dir = Path(args.dir) if args.dir else Path.cwd()
    reports = cmd_peer_health(project_dir)
    if not reports:
        print("No peers to check.")
        return 0

    print(f"{'Peer':<20} {'Reachable':<12} {'Service':<12} {'Latency':<12} {'Error'}")
    print("-" * 80)
    for r in reports:
        reachable = "yes" if r["reachable"] else "no"
        svc = "running" if r["service_running"] else "stopped"
        latency = f"{r['latency_ms']}ms"
        print(f"{r['peer']:<20} {reachable:<12} {svc:<12} {latency:<12} {r['error']}")
    return 0


def cmd_health_workers_cli(args: argparse.Namespace) -> int:
    """Check liveness of autopilot workers."""
    from driftdriver.wire import cmd_health_workers

    project_dir = Path(args.dir) if args.dir else Path.cwd()
    workers = cmd_health_workers(project_dir)
    if not workers:
        print("No workers found (no autopilot state).")
        return 0

    print(f"{'Task ID':<20} {'Session':<30} {'Status':<12} {'Last Event':<20} {'Count'}")
    print("-" * 95)
    for w in workers:
        print(f"{w['task_id']:<20} {w['session_id']:<30} {w['status']:<12} {w['last_event_type']:<20} {w['event_count']}")
    return 0


# ---------------------------------------------------------------------------
# Argparse setup
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="driftdriver")
    p.add_argument("--dir", help="Project directory (or .workgraph dir). Defaults to cwd search.")
    p.add_argument("--json", action="store_true", help="JSON output (where supported)")

    sub = p.add_subparsers(dest="cmd", required=True)

    install = sub.add_parser("install", help="Install Driftdriver into a workgraph repo")
    install.add_argument("--coredrift-bin", help="Path to coredrift bin/coredrift (required if not discoverable)")
    install.add_argument("--specdrift-bin", help="Path to specdrift bin/specdrift (optional)")
    install.add_argument("--datadrift-bin", help="Path to datadrift bin/datadrift (optional)")
    install.add_argument("--archdrift-bin", help="Path to archdrift bin/archdrift (optional)")
    install.add_argument("--depsdrift-bin", help="Path to depsdrift bin/depsdrift (optional)")
    install.add_argument("--with-uxdrift", action="store_true", help="Best-effort: enable uxdrift integration if found")
    install.add_argument("--uxdrift-bin", help="Path to uxdrift bin/uxdrift (enables uxdrift integration)")
    install.add_argument(
        "--with-therapydrift",
        action="store_true",
        help="Best-effort: enable therapydrift integration if found",
    )
    install.add_argument("--therapydrift-bin", help="Path to therapydrift bin/therapydrift (enables therapydrift integration)")
    install.add_argument(
        "--with-fixdrift",
        action="store_true",
        help="Best-effort: enable fixdrift integration if found",
    )
    install.add_argument("--fixdrift-bin", help="Path to fixdrift bin/fixdrift (enables fixdrift integration)")
    install.add_argument(
        "--with-yagnidrift",
        action="store_true",
        help="Best-effort: enable yagnidrift integration if found",
    )
    install.add_argument("--yagnidrift-bin", help="Path to yagnidrift bin/yagnidrift (enables yagnidrift integration)")
    install.add_argument(
        "--with-redrift",
        action="store_true",
        help="Best-effort: enable redrift integration if found",
    )
    install.add_argument("--redrift-bin", help="Path to redrift bin/redrift (enables redrift integration)")
    install.add_argument(
        "--with-amplifier-executor",
        action="store_true",
        help="Install .workgraph/executors/amplifier.toml + autostart hooks for Amplifier sessions",
    )
    install.add_argument(
        "--with-claude-code-hooks",
        action="store_true",
        help="Install .claude/hooks.json adapter for Claude Code lifecycle events",
    )
    install.add_argument(
        "--all-clis",
        action="store_true",
        help="Install all CLI adapter hooks at once (claude-code, codex, opencode, amplifier, session-driver)",
    )
    install.add_argument(
        "--with-lessons-mcp",
        action="store_true",
        help="Configure lessons-mcp in .mcp.json in the project root",
    )
    install.add_argument("--json", action="store_true", help="JSON output")
    install.add_argument(
        "--wrapper-mode",
        choices=["auto", "pinned", "portable"],
        default="auto",
        help="Wrapper style: pinned paths (dev) or portable PATH-based (commit-safe). Default: auto.",
    )
    install.add_argument("--no-ensure-contracts", action="store_true", help="Do not inject default contracts into tasks")
    install.set_defaults(func=cmd_install)

    check = sub.add_parser(
        "check",
        help="Unified check (coredrift always; optional drifts selected by lane strategy)",
    )
    check.add_argument("--task", help="Task id to check")
    check.add_argument(
        "--lane-strategy",
        choices=LANE_STRATEGIES,
        default="auto",
        help="Optional lane routing: auto (default), fences, or all.",
    )
    check.add_argument("--json", action="store_true", help="JSON output")
    check.add_argument("--write-log", action="store_true", help="Write summary into wg log")
    check.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for findings")
    check.add_argument("--actor-id", default="", help="Actor ID for authority-gated follow-up creation")
    check.add_argument("--actor-class", default="", help="Actor class (human/interactive/worker/daemon/lane)")
    check.set_defaults(func=cmd_check)

    updates = sub.add_parser("updates", help="Check Speedrift ecosystem repos for upstream updates")
    updates.add_argument("--json", action="store_true", help="JSON output")
    updates.add_argument("--force", action="store_true", help="Ignore interval and check remotes now")
    updates.add_argument(
        "--config",
        help="Path to ecosystem review JSON config (default: .workgraph/.driftdriver/ecosystem-review.json)",
    )
    updates.add_argument(
        "--watch-repo",
        action="append",
        default=[],
        help="Extra repo watch target in the form tool=owner/repo (repeatable)",
    )
    updates.add_argument(
        "--watch-user",
        action="append",
        default=[],
        help="GitHub user to scan for new/updated repos (repeatable)",
    )
    updates.add_argument(
        "--watch-report",
        action="append",
        default=[],
        help="Report URL to watch, optionally named as name=url (repeatable)",
    )
    updates.add_argument(
        "--report-keyword",
        action="append",
        default=[],
        help="Keyword to surface from watched report content (repeatable)",
    )
    updates.add_argument(
        "--user-repo-limit",
        type=int,
        help="Max repos per watched GitHub user to inspect (default: config value or 10)",
    )
    updates.add_argument(
        "--write-review",
        help="Write a markdown review report to this path",
    )
    updates.set_defaults(func=cmd_updates)

    queue = sub.add_parser("queue", help="Show ranked ready drift follow-ups and duplicate groups")
    queue.add_argument("--json", action="store_true", help="JSON output")
    queue.add_argument("--limit", type=int, default=10, help="Maximum queue items to display (default: 10)")
    queue.set_defaults(func=cmd_queue)

    doctor = sub.add_parser("doctor", help="Health audit for wrappers, contracts, drift queue pressure, and loop risk")
    doctor.add_argument("--json", action="store_true", help="JSON output")
    doctor.add_argument("--fix", action="store_true", help="Reinstall wrappers + run contract hygiene before reporting")
    doctor.set_defaults(func=cmd_doctor)

    compact = sub.add_parser(
        "compact",
        help="Compact drift queue by abandoning duplicate follow-ups and deferring overflow ready items",
    )
    compact.add_argument("--json", action="store_true", help="JSON output")
    compact.add_argument("--apply", action="store_true", help="Apply compaction actions (default: dry-run)")
    compact.add_argument(
        "--max-ready",
        type=int,
        help="Ready drift queue cap for overflow defer (default: policy loop_safety.max_ready_drift_followups)",
    )
    compact.add_argument("--defer-hours", type=int, default=24, help="Reschedule overflow items by this many hours")
    compact.set_defaults(func=cmd_compact)

    run_p = sub.add_parser("run", help="One-shot operation: check + normalized actions + next queued drift tasks")
    run_p.add_argument("--task", help="Task id to run")
    run_p.add_argument(
        "--lane-strategy",
        choices=LANE_STRATEGIES,
        default="auto",
        help="Optional lane routing: auto (default), fences, or all.",
    )
    run_p.add_argument("--max-next", type=int, default=3, help="Max queued next actions to print (default: 3)")
    run_p.add_argument("--json", action="store_true", help="JSON output")
    run_p.set_defaults(func=cmd_run)

    factory = sub.add_parser("factory", help="Generate one autonomous factory cycle plan + decision ledger")
    factory.add_argument(
        "--workspace-root",
        default="",
        help="Workspace root containing ecosystem repos (default: parent of project dir)",
    )
    factory.add_argument(
        "--ecosystem-toml",
        default="",
        help="Path to ecosystem.toml (default: <workspace-root>/speedrift-ecosystem/ecosystem.toml)",
    )
    factory.add_argument(
        "--central-repo",
        default="",
        help="Override central register repo path (default: policy reporting.central_repo)",
    )
    factory.add_argument("--skip-updates", action="store_true", help="Skip remote update checks for this cycle")
    factory.add_argument("--max-next", type=int, default=5, help="Max next-work items per repo for snapshot context")
    factory.add_argument("--plan-only", action="store_true", help="Force plan-only mode for this cycle")
    factory.add_argument("--execute", action="store_true", help="Run execute mode with deterministic safe handlers")
    factory.add_argument("--force", action="store_true", help="Run even when [factory].enabled is false")
    factory.add_argument("--emit-followups", action="store_true", help="Create/update local corrective workgraph tasks")
    factory.add_argument("--execute-draft-prs", action="store_true", help="Allow factory executor to open upstream draft PRs")
    factory.add_argument("--no-write-ledger", action="store_true", help="Do not write local/central decision ledger")
    factory.add_argument("--write", default="", help="Write JSON payload to this path")
    factory.add_argument("--max-prompts", type=int, default=8, help="Max prompts to print in text mode")
    factory.add_argument("--json", action="store_true", help="JSON output")
    factory.set_defaults(func=cmd_factory)

    orch = sub.add_parser("orchestrate", help="Run continuous drift monitor+redirect loops (delegates to coredrift)")
    orch.add_argument("--interval", type=int, default=30, help="Monitor poll interval seconds (default: 30)")
    orch.add_argument("--redirect-interval", type=int, default=5, help="Redirect poll interval seconds (default: 5)")
    orch.add_argument("--write-log", action="store_true", help="Write a drift summary to wg log (redirect agent)")
    orch.add_argument("--create-followups", action="store_true", help="Create follow-up tasks (redirect agent)")
    orch.set_defaults(func=cmd_orchestrate)

    verify_p = sub.add_parser("verify", help="Run verification checks on the project")
    verify_p.set_defaults(func=cmd_wire_verify)

    loop_check_p = sub.add_parser("loop-check", help="Record a tool action and detect loops")
    loop_check_p.add_argument("--tool-name", default="unknown", help="Tool name")
    loop_check_p.add_argument("--tool-input", default="", help="Tool input string")
    loop_check_p.set_defaults(func=cmd_wire_loop_check)

    enrich_p = sub.add_parser("enrich", help="Enrich a task contract with prior learnings")
    enrich_p.add_argument("--task-id", default="", help="Task ID")
    enrich_p.add_argument("--task-description", default="", help="Task description")
    enrich_p.add_argument("--project", default="", help="Project name")
    enrich_p.set_defaults(func=cmd_wire_enrich)

    bridge_p = sub.add_parser("bridge", help="Parse events file and emit Lessons MCP calls")
    bridge_p.add_argument("--events-file", default="events.jsonl", help="Path to JSONL events file")
    bridge_p.add_argument("--session-id", default="", help="Session ID")
    bridge_p.add_argument("--project", default="", help="Project name")
    bridge_p.set_defaults(func=cmd_wire_bridge)

    distill_p = sub.add_parser("distill", help="Distill events into knowledge entries")
    distill_p.set_defaults(func=cmd_wire_distill)

    rollback_p = sub.add_parser("rollback-eval", help="Evaluate drift score and return rollback decision")
    rollback_p.add_argument("--drift-score", type=float, default=0.0, help="Drift score (0.0-1.0)")
    rollback_p.add_argument("--task-id", default="", help="Task ID")
    rollback_p.set_defaults(func=cmd_wire_rollback_eval)

    record_event_p = sub.add_parser("record-event", help="Record a single event immediately to lessons.db")
    record_event_p.add_argument("--event-type", required=True, help="Event type (e.g. task_completed, drift_finding)")
    record_event_p.add_argument("--content", required=True, help="Event content/description")
    record_event_p.add_argument("--session-id", default="", help="Session ID")
    record_event_p.add_argument("--project", default="", help="Project name")
    record_event_p.set_defaults(func=cmd_wire_record_event)

    outcome_p = sub.add_parser("outcome", help="Record a drift outcome to the outcomes ledger")
    outcome_p.add_argument("--task-id", required=True, help="Task ID the outcome belongs to")
    outcome_p.add_argument("--lane", required=True, help="Drift lane (e.g. coredrift, specdrift)")
    outcome_p.add_argument("--finding-key", required=True, help="Key identifying the drift finding")
    outcome_p.add_argument("--recommendation", required=True, help="What driftdriver recommended")
    outcome_p.add_argument("--action-taken", required=True, help="What action was actually taken")
    outcome_p.add_argument(
        "--outcome",
        required=True,
        choices=["resolved", "ignored", "worsened", "deferred"],
        help="Outcome of the drift finding",
    )
    outcome_p.set_defaults(func=cmd_wire_outcome)

    save_snap_p = sub.add_parser(
        "save-check-snapshot",
        help="Save a check JSON result (stdin) for later outcome comparison",
    )
    save_snap_p.add_argument("--task-id", required=True, help="Task ID to associate the snapshot with")
    save_snap_p.set_defaults(func=cmd_save_check_snapshot)

    outcome_from_p = sub.add_parser(
        "outcome-from-check",
        help="Compare post-check JSON (stdin) against saved pre-check snapshot and record outcomes",
    )
    outcome_from_p.add_argument("--task-id", required=True, help="Task ID to compare findings for")
    outcome_from_p.add_argument("--actor-id", default="", help="Actor ID to associate outcomes with")
    outcome_from_p.set_defaults(func=cmd_outcome_from_check)

    # -- Quality signal commands --
    quality_p = sub.add_parser("quality", help="Actor quality signal and briefings")
    quality_p.add_argument("action", choices=["briefing", "scores", "all"],
                           help="Quality action: briefing (for one actor), scores (all actors), all (full report)")
    quality_p.add_argument("--actor-id", default="", help="Actor ID for briefing")
    quality_p.add_argument("--window-days", type=int, default=30, help="Lookback window in days")
    quality_p.set_defaults(func=cmd_quality)

    profile_p = sub.add_parser("profile", help="Build and display a project profile report")
    profile_p.set_defaults(func=cmd_profile)

    ready_p = sub.add_parser("ready", help="List ready tasks from the workgraph")
    ready_p.set_defaults(func=cmd_ready)

    prime_p = sub.add_parser("prime", help="Prime knowledge context for current task scope")
    prime_p.set_defaults(func=cmd_wire_prime)

    recover_p = sub.add_parser("recover", help="List interrupted tasks that can be recovered")
    recover_p.set_defaults(func=cmd_wire_recover)

    scope_check_p = sub.add_parser("scope-check", help="Check if current changes are within declared scope")
    scope_check_p.add_argument("--allowed-patterns", default="", help="Comma-separated allowed file patterns")
    scope_check_p.set_defaults(func=cmd_wire_scope_check)

    reflect_p = sub.add_parser("reflect", help="Run self-reflect on recent task")
    reflect_p.set_defaults(func=cmd_wire_reflect)

    run_vg_p = sub.add_parser("run-validation-gates", help="Run validation gates for a completing task")
    run_vg_p.add_argument("--task-id", required=True, help="Task ID to validate")
    run_vg_p.set_defaults(func=cmd_run_validation_gates)

    decompose_p = sub.add_parser("decompose", help="Decompose a goal into workgraph tasks via LLM")
    decompose_p.add_argument("--goal", required=True, help="High-level goal to decompose")
    decompose_p.add_argument("--repo", default="", help="Repo name for directive metadata")
    decompose_p.add_argument("--context", default="", help="Additional context for LLM")
    decompose_p.set_defaults(func=cmd_decompose)

    autopilot_p = sub.add_parser("autopilot", help="Run project autopilot: goal -> tasks -> workers -> drift -> done")
    autopilot_p.add_argument("--goal", required=True, help="High-level goal to decompose and execute")
    autopilot_p.add_argument("--max-parallel", type=int, default=4, help="Max parallel workers (default: 4)")
    autopilot_p.add_argument("--worker-timeout", type=int, default=1800, help="Worker timeout in seconds (default: 1800)")
    autopilot_p.add_argument("--dry-run", action="store_true", help="Print plan without dispatching workers")
    autopilot_p.add_argument("--skip-decompose", action="store_true", help="Skip goal decomposition, use existing wg tasks")
    autopilot_p.add_argument("--skip-review", action="store_true", help="Skip milestone review after completion")
    autopilot_p.add_argument("--no-peer-dispatch", action="store_true", help="Disable cross-repo peer dispatch")
    autopilot_p.set_defaults(func=cmd_autopilot)

    speedriftd_p = sub.add_parser("speedriftd", help="Run the repo-local runtime supervisor shell")
    speedriftd_p.add_argument(
        "action",
        nargs="?",
        choices=["status", "once", "loop"],
        default="status",
        help="status (default), once, or loop",
    )
    speedriftd_p.add_argument(
        "--refresh",
        action="store_true",
        help="Refresh runtime snapshot before returning status",
    )
    speedriftd_p.add_argument(
        "--set-mode",
        choices=["manual", "observe", "supervise", "autonomous"],
        help="Update repo runtime control mode before the selected action runs",
    )
    speedriftd_p.add_argument(
        "--lease-owner",
        default=None,
        help="Set or replace the current repo lease owner",
    )
    speedriftd_p.add_argument(
        "--lease-ttl-seconds",
        type=int,
        default=None,
        help="Lease TTL in seconds (0 = no expiry)",
    )
    speedriftd_p.add_argument(
        "--release-lease",
        action="store_true",
        help="Release any active repo lease before the selected action runs",
    )
    speedriftd_p.add_argument(
        "--reason",
        default="",
        help="Reason to record with a control-mode or lease update",
    )
    speedriftd_p.add_argument(
        "--interval-seconds",
        type=int,
        default=30,
        help="Loop interval seconds for `speedriftd loop` (default: 30)",
    )
    speedriftd_p.add_argument(
        "--max-cycles",
        type=int,
        default=0,
        help="Stop after this many cycles when looping (default: 0 = forever)",
    )
    speedriftd_p.set_defaults(func=cmd_speedriftd)

    # -- Peer federation commands --
    peer_list_p = sub.add_parser("peer-list", help="Discover and list workgraph peers")
    peer_list_p.set_defaults(func=cmd_peer_list_cli)

    peer_health_p = sub.add_parser("peer-health", help="Check health of all known peers")
    peer_health_p.set_defaults(func=cmd_peer_health_cli)

    health_workers_p = sub.add_parser("health-workers", help="Check liveness of autopilot workers")
    health_workers_p.set_defaults(func=cmd_health_workers_cli)

    # -- Reporting commands --
    report_p = sub.add_parser("report", help="Generate session report, flush events, export knowledge")
    report_p.add_argument("--session-id", default="", help="Session ID (defaults to CLAUDE_SESSION_ID env var)")
    report_p.add_argument("--project", default="", help="Project name (defaults to directory name)")
    report_p.add_argument("--flush", action="store_true", help="Flush pending events to lessons DB")
    report_p.add_argument("--push", action="store_true", help="Push report to central repo")
    report_p.set_defaults(func=cmd_report_cli)

    # -- Presence commands --
    presence_p = sub.add_parser("presence", help="Manage actor presence heartbeats")
    presence_p.add_argument("action", choices=["register", "heartbeat", "deregister", "list", "gc"],
                            help="Presence action")
    presence_p.add_argument("--actor-id", default="", help="Actor ID (session ID)")
    presence_p.add_argument("--actor-class", default="interactive", help="Actor class (default: interactive)")
    presence_p.add_argument("--name", default="", help="Actor display name")
    presence_p.add_argument("--task", default="", help="Current task ID (for heartbeat)")
    presence_p.add_argument("--max-age", type=int, default=600, help="Max heartbeat age in seconds (for gc/list)")
    presence_p.set_defaults(func=cmd_presence)

    ecosystem_hub_p = sub.add_parser("ecosystem-hub", help="Proxy to the ecosystem hub service manager")
    ecosystem_hub_p.add_argument("ecosystem_hub_args", nargs=argparse.REMAINDER, help="Arguments for ecosystem hub")
    ecosystem_hub_p.set_defaults(func=cmd_ecosystem_hub_proxy)

    return p


def main(argv: list[str] | None = None) -> int:
    forwarded = list(argv) if argv is not None else sys.argv[1:]
    # Strip the legacy 'wire' prefix — e.g. `driftdriver wire reflect` → `driftdriver reflect`
    if forwarded:
        try:
            wire_idx = forwarded.index("wire")
        except ValueError:
            wire_idx = -1
        if wire_idx != -1:
            forwarded = forwarded[:wire_idx] + forwarded[wire_idx + 1:]
    if forwarded:
        try:
            hub_idx = forwarded.index("ecosystem-hub")
        except ValueError:
            hub_idx = -1
        if hub_idx != -1:
            from driftdriver.ecosystem_hub import main as ecosystem_hub_main

            prefix = forwarded[:hub_idx]
            hub_args = forwarded[hub_idx + 1 :]
            if "--project-dir" not in hub_args:
                if "--dir" in prefix:
                    idx = prefix.index("--dir")
                    if idx + 1 < len(prefix):
                        hub_args = ["--project-dir", prefix[idx + 1], *hub_args]
                else:
                    for item in prefix:
                        if item.startswith("--dir="):
                            hub_args = ["--project-dir", item.split("=", 1)[1], *hub_args]
                            break
            if not hub_args:
                hub_args = ["--help"]
            return int(ecosystem_hub_main(hub_args))
    p = _build_parser()
    args = p.parse_args(forwarded)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
