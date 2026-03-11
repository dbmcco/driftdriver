# ABOUTME: CLI handler functions for factory brain inspection and control.
# ABOUTME: Provides status, roster, log, enroll, and unenroll subcommands.
from __future__ import annotations

import argparse
import json
from pathlib import Path

from driftdriver.factory_brain.roster import (
    active_repos,
    enroll_repo,
    load_roster,
    save_roster,
    unenroll_repo,
)

DEFAULT_HUB_DATA_DIR = Path.home() / ".config" / "workgraph" / "factory-brain"


def _resolve_hub_dir(args: argparse.Namespace) -> Path:
    """Return the hub data directory from args or the default."""
    return Path(getattr(args, "hub_data_dir", "")) or DEFAULT_HUB_DATA_DIR


def handle_brain_status(args: argparse.Namespace, *, hub_data_dir: Path | None = None) -> int:
    """Show brain state: active/total repos, total invocations, last invocation."""
    hub = hub_data_dir or _resolve_hub_dir(args)
    roster_file = hub / "roster.json"
    roster = load_roster(roster_file)
    active = active_repos(roster)
    total = len(roster.repos)
    active_count = len(active)

    invocations_file = hub / "brain-invocations.jsonl"
    total_invocations = 0
    last_invocation: dict | None = None
    if invocations_file.exists():
        for line in invocations_file.read_text().strip().splitlines():
            line = line.strip()
            if not line:
                continue
            total_invocations += 1
            try:
                last_invocation = json.loads(line)
            except json.JSONDecodeError:
                pass

    print(f"Factory Brain Status")
    print(f"  Repos: {active_count} active / {total} total")
    print(f"  Invocations: {total_invocations}")
    if last_invocation:
        model = last_invocation.get("model", "unknown")
        tier = last_invocation.get("tier", "?")
        print(f"  Last invocation: tier {tier} ({model})")
    else:
        print(f"  Last invocation: (none)")
    return 0


def handle_brain_roster(args: argparse.Namespace, *, hub_data_dir: Path | None = None) -> int:
    """Show enrolled repos as a table."""
    hub = hub_data_dir or _resolve_hub_dir(args)
    roster_file = hub / "roster.json"
    roster = load_roster(roster_file)

    if not roster.repos:
        print("No repos enrolled.")
        return 0

    # Print header
    print(f"{'Name':<25} {'Status':<10} {'Target':<20} {'Path'}")
    print(f"{'-' * 25} {'-' * 10} {'-' * 20} {'-' * 40}")
    for name, entry in sorted(roster.repos.items()):
        status = entry.get("status", "unknown")
        target = entry.get("target", "")
        path = entry.get("path", "")
        print(f"{name:<25} {status:<10} {target:<20} {path}")
    return 0


def handle_brain_log(args: argparse.Namespace, *, hub_data_dir: Path | None = None) -> int:
    """Show recent reasoning from brain-log.md (last 2000 chars)."""
    hub = hub_data_dir or _resolve_hub_dir(args)
    log_file = hub / "brain-log.md"

    if not log_file.exists():
        print("No brain log found.")
        return 0

    content = log_file.read_text()
    if len(content) > 2000:
        print("... (truncated, showing last 2000 chars)\n")
        print(content[-2000:])
    else:
        print(content)
    return 0


def handle_brain_enroll(args: argparse.Namespace, *, hub_data_dir: Path | None = None) -> int:
    """Manually enroll a repo."""
    hub = hub_data_dir or _resolve_hub_dir(args)
    roster_file = hub / "roster.json"
    roster = load_roster(roster_file)
    path = str(Path(args.path).resolve())
    target = getattr(args, "target", "onboarded") or "onboarded"
    name = enroll_repo(roster, path=path, target=target)
    save_roster(roster, roster_file)
    print(f"Enrolled '{name}' (target: {target})")
    return 0


def handle_brain_unenroll(args: argparse.Namespace, *, hub_data_dir: Path | None = None) -> int:
    """Manually unenroll a repo."""
    hub = hub_data_dir or _resolve_hub_dir(args)
    roster_file = hub / "roster.json"
    roster = load_roster(roster_file)
    name = args.name
    if name not in roster.repos:
        print(f"Repo '{name}' not found in roster.")
        return 1
    unenroll_repo(roster, name=name)
    save_roster(roster, roster_file)
    print(f"Unenrolled '{name}' (marked inactive)")
    return 0
