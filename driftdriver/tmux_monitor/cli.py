# ABOUTME: CLI subcommand for tmux-monitor — start/stop/status/sessions/logs.
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from driftdriver.tmux_monitor.config import TmuxMonitorConfig
from driftdriver.tmux_monitor.daemon import run_daemon, run_heartbeat
from driftdriver.tmux_monitor.relevance import (
    format_relevant_json,
    format_relevant_text,
)


def _load_config(args: argparse.Namespace) -> TmuxMonitorConfig:
    state_dir = getattr(args, "state_dir", None)
    if state_dir:
        cfg_path = Path(state_dir) / "config.json"
        cfg = TmuxMonitorConfig.load(cfg_path)
        cfg.state_dir = Path(state_dir)
        return cfg
    default_path = TmuxMonitorConfig().state_dir / "config.json"
    return TmuxMonitorConfig.load(default_path)


def _load_status(config: TmuxMonitorConfig) -> dict | None:
    path = config.status_path
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def cmd_start(args: argparse.Namespace) -> int:
    config = _load_config(args)
    run_daemon(config)
    return 0


def cmd_heartbeat(args: argparse.Namespace) -> int:
    config = _load_config(args)
    result = run_heartbeat(config)
    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
    else:
        print(f"Sessions: {result['sessions']}")
        print(f"Panes: {result['panes']}")
        if result["events"]:
            print(f"Events: {', '.join(result['events'])}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    config = _load_config(args)
    data = _load_status(config)
    if data is None:
        print("No status file found. Run `driftdriver tmux-monitor heartbeat` first.", file=sys.stderr)
        return 1

    target_path = getattr(args, "cwd", None) or ""
    target_repo = getattr(args, "repo", None) or ""
    if not target_path and not target_repo:
        target_path = str(Path.cwd())

    use_json = getattr(args, "json", False)
    include_all = getattr(args, "all", False)

    if use_json:
        out = format_relevant_json(data, target_repo=target_repo, target_path=target_path, include_unrelated=include_all)
        print(json.dumps(out, indent=2))
    else:
        text = format_relevant_text(data, target_repo=target_repo, target_path=target_path, include_unrelated=include_all)
        print(text)
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    config = _load_config(args)
    data = _load_status(config)
    if data is None:
        print("No status file found.", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        sessions = {}
        for s, sd in data.get("sessions", {}).items():
            agent_types = set()
            for pd in sd.get("panes", {}).values():
                t = pd.get("type", "unknown")
                if t not in ("shell", "idle"):
                    agent_types.add(t)
            sessions[s] = {"windows": sd["windows"], "agents": sorted(agent_types)}
        print(json.dumps(sessions, indent=2))
        return 0

    for sess_name, sess_data in data.get("sessions", {}).items():
        agent_types = set()
        pane_count = len(sess_data.get("panes", {}))
        for pd in sess_data.get("panes", {}).values():
            t = pd.get("type", "unknown")
            if t not in ("shell", "idle"):
                agent_types.add(t)
        agents = ", ".join(sorted(agent_types)) if agent_types else "-"
        print(f"{sess_name:30s}  {sess_data['windows']}w {pane_count}p  agents: {agents}")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    config = _load_config(args)
    panes_dir = config.panes_dir
    if not panes_dir.exists():
        print("No pane logs found.", file=sys.stderr)
        return 1

    target = getattr(args, "pane", None)
    if target:
        log_file = panes_dir / f"{target}.log"
        if not log_file.exists():
            candidates = list(panes_dir.glob("*.log"))
            matches = [c for c in candidates if target in c.name]
            if len(matches) == 1:
                log_file = matches[0]
            elif len(matches) > 1:
                print(f"Ambiguous match: {[c.name for c in matches]}", file=sys.stderr)
                return 1
            else:
                print(f"No log found for '{target}'", file=sys.stderr)
                return 1
        lines = int(getattr(args, "lines", 50))
        content = log_file.read_text(encoding="utf-8", errors="replace")
        all_lines = content.splitlines()
        for line in all_lines[-lines:]:
            print(line)
        return 0

    for f in sorted(panes_dir.glob("*.log")):
        size = f.stat().st_size
        print(f"{f.name:40s}  {size:>8d} bytes")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    try:
        result = subprocess.run(
            ["pgrep", "-f", f"tmux-monitor.*start"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        pids = result.stdout.strip().splitlines()
        if not pids:
            print("No tmux-monitor daemon found.")
            return 0
        for pid in pids:
            pid = pid.strip()
            if pid:
                subprocess.run(["kill", pid], timeout=5)
                print(f"Sent SIGTERM to pid {pid}")
        return 0
    except (subprocess.TimeoutExpired, OSError) as exc:
        print(f"Error stopping daemon: {exc}", file=sys.stderr)
        return 1


def cmd_web(args: argparse.Namespace) -> int:
    import driftdriver.tmux_monitor.web as web_module
    web_path = Path(web_module.__file__)
    port = getattr(args, "port", 8501)
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(web_path),
         "--server.port", str(port),
         "--server.headless", "true"],
    )
    return 0


def register_tmux_monitor_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "tmux-monitor",
        help="tmux session monitoring daemon",
    )
    p.add_argument("--state-dir", help="Override state directory")

    tmux_sub = p.add_subparsers(dest="tmux_action", required=True)

    start = tmux_sub.add_parser("start", help="Start the monitoring daemon")
    start.set_defaults(func=cmd_start)

    stop = tmux_sub.add_parser("stop", help="Stop the monitoring daemon")
    stop.set_defaults(func=cmd_stop)

    status = tmux_sub.add_parser("status", help="Show agents relevant to a repo (default: cwd)")
    status.add_argument("--cwd", help="Target directory to find relevant agents for (default: cwd)")
    status.add_argument("--repo", help="Target repo name to filter by")
    status.add_argument("--all", action="store_true", help="Include unrelated agents")
    status.add_argument("--json", action="store_true", help="JSON output")
    status.set_defaults(func=cmd_status)

    hb = tmux_sub.add_parser("heartbeat", help="Run a single heartbeat cycle")
    hb.add_argument("--json", action="store_true", help="JSON output")
    hb.set_defaults(func=cmd_heartbeat)

    sessions = tmux_sub.add_parser("sessions", help="List sessions with agent types")
    sessions.add_argument("--json", action="store_true", help="JSON output")
    sessions.set_defaults(func=cmd_sessions)

    logs = tmux_sub.add_parser("logs", help="Show pane logs")
    logs.add_argument("pane", nargs="?", help="Pane identifier to tail")
    logs.add_argument("-n", "--lines", type=int, default=50, help="Number of lines")
    logs.set_defaults(func=cmd_logs)

    web = tmux_sub.add_parser("web", help="Launch Streamlit web dashboard")
    web.add_argument("--port", type=int, default=8501, help="Port (default: 8501)")
    web.set_defaults(func=cmd_web)
