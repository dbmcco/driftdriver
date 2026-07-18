# ABOUTME: Reap stale claude --print workers whose Workgraph tasks are no longer active.
# ABOUTME: Protects the coordinator and records kill/skip outcomes for watchdog status.

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    elapsed_seconds: int
    task_id: str | None
    cmdline: str


@dataclass
class ReaperResult:
    killed: int = 0
    skipped: int = 0
    kills: list[dict[str, Any]] = field(default_factory=list)


def _parse_elapsed(value: str) -> int | None:
    """Parse ps elapsed-time output in seconds or [[days-]hours:]minutes:seconds."""
    value = value.strip()
    if value.isdigit():
        return int(value)

    days = 0
    if "-" in value:
        day_text, value = value.split("-", 1)
        if not day_text.isdigit():
            return None
        days = int(day_text)

    parts = value.split(":")
    if not all(part.isdigit() for part in parts) or len(parts) > 3:
        return None
    numbers = [int(part) for part in parts]
    if len(numbers) == 3:
        hours, minutes, seconds = numbers
    elif len(numbers) == 2:
        hours, minutes, seconds = 0, numbers[0], numbers[1]
    else:
        hours, minutes, seconds = 0, 0, numbers[0]
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def parse_claude_processes(lines: list[str], *, min_age_seconds: int) -> list[ProcessInfo]:
    """Parse ps rows and return only old, non-coordinator claude workers."""
    processes: list[ProcessInfo] = []
    for line in lines:
        parts = line.strip().split(maxsplit=2)
        if len(parts) != 3:
            continue
        pid_text, elapsed_text, cmdline = parts
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        elapsed_seconds = _parse_elapsed(elapsed_text)
        if elapsed_seconds is None or elapsed_seconds < min_age_seconds:
            continue
        if "--print" not in cmdline or not re.search(r"(?:^|\s|/)claude(?:\s|$)", cmdline):
            continue
        # The coordinator speaks the stream-json protocol and must never be reaped.
        if re.search(r"(?:^|\s)--input-format(?:=|\s+)stream-json(?:\s|$)", cmdline):
            continue
        task_match = re.search(r"(?:^|\s)--task(?:=|\s+)(\S+)", cmdline)
        processes.append(
            ProcessInfo(
                pid=pid,
                elapsed_seconds=elapsed_seconds,
                task_id=task_match.group(1) if task_match else None,
                cmdline=cmdline,
            )
        )
    return processes


def _read_task_statuses(wg_dir: Path) -> dict[str, str]:
    graph_path = wg_dir / "graph.jsonl"
    if not graph_path.exists():
        return {}
    statuses: dict[str, str] = {}
    for line in graph_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        task_id = row.get("id")
        status = row.get("status")
        if task_id is not None and isinstance(status, str):
            statuses[str(task_id)] = status
    return statuses


def classify_process(process: ProcessInfo, wg_dir: Path) -> str:
    """Return ``kill`` for orphaned/terminal workers, otherwise ``skip``."""
    if process.task_id is None:
        return "kill"
    status = _read_task_statuses(wg_dir).get(process.task_id)
    if status is None:
        return "kill"
    if status in {"done", "failed", "abandoned", "completed"}:
        return "kill"
    return "skip"


def _get_ps_output() -> list[str]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,etimes=,command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return []
    return result.stdout.splitlines()


def _kill_process(pid: int) -> None:
    os.kill(pid, signal.SIGTERM)


def _write_log(wg_dir: Path, entry: dict[str, Any]) -> None:
    wg_dir.mkdir(parents=True, exist_ok=True)
    with (wg_dir / "zombie-reaper.log").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def reap_zombies(*, wg_dir: Path, min_age_seconds: int, dry_run: bool = False) -> ReaperResult:
    """Find stale workers, terminate eligible ones, and record each decision."""
    result = ReaperResult()
    for process in parse_claude_processes(_get_ps_output(), min_age_seconds=min_age_seconds):
        action = classify_process(process, wg_dir)
        timestamp = datetime.now(timezone.utc).isoformat()
        if action == "skip":
            result.skipped += 1
            _write_log(
                wg_dir,
                {
                    "action": "skip",
                    "pid": process.pid,
                    "task_id": process.task_id,
                    "timestamp": timestamp,
                },
            )
            continue

        reason = "terminal or unknown Workgraph task"
        if not dry_run:
            _kill_process(process.pid)
        result.killed += 1
        kill_entry = {
            "action": "dry-run" if dry_run else "kill",
            "pid": process.pid,
            "task_id": process.task_id,
            "reason": reason,
            "timestamp": timestamp,
        }
        result.kills.append(kill_entry)
        _write_log(wg_dir, kill_entry)
    return result


def read_reaper_status(wg_dir: Path) -> dict[str, Any]:
    """Summarize persisted reaper decisions."""
    log_path = wg_dir / "zombie-reaper.log"
    status: dict[str, Any] = {
        "last_run": None,
        "total_killed": 0,
        "total_skipped": 0,
    }
    if not log_path.exists():
        return status
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        timestamp = entry.get("timestamp")
        if timestamp:
            status["last_run"] = timestamp
        action = entry.get("action")
        if action in {"kill", "dry-run"}:
            status["total_killed"] += 1
        elif action == "skip":
            status["total_skipped"] += 1
    return status
