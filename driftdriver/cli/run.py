# ABOUTME: Run, factory, and orchestrate subcommands for driftdriver CLI.
# ABOUTME: One-shot check+action-plan, autonomous factory cycles, and orchestration delegation.

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from driftdriver.factorydrift import (
    build_factory_cycle,
    emit_factory_followups,
    execute_factory_cycle,
    summarize_factory_cycle,
    write_factory_ledger,
)
from driftdriver.health import (
    compute_scoreboard,
    find_duplicate_open_drift_groups,
    rank_ready_drift_queue,
)
from driftdriver.policy import load_drift_policy
from driftdriver.workgraph import find_workgraph_dir, load_workgraph

from .check import ExitCode, _run, cmd_check
from ._helpers import _normalize_actions


def _invoke_check_json(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    capture = io.StringIO()
    with redirect_stdout(capture):
        rc = cmd_check(args)
    raw = capture.getvalue().strip()
    if not raw:
        return (int(rc), {})
    try:
        return (int(rc), json.loads(raw))
    except Exception:
        return (int(rc), {"raw": raw})


def cmd_run(args: argparse.Namespace) -> int:
    if not args.task:
        print("error: --task is required", file=sys.stderr)
        return ExitCode.usage

    check_args = argparse.Namespace(
        dir=args.dir,
        task=args.task,
        lane_strategy=getattr(args, "lane_strategy", "auto"),
        write_log=True,
        create_followups=True,
        json=True,
    )
    rc, check_report = _invoke_check_json(check_args)

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    wg = load_workgraph(wg_dir)
    tasks = list(wg.tasks.values())
    max_next = int(getattr(args, "max_next", 3))
    if max_next < 1:
        max_next = 1
    next_actions = rank_ready_drift_queue(tasks, limit=max_next)
    duplicates = find_duplicate_open_drift_groups(tasks)
    out = {
        "exit_code": rc,
        "check": check_report,
        "next_actions": next_actions,
        "duplicate_open_drift_groups": duplicates,
        "scoreboard": compute_scoreboard(tasks),
    }

    as_json = bool(getattr(args, "json", False))
    if as_json:
        print(json.dumps(out, indent=2, sort_keys=False))
        return int(rc)

    print(f"Run exit code: {rc}")
    action_plan = check_report.get("action_plan") if isinstance(check_report, dict) else None
    if isinstance(action_plan, list) and action_plan:
        print("Normalized actions:")
        for item in action_plan[:5]:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "")
            kind = str(item.get("kind") or "")
            source = str(item.get("source") or "")
            print(f"- {action}: {kind} ({source})")
    else:
        print("Normalized actions: none")

    print("Next actions:")
    if not next_actions:
        print("- none")
    else:
        for item in next_actions:
            print(f"- {item['task_id']} [p={item['priority']}] {item['title']}")

    if duplicates:
        print(f"Duplicate open drift groups: {len(duplicates)}")
    return int(rc)


