# ABOUTME: Continuation evaluator for agent stop events.
# ABOUTME: Decides whether a stopped agent should CONTINUE, STOP, or ESCALATE.
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ContinuationDecision:
    action: str  # "CONTINUE", "STOP", "ESCALATE"
    reason: str
    confidence: float  # 0-1
    throttled: bool  # True if continuation was throttled


_STATE_FILE = ".continuation-state"


def check_throttle(state_dir: Path, window_seconds: int = 300, max_continuations: int = 3) -> bool:
    """Return True if continuation count within the window exceeds max_continuations."""
    state_file = state_dir / _STATE_FILE
    if not state_file.exists():
        return False
    now = time.time()
    cutoff = now - window_seconds
    lines = state_file.read_text(encoding="utf-8").splitlines()
    recent = [float(ts) for ts in lines if ts.strip() and float(ts) >= cutoff]
    return len(recent) >= max_continuations


def record_continuation(state_dir: Path) -> None:
    """Append the current timestamp to the state file."""
    state_file = state_dir / _STATE_FILE
    with state_file.open("a", encoding="utf-8") as fh:
        fh.write(f"{time.time()}\n")


def evaluate_continuation(
    task_contract: dict,
    recent_actions: list[str],
    stop_reason: str,
    state_dir: Path | None = None,
) -> ContinuationDecision:
    """Evaluate whether the agent should continue, stop, or escalate."""
    # No active task
    if not task_contract:
        return ContinuationDecision(
            action="STOP",
            reason="No active task contract",
            confidence=1.0,
            throttled=False,
        )

    status = task_contract.get("status", "")

    if status == "blocked":
        return ContinuationDecision(
            action="ESCALATE",
            reason="Task is blocked",
            confidence=0.9,
            throttled=False,
        )

    if status == "done":
        return ContinuationDecision(
            action="STOP",
            reason="Task is already complete",
            confidence=1.0,
            throttled=False,
        )

    # Task is in_progress — check if checklist is complete
    checklist = task_contract.get("checklist", [])
    checklist_done = task_contract.get("checklist_done", [])
    all_done = len(checklist) > 0 and set(checklist_done) >= set(checklist)

    if all_done:
        return ContinuationDecision(
            action="STOP",
            reason="Checklist fully complete",
            confidence=0.95,
            throttled=False,
        )

    # Check throttle before recommending continuation
    if state_dir is not None and check_throttle(state_dir):
        return ContinuationDecision(
            action="STOP",
            reason="Continuation throttled: too many continuations in window",
            confidence=1.0,
            throttled=True,
        )

    return ContinuationDecision(
        action="CONTINUE",
        reason="Task in progress with incomplete checklist",
        confidence=0.8,
        throttled=False,
    )
