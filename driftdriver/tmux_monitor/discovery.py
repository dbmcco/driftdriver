# ABOUTME: tmux session/pane discovery and pipe-pane lifecycle management.
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PaneInfo:
    pane_id: str
    session: str
    window: int
    pane_index: int
    tty: str
    cwd: str
    title: str = ""
    current_command: str = ""

    @property
    def qualified_id(self) -> str:
        return f"{self.session}:{self.window}.{self.pane_index}"

    @property
    def log_filename(self) -> str:
        return f"{self.session}_{self.window}.{self.pane_index}.log"


def _tmux_out(*args: str) -> str:
    result = subprocess.run(
        ["tmux", *args],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def list_sessions() -> list[str]:
    out = _tmux_out("list-sessions", "-F", "#{session_name}")
    if not out:
        return []
    return out.splitlines()


def list_panes(session: str) -> list[PaneInfo]:
    fmt = "#{pane_id}:#{session_name}:#{window_index}:#{pane_index}:#{pane_tty}:#{pane_current_path}:#{pane_title}:#{pane_current_command}"
    out = _tmux_out("list-panes", "-t", session, "-F", fmt)
    if not out:
        return []
    panes = []
    for line in out.splitlines():
        parts = line.split(":", 7)
        if len(parts) != 8:
            continue
        panes.append(PaneInfo(
            pane_id=parts[0],
            session=parts[1],
            window=int(parts[2]),
            pane_index=int(parts[3]),
            tty=parts[4],
            cwd=parts[5],
            title=parts[6],
            current_command=parts[7],
        ))
    return panes


def attach_pipe(pane_id: str, log_path: Path) -> bool:
    if not log_path.parent.exists():
        log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.touch()
    result = subprocess.run(
        ["tmux", "pipe-pane", "-t", pane_id, f"cat >> {log_path}"],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.returncode == 0


def detach_pipe(pane_id: str) -> bool:
    result = subprocess.run(
        ["tmux", "pipe-pane", "-t", pane_id],
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.returncode == 0


def capture_pane(pane_id: str, lines: int = 200) -> str:
    out = _tmux_out("capture-pane", "-t", pane_id, "-p", "-S", f"-{lines}")
    return out


def discover_all() -> dict[str, list[PaneInfo]]:
    sessions: dict[str, list[PaneInfo]] = {}
    for sess in list_sessions():
        sessions[sess] = list_panes(sess)
    return sessions
