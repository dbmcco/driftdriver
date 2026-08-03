# ABOUTME: Workgraph task-query utilities extracted from the retired autopilot.
# ABOUTME: Provides get_ready_tasks + helpers for speedriftd and other consumers.
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from driftdriver.manual_owner import apply_manual_owner_policy
from driftdriver.policy import load_drift_policy


# ---------------------------------------------------------------------------
# Binary resolution helpers
# ---------------------------------------------------------------------------


def _binary_candidates(binary: str) -> list[str]:
    candidates: list[str] = []
    discovered = shutil.which(binary)
    if discovered:
        candidates.append(discovered)
    if binary == "wg":
        candidates.extend(
            [
                str(Path.home() / ".cargo" / "bin" / "wg"),
                "/opt/homebrew/bin/wg",
                "/usr/local/bin/wg",
            ]
        )
        users_root = Path("/Users")
        if users_root.exists():
            for extra in users_root.glob("*/.cargo/bin/wg"):
                candidates.append(str(extra))
    elif binary == "claude":
        candidates.extend(
            [
                str(Path.home() / ".npm-global" / "bin" / "claude"),
                str(Path.home() / ".local" / "bin" / "claude"),
                "/opt/homebrew/bin/claude",
                "/usr/local/bin/claude",
            ]
        )
    return candidates


def _resolve_command(cmd: list[str]) -> list[str]:
    if not cmd:
        return cmd
    binary = str(cmd[0] or "")
    if not binary or "/" in binary:
        return cmd
    seen: set[str] = set()
    for candidate in _binary_candidates(binary):
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        if Path(candidate).exists():
            return [candidate, *cmd[1:]]
    return cmd


def _subprocess_env(
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    env = os.environ.copy()
    path_entries: list[str] = []
    if cwd is not None:
        repo_bin = cwd / ".workgraph" / "bin"
        if repo_bin.is_dir():
            path_entries.append(str(repo_bin))
    for binary in ("wg", "claude"):
        for candidate in _binary_candidates(binary):
            parent = str(Path(candidate).parent)
            if parent and parent not in path_entries:
                path_entries.append(parent)
    current = env.get("PATH", "")
    if current:
        path_entries.extend(part for part in current.split(os.pathsep) if part)
    env["PATH"] = os.pathsep.join(path_entries)
    if extra_env:
        env.update(extra_env)
    return env


# ---------------------------------------------------------------------------
# Command execution
# ---------------------------------------------------------------------------


def _run_command(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    resolved = _resolve_command(cmd)
    try:
        return subprocess.run(
            resolved,
            capture_output=True,
            text=True,
            cwd=str(cwd) if cwd else None,
            timeout=timeout,
            env=_subprocess_env(cwd, extra_env),
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(
            resolved,
            127,
            stdout="",
            stderr=str(exc),
        )


# ---------------------------------------------------------------------------
# Task query functions
# ---------------------------------------------------------------------------


def get_task_details(project_dir: Path, task_id: str) -> dict | None:
    """Get full task details from workgraph."""
    result = _run_command(["wg", "show", task_id], cwd=project_dir)
    if result.returncode != 0:
        return None

    # Parse text output for description
    lines = result.stdout.strip().splitlines()
    title = ""
    description_lines: list[str] = []
    in_description = False

    for line in lines:
        if line.startswith("Title:"):
            title = line.split(":", 1)[1].strip()
        elif line.startswith("Description:"):
            in_description = True
            desc_part = line.split(":", 1)[1].strip()
            if desc_part:
                description_lines.append(desc_part)
        elif in_description:
            if line.startswith(("Status:", "Blocked", "Log:", "Dependencies:")):
                in_description = False
            else:
                description_lines.append(line)

    return {
        "id": task_id,
        "title": title,
        "description": "\n".join(description_lines).strip(),
    }


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


def get_ready_tasks(project_dir: Path) -> list[dict]:
    """Get ready tasks from workgraph with full details."""
    result = _run_command(["wg", "ready"], cwd=project_dir)
    if result.returncode != 0:
        return []

    basic_tasks = _parse_ready_output(result.stdout)
    policy = load_drift_policy(project_dir / ".workgraph")
    detailed: list[dict] = []
    for task in basic_tasks:
        details = get_task_details(project_dir, task["id"])
        prepared = apply_manual_owner_policy(
            details if details else task,
            project_dir,
            policy=policy,
        )
        if prepared is not None:
            detailed.append(prepared)
    return detailed
