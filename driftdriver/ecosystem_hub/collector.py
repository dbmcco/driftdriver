# ABOUTME: Thin async facade for ecosystem snapshot collection.
# ABOUTME: Provides collect_once() and wg-CLI task loader used by snapshot.py.

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from driftdriver.workgraph import find_workgraph_dir


def load_tasks_via_wg_cli(repo_path: Path) -> dict[str, dict[str, Any]]:
    """Return a tasks dict keyed by task id using ``wg list --json``.

    Maps the ``blocked_by`` field returned by the CLI to ``after`` so the
    output shape matches what snapshot.py expects from ``load_workgraph``.

    Falls back to direct ``graph.jsonl`` reading (via driftdriver's own
    ``load_workgraph``) when the ``wg`` CLI is unavailable or exits non-zero.
    This handles test environments where repos have ``graph.jsonl`` but no
    running ``wg`` daemon, and repos in the driftdriver JSONL format.

    Returns an empty dict when neither wg CLI nor graph.jsonl is available.
    """
    try:
        wg_dir = find_workgraph_dir(repo_path)
    except FileNotFoundError:
        wg_dir = repo_path / ".workgraph"
    # Primary path: wg list --json
    try:
        result = subprocess.run(
            ["wg", "--dir", str(wg_dir), "list", "--json"],
            capture_output=True,
            text=True,
            cwd=str(wg_dir.parent),
            timeout=10,
        )
        if result.returncode == 0:
            raw: list[dict[str, Any]] = json.loads(result.stdout)
            tasks: dict[str, dict[str, Any]] = {}
            for item in raw:
                tid = str(item.get("id") or "").strip()
                if not tid:
                    continue
                task: dict[str, Any] = dict(item)
                # Map blocked_by → after so snapshot.py helpers work unchanged.
                task.setdefault("after", task.get("blocked_by") or [])
                tasks[tid] = task
            return tasks
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError, TypeError):
        pass

    # Fallback: read graph.jsonl directly (test environments / driftdriver format).
    if (wg_dir / "graph.jsonl").exists():
        try:
            from driftdriver.workgraph import load_workgraph

            wg = load_workgraph(wg_dir)
            return {
                tid: {**task, "after": task.get("after") or task.get("blocked_by") or []}
                for tid, task in wg.tasks.items()
            }
        except Exception:
            pass

    return {}


async def collect_once(
    project_dir: Path | None = None,
    workspace_root: Path | None = None,
    ecosystem_toml: Path | None = None,
    include_updates: bool = False,
    max_next: int = 5,
    central_repo: Path | None = None,
) -> dict[str, Any]:
    """Collect a full ecosystem snapshot and return it as a dict.

    Wraps :func:`driftdriver.ecosystem_hub.snapshot.write_snapshot_once`
    with sensible defaults so it can be called with no arguments in tests
    or from the CLI.
    """
    from driftdriver.ecosystem_hub.snapshot import write_snapshot_once
    from driftdriver.ecosystem_hub.discovery import _load_ecosystem_repos

    if project_dir is None:
        project_dir = Path.cwd()
    if workspace_root is None:
        workspace_root = project_dir.parent

    return write_snapshot_once(
        project_dir=project_dir,
        workspace_root=workspace_root,
        ecosystem_toml=ecosystem_toml,
        include_updates=include_updates,
        max_next=max_next,
        central_repo=central_repo,
    )
