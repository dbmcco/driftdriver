# ABOUTME: CLI subcommand for tmux-monitor — start/stop/status/sessions/logs.
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from driftdriver.tmux_monitor.config import TmuxMonitorConfig
from driftdriver.tmux_monitor.daemon import run_daemon, run_heartbeat


def _load_config(args: argparse.Namespace) -> TmuxMonitorConfig:
    state_dir = getattr(args, "state_dir", None)
    if state_dir:
        cfg_path = Path(state_dir) / "config.json"
        cfg = TmuxMonitorConfig.load(cfg_path)
        cfg.state_dir = Path(state_dir)
        return cfg
    default_path = TmuxMonitorConfig().state_dir / "config.json"
    return TmuxMonitorConfig.load(default_path)


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
    status_path = config.status_path
    if not status_path.exists():
        print("No status file found. Is the daemon running?", file=sys.stderr)
        return 1

    data = json.loads(status_path.read_text(encoding="utf-8"))
    if getattr(args, "json", False):
        print(json.dumps(data, indent=2))
        return 0

    print(f"Last update: {data['timestamp']}")
    print(f"Heartbeat:   {data['heartbeat_interval']}s")
    print()
    for sess_name, sess_data in data.get("sessions", {}).items():
        print(f"Session: {sess_name} ({sess_data['windows']} windows)")
        for pane_id, pane_data in sess_data.get("panes", {}).items():
            ptype = pane_data.get("type", "?")
            cwd = pane_data.get("cwd", "")
            marker = ">>>" if ptype not in ("shell", "idle") else "   "
            line = f"  {marker} {pane_id:30s} [{ptype}]"
            if cwd:
                short_cwd = cwd.replace(str(Path.home()), "~")
                line += f"  {short_cwd}"
            print(line)
            if pane_data.get("summary"):
                print(f"      {pane_data['summary'][:120]}")
            if pane_data.get("current_task"):
                print(f"      task: {pane_data['current_task']}")
        print()
    return 0


def cmd_sessions(args: argparse.Namespace) -> int:
    config = _load_config(args)
    status_path = config.status_path
    if not status_path.exists():
        print("No status file found. Is the daemon running?", file=sys.stderr)
        return 1

    data = json.loads(status_path.read_text(encoding="utf-8"))
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
    state_dir = _load_config(args).state_dir
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


def register_tmux_monitor_parser(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "tmux-monitor",
        help="tmux session monitoring daemon",
    )
    p.add_argument("--state-dir", help="Override state directory")
    p.add_argument("--json", action="store_true", help="JSON output")

    tmux_sub = p.add_subparsers(dest="tmux_action", required=True)

    start = tmux_sub.add_parser("start", help="Start the monitoring daemon")
    start.set_defaults(func=cmd_start)

    stop = tmux_sub.add_parser("stop", help="Stop the monitoring daemon")
    stop.set_defaults(func=cmd_stop)

    status = tmux_sub.add_parser("status", help="Show current tmux status")
    status.set_defaults(func=cmd_status)

    hb = tmux_sub.add_parser("heartbeat", help="Run a single heartbeat cycle")
    hb.set_defaults(func=cmd_heartbeat)

    sessions = tmux_sub.add_parser("sessions", help="List sessions with agent types")
    sessions.set_defaults(func=cmd_sessions)

    logs = tmux_sub.add_parser("logs", help="Show pane logs")
    logs.add_argument("pane", nargs="?", help="Pane identifier to tail")
    logs.add_argument("-n", "--lines", type=int, default=50, help="Number of lines")
    logs.set_defaults(func=cmd_logs)
