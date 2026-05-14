# ABOUTME: Streamlit web frontend for tmux-monitor — live dashboard for agent sessions.
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import streamlit as st

_DEFAULT_STATE_DIR = Path.home() / ".local" / "share" / "driftdriver" / "tmux-monitor"


def _load_status() -> dict | None:
    status_path = _DEFAULT_STATE_DIR / "status.json"
    if not status_path.exists():
        return None
    try:
        return json.loads(status_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _load_daily_events() -> list[dict]:
    today = datetime.now().strftime("%Y-%m-%d")
    daily_path = _DEFAULT_STATE_DIR / "daily" / f"{today}.jsonl"
    if not daily_path.exists():
        return []
    events = []
    for line in daily_path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return events


def _time_ago(iso_str: str) -> str:
    try:
        ts = datetime.fromisoformat(iso_str)
        delta = datetime.now(timezone.utc) - ts
        secs = int(delta.total_seconds())
        if secs < 60:
            return f"{secs}s ago"
        if secs < 3600:
            return f"{secs // 60}m ago"
        return f"{secs // 3600}h ago"
    except (ValueError, TypeError):
        return iso_str


_AGENT_COLORS = {
    "claude-code": "#D97706",
    "codex": "#10B981",
    "opencode": "#6366F1",
    "kilocode": "#EC4899",
    "pi-dev": "#8B5CF6",
    "shell": "#6B7280",
    "idle": "#9CA3AF",
    "unknown": "#9CA3AF",
}

st.set_page_config(
    page_title="tmux monitor",
    page_icon=":satellite:",
    layout="wide",
)

st.title("tmux monitor")

status = _load_status()

if status is None:
    st.warning("No status file found. Run `driftdriver tmux-monitor heartbeat` or start the daemon.")
    st.stop()

col1, col2, col3, col4 = st.columns(4)
sessions = status.get("sessions", {})
total_panes = sum(len(s.get("panes", {})) for s in sessions.values())
agent_panes = [
    (pid, pd)
    for s in sessions.values()
    for pid, pd in s.get("panes", {}).items()
    if pd.get("type") not in ("shell", "idle", "unknown")
]
with col1:
    st.metric("Sessions", len(sessions))
with col2:
    st.metric("Panes", total_panes)
with col3:
    st.metric("Agents", len(agent_panes))
with col4:
    ts = status.get("timestamp", "")
    st.metric("Last Update", _time_ago(ts) if ts else "never")

st.divider()

tab_sessions, tab_agents, tab_events = st.tabs(["Sessions", "Agents", "Events"])

with tab_sessions:
    for sess_name, sess_data in sessions.items():
        panes = sess_data.get("panes", {})
        agent_count = sum(1 for p in panes.values() if p.get("type") not in ("shell", "idle", "unknown"))
        header = f"**{sess_name}** — {sess_data.get('windows', '?')} windows, {len(panes)} panes"
        if agent_count:
            header += f" ({agent_count} agent{'s' if agent_count != 1 else ''})"
        st.markdown(header)
        for pane_id, pane_data in panes.items():
            ptype = pane_data.get("type", "unknown")
            color = _AGENT_COLORS.get(ptype, "#9CA3AF")
            cols = st.columns([3, 1, 1, 2])
            with cols[0]:
                st.markdown(f"`{pane_id}`")
            with cols[1]:
                st.markdown(f":{color}[**{ptype}**]")
            with cols[2]:
                cwd = pane_data.get("cwd", "")
                if cwd:
                    short = cwd.replace(str(Path.home()), "~")
                    st.text(short[-40:] if len(short) > 40 else short)
            with cols[3]:
                task = pane_data.get("current_task", "")
                if task:
                    st.text(task[:60])
        st.markdown("---")

with tab_agents:
    if not agent_panes:
        st.info("No active agent panes detected.")
    else:
        for pane_id, pane_data in agent_panes:
            ptype = pane_data.get("type", "unknown")
            color = _AGENT_COLORS.get(ptype, "#9CA3AF")
            with st.expander(f"{pane_id} — {ptype}", expanded=True):
                c1, c2 = st.columns([1, 2])
                with c1:
                    st.markdown(f"**Type:** {ptype}")
                    cwd = pane_data.get("cwd", "")
                    if cwd:
                        st.markdown(f"**CWD:** `{cwd.replace(str(Path.home()), '~')}`")
                    pid = pane_data.get("pid", 0)
                    if pid:
                        st.markdown(f"**PID:** {pid}")
                    since = pane_data.get("active_since", "")
                    if since:
                        st.markdown(f"**Active:** {_time_ago(since)}")
                    llm_at = pane_data.get("llm_summary_at", "")
                    if llm_at:
                        st.markdown(f"**Last summary:** {_time_ago(llm_at)}")
                with c2:
                    summary = pane_data.get("summary", "")
                    if summary:
                        st.markdown(f"> {summary}")
                    task = pane_data.get("current_task", "")
                    if task:
                        st.markdown(f"**Task:** {task}")
                    related = pane_data.get("related_panes", [])
                    if related:
                        st.markdown(f"**Related:** {', '.join(related)}")

with tab_events:
    events = _load_daily_events()
    if not events:
        st.info("No events today.")
    else:
        event_colors = {
            "session.appeared": "🟢",
            "session.disappeared": "🔴",
            "pane.created": "🟡",
            "pane.destroyed": "⚫",
            "agent.started": "🔵",
            "agent.stopped": "🟠",
            "agent.summary": "🟣",
        }
        for ev in reversed(events[-100:]):
            etype = ev.get("event_type", "")
            icon = event_colors.get(etype, "⚪")
            ts = ev.get("timestamp", "")
            session = ev.get("session", "")
            pane = ev.get("pane_id", "")
            agent_type = ev.get("agent_type", "")
            line = f"{icon} `{ts[11:19] if ts else ''}` **{etype}**"
            if session:
                line += f" session=`{session}`"
            if pane:
                line += f" pane=`{pane}`"
            if agent_type:
                line += f" ({agent_type})"
            st.markdown(line)

if "auto_refresh" not in st.session_state:
    st.session_state.auto_refresh = True

refresh = st.sidebar.checkbox("Auto-refresh (5s)", value=st.session_state.auto_refresh)
st.session_state.auto_refresh = refresh
if refresh:
    time.sleep(5)
    st.rerun()
