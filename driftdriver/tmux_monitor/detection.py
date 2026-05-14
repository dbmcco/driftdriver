# ABOUTME: Heuristic agent detection — classifies tmux panes by process and content patterns.
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

    def to_dict(self) -> dict:
        return {
            "type": self.pane_type,
            "process_name": self.process_name,
            "pid": self.pid,
            "tty": self.tty,
        }


_AGENT_SIGNATURES: list[tuple[str, re.Pattern[str]]] = [
    ("claude-code", re.compile(r"[╭╰]─", re.MULTILINE)),
    ("claude-code", re.compile(r"^>", re.MULTILINE)),
    ("codex", re.compile(r"codex\s*>", re.MULTILINE)),
    ("opencode", re.compile(r"opencode", re.IGNORECASE)),
    ("kilocode", re.compile(r"kilocode", re.IGNORECASE)),
    ("pi-dev", re.compile(r"pi\.?dev", re.IGNORECASE)),
]

_PROCESS_MAP: dict[str, str] = {
    "claude": "claude-code",
    "codex": "codex",
    "opencode": "opencode",
    "kilocode": "kilocode",
    "pi": "pi-dev",
    "pi.dev": "pi-dev",
}

_SHELL_NAMES = {"bash", "zsh", "sh", "fish"}


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


def _match_content(content: str) -> str | None:
    for name, pattern in _AGENT_SIGNATURES:
        if pattern.search(content):
            return name
    return None


def classify_pane(pane_content: str, tty: str) -> PaneClassification:
    proc_name, pid = _get_foreground_process(tty)

    if proc_name:
        proc_base = proc_name.rsplit("/", 1)[-1].lower()
        for key, agent_type in _PROCESS_MAP.items():
            if key in proc_base:
                return PaneClassification(
                    pane_type=agent_type,
                    process_name=proc_base,
                    pid=pid,
                    tty=tty,
                )

    content_match = _match_content(pane_content)
    if content_match:
        return PaneClassification(
            pane_type=content_match,
            process_name=proc_name.rsplit("/", 1)[-1] if proc_name else "",
            pid=pid,
            tty=tty,
        )

    if proc_name:
        proc_base = proc_name.rsplit("/", 1)[-1].lower()
        if proc_base in _SHELL_NAMES:
            return PaneClassification(
                pane_type="shell",
                process_name=proc_base,
                pid=pid,
                tty=tty,
            )
        return PaneClassification(
            pane_type="unknown",
            process_name=proc_base,
            pid=pid,
            tty=tty,
        )

    return PaneClassification(
        pane_type="idle",
        process_name="",
        pid=0,
        tty=tty,
    )
