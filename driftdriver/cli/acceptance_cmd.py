# ABOUTME: CLI commands for the deterministic acceptance-criteria gate.
# ABOUTME: check (evaluate a task's verify commands), status (degrade state),
# ABOUTME: reset (clear degrade counter). Used by the speedrift post-task flow.

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from driftdriver.acceptance_gate import check_task, degrade_status, reset_degrade


def register_acceptance_parser(subparsers: Any) -> None:
    """Register the acceptance-gate subcommand group."""
    acceptance = subparsers.add_parser(
        "acceptance",
        help="Acceptance-criteria gate: check a task's verify commands before completion",
    )
    acceptance.add_argument("action", choices=["check", "status", "reset"],
                            help="check=run the gate on a task; status=show degrade state; reset=clear degrade counter")
    acceptance.add_argument("task_id", nargs="?", default=None,
                            help="Task ID (required for check; optional for reset)")
    acceptance.add_argument("--dir", default="", help="Repository root (default: cwd)")
    acceptance.add_argument("--json", action="store_true", help="JSON output")
    acceptance.set_defaults(func=_dispatch)


def _dispatch(args: Any) -> int:
    """Route to the right acceptance subcommand."""
    if args.action == "check":
        if not args.task_id:
            print("Error: task_id is required for 'acceptance check'", file=__import__('sys').stderr)
            return 2
        return cmd_acceptance_check(args)
    if args.action == "status":
        return cmd_acceptance_status(args)
    if args.action == "reset":
        return cmd_acceptance_reset(args)
    return 2


def _wg_dir(repo: Path) -> Path:
    """Resolve the workgraph dir (.workgraph preferred, .wg accepted)."""
    for name in (".workgraph", ".wg"):
        candidate = repo / name
        if candidate.is_dir():
            return candidate
    return repo / ".workgraph"


def cmd_acceptance_check(args: Any) -> int:
    """Run the acceptance gate on a task. Exit 1 if blocked, 0 otherwise."""
    repo = Path(args.dir) if args.dir else Path.cwd()
    wg_dir = _wg_dir(repo)
    result = check_task(wg_dir, args.task_id, record_degrade=False)
    print(json.dumps(result.to_dict(), indent=2))
    return 1 if result.is_blocking else 0


def cmd_acceptance_status(args: Any) -> int:
    """Show the per-repo degrade state."""
    repo = Path(args.dir) if args.dir else Path.cwd()
    status = degrade_status(repo)
    print(json.dumps(status, indent=2))
    return 0


def cmd_acceptance_reset(args: Any) -> int:
    """Reset the degrade counter for a task (or all tasks)."""
    repo = Path(args.dir) if args.dir else Path.cwd()
    task_id = getattr(args, "task_id", None)
    count = reset_degrade(repo, task_id)
    label = task_id or "all tasks"
    print(json.dumps({"reset": count, "scope": label}))
    return 0
