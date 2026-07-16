# ABOUTME: 'driftdriver graph-dir' subcommand — machine-readable graph directory resolver.
# ABOUTME: Exposes resolve_workgraph_dir() to PlanForge without migrating runtime call sites.
from __future__ import annotations

import argparse
import json
from pathlib import Path

from driftdriver.workgraph import resolve_workgraph_dir


def cmd_graph_dir(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir or Path.cwd()).resolve()
    result = resolve_workgraph_dir(project_dir)
    payload = {
        "project_dir": str(project_dir),
        "graph_dir": str(result.path),
        "initialized": result.initialized,
        "source": result.source,
    }
    if bool(getattr(args, "json", False)):
        print(json.dumps(payload, sort_keys=True))
    else:
        print(result.path)
    return 0


def register_graph_dir_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register 'driftdriver graph-dir' subcommand."""
    graph_dir_p = subparsers.add_parser(
        "graph-dir",
        help="Resolve and report the canonical Workgraph directory",
    )
    graph_dir_p.set_defaults(func=cmd_graph_dir)
