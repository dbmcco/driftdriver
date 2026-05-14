# ABOUTME: Configuration for the tmux-monitor daemon.
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_STATE_DIR = Path.home() / ".local" / "share" / "driftdriver" / "tmux-monitor"


@dataclass
class TmuxMonitorConfig:
    heartbeat_day_seconds: int = 30
    heartbeat_night_seconds: int = 3600
    night_start_hour: int = 22
    night_end_hour: int = 4
    llm_summary_interval_seconds: int = 300
    max_pane_log_bytes: int = 524288
    state_dir: Path = field(default_factory=lambda: _DEFAULT_STATE_DIR)

    @classmethod
    def load(cls, path: Path) -> TmuxMonitorConfig:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        state_dir = data.get("state_dir", str(_DEFAULT_STATE_DIR))
        return cls(
            heartbeat_day_seconds=data.get("heartbeat_day_seconds", 30),
            heartbeat_night_seconds=data.get("heartbeat_night_seconds", 3600),
            night_start_hour=data.get("night_start_hour", 22),
            night_end_hour=data.get("night_end_hour", 4),
            llm_summary_interval_seconds=data.get("llm_summary_interval_seconds", 300),
            max_pane_log_bytes=data.get("max_pane_log_bytes", 524288),
            state_dir=Path(state_dir).expanduser(),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "heartbeat_day_seconds": self.heartbeat_day_seconds,
            "heartbeat_night_seconds": self.heartbeat_night_seconds,
            "night_start_hour": self.night_start_hour,
            "night_end_hour": self.night_end_hour,
            "llm_summary_interval_seconds": self.llm_summary_interval_seconds,
            "max_pane_log_bytes": self.max_pane_log_bytes,
            "state_dir": str(self.state_dir),
        }
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    def heartbeat_interval(self, hour: int) -> float:
        if self.night_start_hour <= hour or hour < self.night_end_hour:
            return float(self.heartbeat_night_seconds)
        return float(self.heartbeat_day_seconds)

    @property
    def panes_dir(self) -> Path:
        return self.state_dir / "panes"

    @property
    def daily_dir(self) -> Path:
        return self.state_dir / "daily"

    @property
    def status_path(self) -> Path:
        return self.state_dir / "status.json"

    @property
    def known_sessions_path(self) -> Path:
        return self.state_dir / "known_sessions.json"
