# ABOUTME: Intent CLI subcommand for driftdriver.
# ABOUTME: Provides handle_intent_set() and handle_intent_read() for shell access to continuation intent.
from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from driftdriver.continuation_intent import read_intent, write_intent


def handle_intent_set(args: argparse.Namespace) -> int:
    """Set continuation intent on a repo's control state."""
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    try:
        result = write_intent(
            project_dir,
            intent=args.intent,
            set_by=args.set_by,
            reason=args.reason,
            decision_id=getattr(args, "decision_id", None),
        )
    except ValueError as exc:
        if getattr(args, "json", False):
            print(json.dumps({"error": str(exc)}))
        else:
            print(f"Error: {exc}")
        return 1

    if getattr(args, "json", False):
        print(json.dumps(asdict(result)))
    else:
        print(f"Intent set: {result.intent} (by {result.set_by}) — {result.reason}")
    return 0


def handle_intent_read(args: argparse.Namespace) -> int:
    """Read continuation intent from a repo's control state."""
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    intent = read_intent(project_dir)

    if getattr(args, "json", False):
        if intent is None:
            print(json.dumps(None))
        else:
            print(json.dumps(asdict(intent)))
    else:
        if intent is None:
            print("No continuation intent set.")
        else:
            print(f"Intent: {intent.intent}")
            print(f"Set by: {intent.set_by}")
            print(f"Reason: {intent.reason}")
            print(f"Set at: {intent.set_at}")
            if intent.decision_id:
                print(f"Decision ID: {intent.decision_id}")
    return 0
