# ABOUTME: Run and orchestrate subcommands for driftdriver CLI.
# ABOUTME: One-shot check+action-plan and orchestration delegation.

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

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
