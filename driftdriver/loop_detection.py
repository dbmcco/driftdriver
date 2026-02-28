# ABOUTME: Loop detection module for driftdriver.
# ABOUTME: Detects repeated agent actions using fingerprint buffering and state management.
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LoopDetection:
    detected: bool
    pattern: str        # the repeated fingerprint
    occurrences: int    # how many times seen
    suggestion: str     # what to try instead


_STATE_FILE = ".loop-state"


def fingerprint_action(tool_name: str, tool_input_hash: str) -> str:
    """Create a short hash of the tool call for dedup."""
    raw = f"{tool_name}:{tool_input_hash}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def record_action(state_dir: Path, fingerprint: str) -> None:
    """Append the fingerprint to the loop state file."""
    state_file = state_dir / _STATE_FILE
    with state_file.open("a", encoding="utf-8") as fh:
        fh.write(f"{fingerprint}\n")


def detect_loop(state_dir: Path, threshold: int = 3) -> LoopDetection:
    """Read loop state and detect if any fingerprint appears >= threshold times."""
    state_file = state_dir / _STATE_FILE
    if not state_file.exists():
        return LoopDetection(detected=False, pattern="", occurrences=0, suggestion="")

    lines = [l.strip() for l in state_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return LoopDetection(detected=False, pattern="", occurrences=0, suggestion="")

    counts = Counter(lines)
    most_common, count = counts.most_common(1)[0]

    if count >= threshold:
        return LoopDetection(
            detected=True,
            pattern=most_common,
            occurrences=count,
            suggestion=suggest_alternative(most_common, lines),
        )

    return LoopDetection(detected=False, pattern=most_common, occurrences=count, suggestion="")


def suggest_alternative(pattern: str, recent_actions: list[str]) -> str:
    """Return a suggestion based on the repeated pattern."""
    count = recent_actions.count(pattern) if recent_actions else 0
    times = f"{count} times" if count > 1 else "repeatedly"
    return (
        f"Pattern '{pattern}' has been repeated {times} — "
        "consider a different approach or checking prerequisites."
    )


def clear_state(state_dir: Path) -> None:
    """Remove the loop state file."""
    state_file = state_dir / _STATE_FILE
    if state_file.exists():
        state_file.unlink()
