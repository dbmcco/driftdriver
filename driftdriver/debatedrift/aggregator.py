# ABOUTME: debatedrift log aggregator — merges pane logs, counts rounds, detects sentinels.
# ABOUTME: Designed to run as a background polling loop; all operations are pure functions.
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


_ROUND_END = "[ROUND:END]"
_CONCLUDED = "DEBATE:CONCLUDED"
_DEADLOCK = "DEBATE:DEADLOCK"


def count_round_ends(log_path: Path) -> int:
    """Count [ROUND:END] sentinels in a log file. Returns 0 if file missing."""
    if not log_path.exists():
        return 0
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    return text.count(_ROUND_END)


def detect_sentinel(log_path: Path, sentinel: str) -> bool:
    """Return True if sentinel string appears anywhere in log_path."""
    if not log_path.exists():
        return False
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return sentinel in text


def merge_logs(*, debate_dir: Path, output_path: Path) -> None:
    """Merge pane-a.log and pane-b.log into a single file sorted by leading timestamp.

    Lines without timestamps are kept in file order after timestamped lines.
    If ts(1) timestamps are not present, files are interleaved in file order.
    Output is deterministic for the same input — idempotent.
    """
    lines: list[tuple[str, str]] = []  # (timestamp_or_empty, line)

    for pane_file in ["pane-a.log", "pane-b.log"]:
        pane_path = debate_dir / pane_file
        if not pane_path.exists():
            continue
        try:
            text = pane_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines(keepends=True):
            # ts(1) format: "YYYY-MM-DDTHH:MM:SS.ffffff "
            parts = line.split(" ", 1)
            ts = parts[0] if len(parts) == 2 and "T" in parts[0] else ""
            lines.append((ts, line))

    lines.sort(key=lambda x: x[0])
    merged = "".join(line for _, line in lines)
    output_path.write_text(merged, encoding="utf-8")


@dataclass
class AggregatorState:
    round_count: int = 0
    terminated: bool = False
    termination_kind: str | None = None

    def update(self, *, debate_dir: Path) -> None:
        """Refresh state from the debate directory. Idempotent."""
        if self.terminated:
            return

        pane_a = debate_dir / "pane-a.log"
        pane_b = debate_dir / "pane-b.log"
        pane_c = debate_dir / "pane-c.log"

        a_rounds = count_round_ends(pane_a)
        b_rounds = count_round_ends(pane_b)
        self.round_count = a_rounds + b_rounds

        if detect_sentinel(pane_c, _CONCLUDED):
            self.terminated = True
            self.termination_kind = "concluded"
        elif detect_sentinel(pane_c, _DEADLOCK):
            self.terminated = True
            self.termination_kind = "deadlock"


def send_nudge(*, task_id: str, pane: str) -> None:
    """Send a wg msg nudge to the stalled agent."""
    msg = (
        f"You haven't posted a [ROUND:END] in a while ({pane}). "
        "Please complete your current turn and write [ROUND:END] to continue."
    )
    try:
        subprocess.run(
            ["wg", "msg", "send", task_id, msg],
            check=False,
            capture_output=True,
            timeout=10,
        )
    except Exception:
        pass
