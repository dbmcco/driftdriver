# ABOUTME: Streamlit web frontend for tmux-monitor — single table dashboard.
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

_STATE_DIR = Path.home() / ".local" / "share" / "driftdriver" / "tmux-monitor"


def _load_status() -> dict | None:
    p = _STATE_DIR / "status.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _duration(iso_str: str | None) -> str:
    if not iso_str:
        return "-"
    try:
        ts = datetime.fromisoformat(iso_str)
        secs = int((datetime.now(timezone.utc) - ts).total_seconds())
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        if h:
            return f"{h}h{m:02d}m"
        if m:
            return f"{m}m{s:02d}s"
        return f"{s}s"
    except (ValueError, TypeError):
        return "-"


st.set_page_config(page_title="tmux monitor", page_icon=":satellite:", layout="wide")

status = _load_status()
if status is None:
    st.warning("No status file. Run `driftdriver tmux-monitor heartbeat` first.")
    st.stop()

sessions = status.get("sessions", {})

rows = []
for sess_name, sess_data in sessions.items():
    sess_created = sess_data.get("created_at", "")
    for pane_id, pd in sess_data.get("panes", {}).items():
        rows.append({
            "session": sess_name,
            "pane": pane_id,
            "type": pd.get("type", "?"),
            "tmux_session": _duration(sess_created),
            "agent_duration": _duration(pd.get("active_since")),
            "cwd": pd.get("cwd", "").replace(str(Path.home()), "~"),
            "task": pd.get("current_task", ""),
            "summary": pd.get("summary", ""),
        })

col1, col2, col3, col4 = st.columns(4)
agent_count = sum(1 for r in rows if r["type"] not in ("shell", "idle", "unknown"))
with col1:
    st.metric("Sessions", len(sessions))
with col2:
    st.metric("Total Panes", len(rows))
with col3:
    st.metric("Active Agents", agent_count)
with col4:
    ts = status.get("timestamp", "")
    st.metric("Updated", _duration(ts) if ts else "never")

show_filter = st.selectbox("Show", ["all", "agents only"], index=0)

if show_filter == "agents only":
    display = [r for r in rows if r["type"] not in ("shell", "idle", "unknown")]
else:
    display = rows

if not display:
    st.info("No panes match the filter.")
    st.stop()

table_data = []
for r in display:
    table_data.append({
        "Session": r["session"],
        "Pane": r["pane"].split(":")[-1] if ":" in r["pane"] else r["pane"],
        "Type": r["type"],
        "tmux uptime": r["tmux_session"],
        "agent uptime": r["agent_duration"],
        "CWD": r["cwd"].split("/")[-1] if r["cwd"] else "",
        "Current Task": r["task"],
        "Summary": r["summary"][:200] + ("..." if len(r["summary"]) > 200 else ""),
    })

st.dataframe(
    table_data,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Session": st.column_config.TextColumn(width="medium"),
        "Pane": st.column_config.TextColumn(width="small"),
        "Type": st.column_config.TextColumn(width="small"),
        "tmux uptime": st.column_config.TextColumn(width="small"),
        "agent uptime": st.column_config.TextColumn(width="small"),
        "CWD": st.column_config.TextColumn(width="small"),
        "Current Task": st.column_config.TextColumn(width="medium"),
        "Summary": st.column_config.TextColumn(width="large"),
    },
)

auto = st.sidebar.checkbox("Auto-refresh (5s)", value=True)
if auto:
    time.sleep(5)
    st.rerun()
