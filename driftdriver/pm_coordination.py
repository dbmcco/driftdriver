# ABOUTME: PM coordination mode for workgraph-driven agent orchestration
# ABOUTME: Reads wg ready, dispatches workers, monitors progress, chains dependents

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class WorkerAssignment:
    task_id: str
    task_title: str
    worker_name: str
    session_id: str | None = None
    status: str = "pending"  # pending, running, completed, failed


@dataclass
class CoordinationPlan:
    ready_tasks: list[str]
    assignments: list[WorkerAssignment] = field(default_factory=list)
    max_parallel: int = 4


def parse_ready_output(stdout: str) -> list[dict]:
    """Parse the text output of 'wg ready' into task dicts."""
    tasks = []
    for line in stdout.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("Ready tasks:"):
            continue
        # Parse "  task-id - task title" format
        parts = line.split(" - ", 1)
        if len(parts) == 2:
            task_id = parts[0].strip()
            title = parts[1].strip()
            tasks.append({"id": task_id, "title": title, "description": ""})
    return tasks


def get_ready_tasks(project_dir: Path) -> list[dict]:
    """Run `wg ready` and return list of task dicts with id, title, description."""
    result = subprocess.run(
        ["wg", "ready"],
        capture_output=True,
        text=True,
        cwd=str(project_dir),
    )
    if result.returncode != 0:
        return []
    return parse_ready_output(result.stdout)


def plan_dispatch(ready_tasks: list[dict], max_parallel: int = 4) -> CoordinationPlan:
    """Create worker assignments up to max_parallel for the given ready tasks."""
    assignments = [
        WorkerAssignment(
            task_id=task["id"],
            task_title=task.get("title", ""),
            worker_name=f"wg-{task['id']}",
        )
        for task in ready_tasks[:max_parallel]
    ]
    return CoordinationPlan(
        ready_tasks=[t["id"] for t in ready_tasks],
        assignments=assignments,
        max_parallel=max_parallel,
    )


def format_task_prompt(task: dict) -> str:
    """Format a task dict into a worker session prompt including TDD protocol."""
    task_id = task.get("id", "")
    title = task.get("title", "")
    description = task.get("description", "")
    return (
        f"Task ID: {task_id}\n"
        f"Title: {title}\n\n"
        f"{description}\n\n"
        "## Protocol\n"
        "Follow TDD strictly: write failing tests first, verify RED, implement minimal "
        "code to pass, verify GREEN, then run the full suite.\n"
        "When complete, run: wg done\n"
        "Before completing, run: drifts check\n"
    )


def filter_newly_ready(all_ready: list[dict], previously_known: set[str]) -> list[dict]:
    """Filter tasks to only those not in previously_known set."""
    return [t for t in all_ready if t["id"] not in previously_known]


def check_newly_ready(project_dir: Path, previously_known: set[str]) -> list[dict]:
    """Return tasks from `wg ready` that are NOT in previously_known."""
    all_ready = get_ready_tasks(project_dir)
    return filter_newly_ready(all_ready, previously_known)


# ---------------------------------------------------------------------------
# Cross-repo peer dispatch
# ---------------------------------------------------------------------------

import re


@dataclass
class PeerAssignment:
    peer_name: str
    task_id: str
    prompt: str = ""
    status: str = "pending"  # pending, dispatched, completed, failed


def plan_peer_dispatch(
    peer_registry: object,
    ready_tasks: list[dict],
) -> list[PeerAssignment]:
    """Scan task descriptions for @peer:<name> annotations and plan dispatch.

    Args:
        peer_registry: PeerRegistry instance with .peers() method
        ready_tasks: list of task dicts with id, title, description
    """
    peers = {p.name for p in peer_registry.peers()}
    if not peers:
        return []

    assignments: list[PeerAssignment] = []
    pattern = re.compile(r"@peer:(\S+)")

    for task in ready_tasks:
        desc = task.get("description", "")
        match = pattern.search(desc)
        if match:
            peer_name = match.group(1)
            if peer_name in peers:
                assignments.append(PeerAssignment(
                    peer_name=peer_name,
                    task_id=task["id"],
                    prompt=format_task_prompt(task),
                ))
    return assignments


def dispatch_to_peer(
    project_dir: Path,
    peer_name: str,
    task: dict,
    peer_registry: object,
) -> str | None:
    """Dispatch a task to a peer repo via IPC AddTask.

    Returns the remote task_id on success, None on failure.
    """
    from driftdriver.wg_ipc import IpcError, add_task

    socket_path = peer_registry.socket(peer_name)
    if not socket_path:
        return None

    try:
        remote_id = add_task(
            socket_path,
            title=task.get("title", ""),
            description=task.get("description", ""),
            tags=["federation", f"origin:{project_dir.name}"],
            origin=f"peer:{project_dir.name}",
        )
        return remote_id if remote_id else None
    except IpcError:
        return None


def poll_peer_task(
    project_dir: Path,
    peer_name: str,
    task_id: str,
    peer_registry: object,
) -> dict | None:
    """Poll a peer for task status via IPC QueryTask.

    Returns the task dict on success, None on failure.
    """
    from driftdriver.wg_ipc import IpcError, query_task

    socket_path = peer_registry.socket(peer_name)
    if not socket_path:
        return None

    try:
        return query_task(socket_path, task_id)
    except IpcError:
        return None
