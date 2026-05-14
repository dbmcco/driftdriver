# ABOUTME: Status file writer and daily event log for tmux-monitor.
from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any

from driftdriver.tmux_monitor.config import TmuxMonitorConfig
from driftdriver.tmux_monitor.detection import PaneClassification
from driftdriver.tmux_monitor.discovery import PaneInfo


def _iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _today_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d")


def write_status(
    config: TmuxMonitorConfig,
    sessions: dict[str, list[PaneInfo]],
    classifications: dict[str, PaneClassification],
    summaries: dict[str, dict[str, Any]],
    active_since: dict[str, str],
) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)

    status: dict[str, Any] = {
        "timestamp": _iso_now(),
        "heartbeat_interval": config.heartbeat_interval(
            datetime.datetime.now().hour
        ),
        "sessions": {},
    }

    for sess_name, panes in sessions.items():
        session_data: dict[str, Any] = {
            "windows": len({p.window for p in panes}),
            "panes": {},
        }
        for pane in panes:
            qid = pane.qualified_id
            cls = classifications.get(pane.pane_id)
            entry: dict[str, Any] = {
                "type": cls.pane_type if cls else "unknown",
                "pid": cls.pid if cls else 0,
                "tty": pane.tty,
                "cwd": pane.cwd,
            }
            if qid in active_since:
                entry["active_since"] = active_since[qid]
            if qid in summaries:
                entry["summary"] = summaries[qid].get("summary", "")
                entry["current_task"] = summaries[qid].get("current_task", "")
                entry["related_panes"] = summaries[qid].get("related_panes", [])
                entry["llm_summary_at"] = summaries[qid].get("llm_summary_at", "")
            session_data["panes"][qid] = entry
        status["sessions"][sess_name] = session_data

    tmp = config.status_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, indent=2) + "\n", encoding="utf-8")
    tmp.replace(config.status_path)


def append_event(
    config: TmuxMonitorConfig,
    event_type: str,
    session: str,
    pane_id: str,
    **extra: Any,
) -> None:
    config.daily_dir.mkdir(parents=True, exist_ok=True)
    daily_path = config.daily_dir / f"{_today_str()}.jsonl"

    event: dict[str, Any] = {
        "timestamp": _iso_now(),
        "event_type": event_type,
        "session": session,
        "pane_id": pane_id,
        **extra,
    }
    with open(daily_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


def prune_old_daily(config: TmuxMonitorConfig) -> int:
    today = _today_str()
    removed = 0
    if not config.daily_dir.exists():
        return 0
    for f in config.daily_dir.iterdir():
        if f.suffix == ".jsonl" and f.stem != today:
            f.unlink()
            removed += 1
    return removed


def load_known_sessions(config: TmuxMonitorConfig) -> dict[str, Any]:
    path = config.known_sessions_path
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_known_sessions(config: TmuxMonitorConfig, data: dict[str, Any]) -> None:
    config.state_dir.mkdir(parents=True, exist_ok=True)
    tmp = config.known_sessions_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    tmp.replace(config.known_sessions_path)
