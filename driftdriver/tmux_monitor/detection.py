# ABOUTME: Heuristic agent detection — uses tmux pane metadata and content patterns.
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class PaneClassification:
    pane_type: str
    process_name: str
    pid: int
    tty: str
    title: str

    def to_dict(self) -> dict:
        return {
            "type": self.pane_type,
            "process_name": self.process_name,
            "pid": self.pid,
            "tty": self.tty,
            "title": self.title,
        }


_COMMAND_MAP: dict[str, str] = {
    "opencode": "opencode",
    "codex-aarch64-a": "codex",
    "codex": "codex",
    "claude": "claude-code",
    "kilocode": "kilocode",
    "pi": "pi-dev",
    "pi.dev": "pi-dev",
    "2.1.128": "claude-code",
    "2.1.129": "claude-code",
}

_SHELL_NAMES = {"bash", "zsh", "sh", "fish"}

_TITLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("claude-code", re.compile(r"claude\s*code", re.IGNORECASE)),
]

_CONTENT_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("claude-code", re.compile(r"[╭╰]─", re.MULTILINE)),
    ("codex", re.compile(r"codex\s*>", re.MULTILINE)),
    ("opencode", re.compile(r"opencode", re.IGNORECASE)),
    ("kilocode", re.compile(r"kilocode", re.IGNORECASE)),
    ("pi-dev", re.compile(r"pi\.?dev", re.IGNORECASE)),
]


def _get_foreground_process(tty: str) -> tuple[str, int]:
    if not tty:
        return ("", 0)
    tty_dev = tty.replace("/dev/", "")
    try:
        result = subprocess.run(
            ["ps", "-o", "pid=,comm=", "-t", tty_dev],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = result.stdout.strip().splitlines()
        if not lines:
            return ("", 0)
        last = lines[-1].strip()
        parts = last.split(None, 1)
        if len(parts) == 2:
            return (parts[1], int(parts[0]))
        return ("", 0)
    except (subprocess.TimeoutExpired, ValueError, OSError):
        return ("", 0)


def classify_pane(
    pane_content: str,
    tty: str,
    current_command: str = "",
    pane_title: str = "",
) -> PaneClassification:
    proc_name, pid = _get_foreground_process(tty)

    if current_command:
        cmd_lower = current_command.rsplit("/", 1)[-1].lower()
        for key, agent_type in _COMMAND_MAP.items():
            if key in cmd_lower:
                return PaneClassification(
                    pane_type=agent_type,
                    process_name=current_command,
                    pid=pid,
                    tty=tty,
                    title=pane_title,
                )

    if pane_title:
        for agent_type, pattern in _TITLE_PATTERNS:
            if pattern.search(pane_title):
                return PaneClassification(
                    pane_type=agent_type,
                    process_name=current_command or proc_name.rsplit("/", 1)[-1],
                    pid=pid,
                    tty=tty,
                    title=pane_title,
                )

    if pane_content:
        for agent_type, pattern in _CONTENT_PATTERNS:
            if pattern.search(pane_content):
                return PaneClassification(
                    pane_type=agent_type,
                    process_name=current_command or proc_name.rsplit("/", 1)[-1],
                    pid=pid,
                    tty=tty,
                    title=pane_title,
                )

    if proc_name:
        proc_base = proc_name.rsplit("/", 1)[-1].lower()
        if proc_base in _SHELL_NAMES or current_command.lower() in _SHELL_NAMES:
            return PaneClassification(
                pane_type="shell",
                process_name=current_command or proc_base,
                pid=pid,
                tty=tty,
                title=pane_title,
            )
        return PaneClassification(
            pane_type="unknown",
            process_name=current_command or proc_base,
            pid=pid,
            tty=tty,
            title=pane_title,
        )

    return PaneClassification(
        pane_type="idle",
        process_name=current_command,
        pid=0,
        tty=tty,
        title=pane_title,
    )