def cmd_factory(args: argparse.Namespace) -> int:
    from driftdriver.ecosystem_hub import collect_ecosystem_snapshot, resolve_central_repo_path

    if bool(getattr(args, "plan_only", False)) and bool(getattr(args, "execute", False)):
        print("error: --plan-only and --execute are mutually exclusive", file=sys.stderr)
        return ExitCode.usage

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent.resolve()
    policy = load_drift_policy(wg_dir)
    factory_cfg = getattr(policy, "factory", {})
    if not isinstance(factory_cfg, dict):
        factory_cfg = {}

    policy_enabled = bool(factory_cfg.get("enabled", False))
    if not policy_enabled and not bool(getattr(args, "force", False)):
        print(
            "Factory loop is disabled in drift-policy.toml ([factory].enabled = false). "
            "Use --force to generate an on-demand cycle anyway."
        )
        return ExitCode.ok

    workspace_root = (
        Path(str(args.workspace_root)).expanduser().resolve()
        if getattr(args, "workspace_root", "")
        else project_dir.parent
    )
    ecosystem_toml = (
        Path(str(args.ecosystem_toml)).expanduser().resolve()
        if getattr(args, "ecosystem_toml", "")
        else None
    )
    central_repo = resolve_central_repo_path(project_dir, explicit_path=str(getattr(args, "central_repo", "") or ""))
    include_updates = not bool(getattr(args, "skip_updates", False))
    max_next = max(1, int(getattr(args, "max_next", 5)))

    plan_only_override: bool | None = None
    if bool(getattr(args, "plan_only", False)):
        plan_only_override = True
    elif bool(getattr(args, "execute", False)):
        plan_only_override = False

    snapshot = collect_ecosystem_snapshot(
        project_dir=project_dir,
        workspace_root=workspace_root,
        ecosystem_toml=ecosystem_toml,
        include_updates=include_updates,
        max_next=max_next,
        central_repo=central_repo,
    )
    cycle = build_factory_cycle(
        snapshot=snapshot,
        policy=policy,
        project_name=project_dir.name,
        plan_only_override=plan_only_override,
    )
    execution_mode = str(cycle.get("execution_mode") or "plan_only")

    emit_followups = bool(factory_cfg.get("emit_followups", False))
    if bool(getattr(args, "emit_followups", False)):
        emit_followups = True
    followups: dict[str, Any] = {
        "enabled": bool(emit_followups),
        "attempted": 0,
        "created": 0,
        "existing": 0,
        "skipped": 0,
        "errors": [],
        "tasks": [],
    }
    execution: dict[str, Any] = {
        "attempted": 0,
        "executed": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "hard_stop": bool(factory_cfg.get("hard_stop_on_failed_verification", True)),
        "stopped_early": False,
        "stop_reason": "",
        "attempts": [],
        "followups": followups,
    }

    if execution_mode != "plan_only":
        execution = execute_factory_cycle(
            cycle=cycle,
            snapshot=snapshot,
            policy=policy,
            project_dir=project_dir,
            emit_followups=emit_followups,
            max_followups_per_repo=max(1, int(factory_cfg.get("max_followups_per_repo", 2))),
            allow_execute_draft_prs=bool(getattr(args, "execute_draft_prs", False)),
        )
        followups = execution.get("followups") if isinstance(execution.get("followups"), dict) else followups
    elif emit_followups:
        followups = emit_factory_followups(
            cycle=cycle,
            snapshot=snapshot,
            max_followups_per_repo=max(1, int(factory_cfg.get("max_followups_per_repo", 2))),
        )
        execution["followups"] = followups

    summary = summarize_factory_cycle(cycle)

    ledger = {
        "written": False,
        "local_latest": "",
        "local_history": "",
        "central_latest": "",
        "central_history": "",
        "central_written": False,
    }
    if not bool(getattr(args, "no_write_ledger", False)):
        ledger = write_factory_ledger(
            project_dir=project_dir,
            cycle=cycle,
            central_repo=central_repo,
            write_decision_ledger=bool(factory_cfg.get("write_decision_ledger", True)),
        )
        ledger["written"] = True

    payload = {
        "policy_factory_enabled": policy_enabled,
        "forced": bool(getattr(args, "force", False)),
        "summary": summary,
        "cycle": cycle,
        "execution": execution,
        "followups": followups,
        "ledger": ledger,
    }

    write_path = str(getattr(args, "write", "") or "").strip()
    if write_path:
        out = Path(write_path).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, indent=2, sort_keys=False))
        return ExitCode.ok

    print(f"Factory cycle: {summary['cycle_id']}")
    print(f"Execution mode: {summary['execution_mode']}")
    print(f"Execution status: {summary['execution_status']}")
    print(f"Selected repos: {summary['selected_repos']}")
    print(f"Planned actions: {summary['planned_actions']}")
    if summary["execution_mode"] != "plan_only":
        print(
            "Executed actions: "
            f"{summary.get('executed_actions', 0)} "
            f"(failed={summary.get('failed_actions', 0)})"
        )
    if summary.get("next_cycle_hints"):
        print("Hints:")
        for hint in list(summary.get("next_cycle_hints") or [])[:4]:
            print(f"- {hint}")

    if ledger.get("written"):
        print("Decision ledger:")
        local_latest = str(ledger.get("local_latest") or "")
        local_history = str(ledger.get("local_history") or "")
        if local_latest:
            print(f"- local latest: {local_latest}")
        if local_history:
            print(f"- local history: {local_history}")
        if bool(ledger.get("central_written")):
            print(f"- central latest: {ledger.get('central_latest')}")
            print(f"- central history: {ledger.get('central_history')}")

    if emit_followups:
        print("Corrective follow-up tasks:")
        print(
            f"- attempted={followups.get('attempted', 0)} "
            f"created={followups.get('created', 0)} "
            f"existing={followups.get('existing', 0)} "
            f"skipped={followups.get('skipped', 0)}"
        )
        errors = followups.get("errors") if isinstance(followups.get("errors"), list) else []
        if errors:
            print("- errors:")
            for msg in errors[:4]:
                print(f"  - {msg}")

    actions = cycle.get("action_plan")
    max_prompts = max(1, int(getattr(args, "max_prompts", 8)))
    if isinstance(actions, list) and actions:
        print("Action prompts:")
        for row in actions[:max_prompts]:
            if not isinstance(row, dict):
                continue
            repo = str(row.get("repo") or "")
            module = str(row.get("module") or "")
            prompt = str(row.get("prompt") or "")
            print(f"- [{repo}:{module}] {prompt}")
    else:
        print("Action prompts: none")

    return ExitCode.ok


def cmd_orchestrate(args: argparse.Namespace) -> int:
    """
    Run drift "pit wall" loops.

    Today this delegates to baseline coredrift's monitor+redirect orchestrator.
    """

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent

    coredrift = wg_dir / "coredrift"
    if not coredrift.exists():
        print("error: .workgraph/coredrift not found; run driftdriver install first", file=sys.stderr)
        return ExitCode.usage

    cmd = [
        str(coredrift),
        "--dir",
        str(project_dir),
        "orchestrate",
        "--interval",
        str(int(args.interval)),
        "--redirect-interval",
        str(int(args.redirect_interval)),
    ]
    if args.write_log:
        cmd.append("--write-log")
    if args.create_followups:
        cmd.append("--create-followups")

    return int(_run(cmd))
