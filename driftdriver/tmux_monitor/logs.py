# ABOUTME: Log file management — trim pane logs to max size, rotate daily.
from __future__ import annotations

from pathlib import Path

from driftdriver.tmux_monitor.config import TmuxMonitorConfig


def trim_log(log_path: Path, max_bytes: int) -> bool:
    if not log_path.exists():
        return False
    try:
        size = log_path.stat().st_size
    except OSError:
        return False
    if size <= max_bytes:
        return False
    keep_bytes = int(max_bytes * 0.8)
    try:
        with open(log_path, "rb") as f:
            f.seek(size - keep_bytes)
            _ = f.readline()
            remaining = f.read()
        with open(log_path, "wb") as f:
            f.write(remaining)
        return True
    except OSError:
        return False


def trim_all_logs(config: TmuxMonitorConfig) -> int:
    panes_dir = config.panes_dir
    if not panes_dir.exists():
        return 0
    trimmed = 0
    for f in panes_dir.iterdir():
        if f.suffix == ".log":
            if trim_log(f, config.max_pane_log_bytes):
                trimmed += 1
    return trimmed
