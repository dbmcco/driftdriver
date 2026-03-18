# ABOUTME: 'driftdriver debate' subcommand — start, status, conclude debate sessions.
# ABOUTME: Wraps the debatedrift session launcher and aggregator.
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from driftdriver.workgraph import find_workgraph_dir


def cmd_debate_start(args: argparse.Namespace) -> int:
    task_id = str(args.task or "").strip()
    if not task_id:
        print("error: --task is required", file=sys.stderr)
        return 2

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent

    from driftdriver.workgraph import load_workgraph
    wg = load_workgraph(wg_dir)
    task = wg.tasks.get(task_id)
    if not task:
        print(f"error: task {task_id!r} not found in workgraph", file=sys.stderr)
        return 2

    description = str(task.get("description") or "")
    title = str(task.get("title") or task_id)

    from driftdriver.debatedrift.config import parse_debatedrift_config
    cfg = parse_debatedrift_config(description)
    if cfg is None:
        print(
            f"error: task {task_id!r} has no debatedrift fence in its description",
            file=sys.stderr,
        )
        return 2

    from driftdriver.debatedrift.session import launch_debate_session
    session = launch_debate_session(
        task_id=task_id,
        topic=title,
        config=cfg,
        workgraph_dir=wg_dir,
    )

    if args.watch:
        return _watch_loop(session=session, wg_dir=wg_dir, cfg=cfg, task_id=task_id)

    print(f"Session started. Attach: tmux attach -t {session.tmux_session}")
    print(f"Logs: {session.debate_dir}")
    print(f"Status: driftdriver debate status --task {task_id}")
    return 0


def _watch_loop(*, session: object, wg_dir: Path, cfg: object, task_id: str) -> int:
    from driftdriver.debatedrift.aggregator import AggregatorState, merge_logs, send_nudge

    state = AggregatorState()
    debate_log = session.debate_dir / "debate.log"  # type: ignore[attr-defined]
    last_nudge: dict[str, float] = {"a": 0.0, "b": 0.0}
    poll_interval = 10  # seconds

    print("Watching debate session (Ctrl-C to detach)...")
    try:
        while not state.terminated:
            state.update(debate_dir=session.debate_dir)  # type: ignore[attr-defined]
            merge_logs(debate_dir=session.debate_dir, output_path=debate_log)  # type: ignore[attr-defined]

            now = time.time()
            # Check for stalled debaters
            for pane, log_name in [("a", "pane-a.log"), ("b", "pane-b.log")]:
                pane_log = session.debate_dir / log_name  # type: ignore[attr-defined]
                mtime = pane_log.stat().st_mtime if pane_log.exists() else 0.0
                elapsed = now - mtime
                if elapsed > cfg.watchdog_timeout and now - last_nudge[pane] > cfg.watchdog_timeout:  # type: ignore[attr-defined]
                    send_nudge(task_id=task_id, pane=f"pane-{pane}")
                    last_nudge[pane] = now
                    print(f"nudge sent to pane-{pane} (silent {elapsed:.0f}s)")

            # Check round cap
            if state.round_count >= cfg.max_rounds * 2:  # type: ignore[attr-defined]
                print(f"Round cap ({cfg.max_rounds}) reached — concluding debate")  # type: ignore[attr-defined]
                _force_conclude(task_id=task_id)
                break

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nDetached from watch loop. Session continues.")
        return 0

    _on_termination(state=state, session=session, task_id=task_id, wg_dir=wg_dir)
    return 0


def _force_conclude(task_id: str) -> None:
    import subprocess
    subprocess.call(
        ["wg", "msg", "send", task_id,
         "Round cap reached. Write DEBATE:CONCLUDED now with your final decision."],
        capture_output=True,
    )


def _on_termination(*, state: object, session: object, task_id: str, wg_dir: Path) -> None:
    import subprocess
    from driftdriver.debatedrift.session import teardown_session

    kind = getattr(state, "termination_kind", None) or "unknown"
    print(f"Debate terminated: {kind}")

    # Write wg log entry
    subprocess.call(
        ["wg", "log", task_id, f"debatedrift: terminated ({kind})"],
        capture_output=True,
    )

    teardown_session(session)
    print(f"Summary: {session.debate_dir}/summary.md")  # type: ignore[attr-defined]
    print(f"Log: {session.debate_dir}/debate.log")  # type: ignore[attr-defined]


def cmd_debate_status(args: argparse.Namespace) -> int:
    task_id = str(args.task or "").strip()
    if not task_id:
        print("error: --task is required", file=sys.stderr)
        return 2

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    debate_dir = wg_dir / ".debatedrift" / task_id

    if not debate_dir.exists():
        print(f"No debate session found for task {task_id}")
        return 0

    from driftdriver.debatedrift.aggregator import AggregatorState
    state = AggregatorState()
    state.update(debate_dir=debate_dir)

    print(f"Task:        {task_id}")
    print(f"Rounds:      {state.round_count}")
    print(f"Terminated:  {state.terminated}")
    if state.termination_kind:
        print(f"Outcome:     {state.termination_kind}")
    print(f"Logs:        {debate_dir}")
    return 0


def cmd_debate_conclude(args: argparse.Namespace) -> int:
    task_id = str(args.task or "").strip()
    if not task_id:
        print("error: --task is required", file=sys.stderr)
        return 2

    import subprocess
    subprocess.call(
        ["wg", "msg", "send", task_id,
         "Human requests immediate conclusion. Write DEBATE:CONCLUDED now."],
        capture_output=True,
    )
    print(f"Conclude signal sent to task {task_id}")
    return 0


def register_debate_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register 'driftdriver debate' subcommand with start/status/conclude."""
    debate_parser = subparsers.add_parser("debate", help="manage debatedrift sessions")
    debate_sub = debate_parser.add_subparsers(dest="debate_command")

    start_p = debate_sub.add_parser("start", help="launch a debate session for a task")
    start_p.add_argument("--task", required=True, help="task ID")
    start_p.add_argument("--dir", default="", help="project directory")
    start_p.add_argument("--watch", action="store_true", help="watch and manage the session loop")

    status_p = debate_sub.add_parser("status", help="show debate session status")
    status_p.add_argument("--task", required=True, help="task ID")
    status_p.add_argument("--dir", default="", help="project directory")

    conclude_p = debate_sub.add_parser("conclude", help="signal proxy to conclude immediately")
    conclude_p.add_argument("--task", required=True, help="task ID")
    conclude_p.add_argument("--dir", default="", help="project directory")

    debate_parser.set_defaults(func=_dispatch_debate)


def _dispatch_debate(args: argparse.Namespace) -> int:
    cmd = str(getattr(args, "debate_command", "") or "").strip()
    if cmd == "start":
        return cmd_debate_start(args)
    if cmd == "status":
        return cmd_debate_status(args)
    if cmd == "conclude":
        return cmd_debate_conclude(args)
    print("usage: driftdriver debate {start|status|conclude} --task <id>", file=sys.stderr)
    return 2
