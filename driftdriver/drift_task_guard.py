# ABOUTME: Shared guard for drift lane task creation — dedup, cap, and wg compatibility.
# ABOUTME: Prevents feedback loops by capping drift tasks per lane and using --immediate.

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

# Maximum non-terminal drift tasks allowed per lane per repo.
# Once this cap is hit, no new tasks are created for that lane.
DEFAULT_CAP_PER_LANE = 3


def _run_wg(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 40.0,
) -> tuple[int, str, str]:
    """Run a wg command with fallback path resolution."""
    def _invoke(actual_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            actual_cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        proc = _invoke(cmd)
    except FileNotFoundError:
        if cmd and str(cmd[0]) == "wg":
            for candidate in [
                str(Path.home() / ".cargo" / "bin" / "wg"),
                "/opt/homebrew/bin/wg",
                "/usr/local/bin/wg",
            ]:
                if not Path(candidate).exists():
                    continue
                try:
                    proc = _invoke([candidate, *cmd[1:]])
                    return int(proc.returncode), str(proc.stdout or "").strip(), str(proc.stderr or "").strip()
                except FileNotFoundError:
                    continue
        return 127, "", "wg not found"
    except Exception as exc:
        return 1, "", str(exc)
    return int(proc.returncode), str(proc.stdout or "").strip(), str(proc.stderr or "").strip()


def count_active_drift_tasks(
    wg_dir: Path,
    lane_tag: str,
    *,
    cwd: Path | None = None,
) -> int:
    """Count non-terminal tasks tagged with 'drift' and the given lane tag."""
    rc, out, _ = _run_wg(
        ["wg", "--dir", str(wg_dir), "--json", "list"],
        cwd=cwd,
        timeout=30.0,
    )
    if rc != 0:
        return 0
    try:
        tasks = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        return 0
    if not isinstance(tasks, list):
        return 0
    count = 0
    terminal = {"done", "abandoned", "failed"}
    for t in tasks:
        status = str(t.get("status", "")).lower()
        if status in terminal:
            continue
        tags = t.get("tags") or []
        if not isinstance(tags, list):
            continue
        if "drift" in tags and lane_tag in tags:
            count += 1
    return count


def guarded_add_drift_task(
    *,
    wg_dir: Path,
    task_id: str,
    title: str,
    description: str,
    lane_tag: str,
    extra_tags: list[str] | None = None,
    after: str | None = None,
    cwd: Path | None = None,
    cap: int = DEFAULT_CAP_PER_LANE,
) -> str:
    """Create a drift follow-up task with dedup, cap enforcement, and --immediate.

    Returns:
        "created"  - new task added
        "existing" - task_id already exists (dedup hit)
        "capped"   - lane has >= cap active drift tasks
        "error"    - wg add failed
    """
    # 1. Exact-ID dedup: if this task_id already exists in any state, skip.
    show_rc, _, _ = _run_wg(
        ["wg", "--dir", str(wg_dir), "show", task_id, "--json"],
        cwd=cwd,
        timeout=20.0,
    )
    if show_rc == 0:
        return "existing"

    # 2. Cap check: count active (non-terminal) drift tasks for this lane.
    active = count_active_drift_tasks(wg_dir, lane_tag, cwd=cwd)
    if active >= cap:
        return "capped"

    # 3. Build wg add command with --immediate (skip draft-by-default).
    cmd: list[str] = [
        "wg", "--dir", str(wg_dir),
        "add", title,
        "--id", task_id,
        "-d", description,
        "--immediate",
        "-t", "drift",
        "-t", lane_tag,
    ]
    for tag in (extra_tags or []):
        cmd.extend(["-t", tag])
    if after:
        cmd.extend(["--after", after])

    add_rc, _, _ = _run_wg(cmd, cwd=cwd, timeout=30.0)
    return "created" if add_rc == 0 else "error"
