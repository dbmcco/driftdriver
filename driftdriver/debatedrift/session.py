# ABOUTME: debatedrift tmux session launcher — creates 4-pane layout with pipe-pane capture.
# ABOUTME: Emits session.started events for factory brain suppression.
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from driftdriver.debatedrift.config import DebateDriftConfig
from driftdriver.debatedrift.prompts import (
    debater_a_prompt,
    debater_b_prompt,
    proxy_prompt,
)


_CONSTITUTION_PATH = Path(__file__).parent / "proxy-constitution.md"


@dataclass
class DebateSession:
    task_id: str
    debate_dir: Path
    tmux_session: str
    config: DebateDriftConfig


def _tmux(*args: str) -> int:
    return subprocess.call(["tmux", *args])


def _tmux_out(*args: str) -> str:
    result = subprocess.run(["tmux", *args], text=True, capture_output=True)
    return result.stdout.strip()


def _has_ts() -> bool:
    return shutil.which("ts") is not None


def _pipe_pane_cmd(log_path: Path) -> str:
    """Return the pipe-pane shell command for a given log file."""
    log_str = str(log_path)
    if _has_ts():
        return f"ts '%Y-%m-%dT%H:%M:%.S' >> {log_str}"
    return f"cat >> {log_str}"


def _emit_session_event(task_id: str, pane: str, event: str) -> None:
    """Emit a session.started or session.ended event for factory brain suppression."""
    try:
        events_dir = Path(".workgraph") / "events"
        events_dir.mkdir(parents=True, exist_ok=True)
        events_file = events_dir / "events.jsonl"
        record = json.dumps({
            "event": event,
            "session": f"debatedrift-{task_id}-{pane}",
            "pid": os.getpid(),
        })
        with open(events_file, "a", encoding="utf-8") as f:
            f.write(record + "\n")
    except Exception:
        pass


def launch_debate_session(
    *,
    task_id: str,
    topic: str,
    config: DebateDriftConfig,
    workgraph_dir: Path,
) -> DebateSession:
    """Launch a 4-pane tmux debate session and wire pipe-pane capture.

    Layout:
      pane 0 (top-left):  Debater A
      pane 1 (top-right): Debater B
      pane 2 (bot-left):  Proxy
      pane 3 (bot-right): tail -f debate.log (read-only observer)

    Returns a DebateSession describing the running session.
    Raises RuntimeError if tmux is not available.
    """
    if not shutil.which("tmux"):
        raise RuntimeError("tmux is required for debatedrift but is not installed")

    debate_dir = workgraph_dir / ".debatedrift" / task_id
    debate_dir.mkdir(parents=True, exist_ok=True)

    pane_a_log = debate_dir / "pane-a.log"
    pane_b_log = debate_dir / "pane-b.log"
    pane_c_log = debate_dir / "pane-c.log"
    debate_log = debate_dir / "debate.log"

    # Initialise log files
    for log in [pane_a_log, pane_b_log, pane_c_log, debate_log]:
        if not log.exists():
            log.write_text("", encoding="utf-8")

    # Write prompt files so claude can be launched with -p flag
    prompt_a = debate_dir / "prompt-a.txt"
    prompt_b = debate_dir / "prompt-b.txt"
    prompt_c = debate_dir / "prompt-c.txt"

    prompt_a.write_text(
        debater_a_prompt(topic=topic, task_id=task_id,
                         max_rounds=config.max_rounds, context_files=config.context_files),
        encoding="utf-8",
    )
    prompt_b.write_text(
        debater_b_prompt(topic=topic, task_id=task_id,
                         max_rounds=config.max_rounds, context_files=config.context_files),
        encoding="utf-8",
    )
    prompt_c.write_text(
        proxy_prompt(topic=topic, task_id=task_id,
                     context_files=config.context_files,
                     constitution_path=_CONSTITUTION_PATH),
        encoding="utf-8",
    )

    session_name = f"debate-{task_id}"

    # Kill existing session if any
    subprocess.call(["tmux", "kill-session", "-t", session_name],
                    stderr=subprocess.DEVNULL)

    # Create session with 4 panes (2x2 layout)
    # Pane 0: top-left (Debater A)
    _tmux("new-session", "-d", "-s", session_name, "-x", "220", "-y", "50")
    # Split horizontally for pane 1 (Debater B)
    _tmux("split-window", "-h", "-t", f"{session_name}:0")
    # Split pane 0 vertically for pane 2 (Proxy)
    _tmux("split-window", "-v", "-t", f"{session_name}:0.0")
    # Split pane 1 vertically for pane 3 (debate.log observer)
    _tmux("split-window", "-v", "-t", f"{session_name}:0.1")

    # Pane indices after splits:
    #   0.0 = Debater A (top-left, initial pane)
    #   0.1 = Debater B (top-right, after split-window -h)
    #   0.2 = Proxy     (bottom-left, after split-window -v on pane 0)
    #   0.3 = Observer  (bottom-right, after split-window -v on pane 1)

    # Wire pipe-pane for debaters and proxy
    _tmux("pipe-pane", "-t", f"{session_name}:0.0",
          _pipe_pane_cmd(pane_a_log))
    _tmux("pipe-pane", "-t", f"{session_name}:0.1",
          _pipe_pane_cmd(pane_b_log))
    _tmux("pipe-pane", "-t", f"{session_name}:0.2",
          _pipe_pane_cmd(pane_c_log))

    # Launch claude in each debater pane
    _tmux("send-keys", "-t", f"{session_name}:0.0",
          f"claude --print < {prompt_a}", "Enter")
    time.sleep(0.5)
    _tmux("send-keys", "-t", f"{session_name}:0.1",
          f"claude --print < {prompt_b}", "Enter")
    time.sleep(0.5)
    _tmux("send-keys", "-t", f"{session_name}:0.2",
          f"claude --print < {prompt_c}", "Enter")

    # Pane 3: tail the debate log (observer, read-only)
    _tmux("send-keys", "-t", f"{session_name}:0.3",
          f"tail -f {debate_log}", "Enter")

    # Emit session.started events for factory brain
    for pane in ["debater-a", "debater-b", "proxy"]:
        _emit_session_event(task_id, pane, "session.started")

    print(f"debatedrift: session launched → tmux attach -t {session_name}", file=sys.stderr)
    print(f"debatedrift: logs → {debate_dir}", file=sys.stderr)

    return DebateSession(
        task_id=task_id,
        debate_dir=debate_dir,
        tmux_session=session_name,
        config=config,
    )


def teardown_session(session: DebateSession) -> None:
    """Close the tmux session and emit session.ended events."""
    subprocess.call(["tmux", "kill-session", "-t", session.tmux_session],
                    stderr=subprocess.DEVNULL)
    for pane in ["debater-a", "debater-b", "proxy"]:
        _emit_session_event(session.task_id, pane, "session.ended")
