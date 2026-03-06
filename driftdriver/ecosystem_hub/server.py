# ABOUTME: ThreadingHTTPServer subclass, request routing, main serve loop, CLI entry point.
# ABOUTME: Service lifecycle: start, stop, foreground run, argument parsing, and daemon management.
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from driftdriver.factorydrift import (
    build_factory_cycle,
    emit_factory_followups,
    execute_factory_cycle,
    summarize_factory_cycle,
    write_factory_ledger,
)
from driftdriver.northstardrift import emit_northstar_review_tasks
from driftdriver.policy import load_drift_policy

from .api import _handler_factory
from .discovery import (
    _iso_now,
    _process_alive,
    _write_json,
    apply_upstream_automation,
    build_draft_pr_requests,
    resolve_central_repo_path,
    render_upstream_packets,
    run_draft_pr_requests,
)
from .models import UpstreamCandidate
from .snapshot import (
    _SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
    _SUPERVISOR_DEFAULT_MAX_STARTS,
    _decorate_snapshot_with_northstardrift,
    _northstardrift_config,
    collect_ecosystem_snapshot,
    read_service_status,
    service_paths,
    supervise_repo_services,
    write_snapshot_once,
)
from .websocket import LiveStreamHub

_CHILD_PROCS: dict[int, subprocess.Popen[str]] = {}


def start_service_process(
    *,
    project_dir: Path,
    workspace_root: Path,
    host: str,
    port: int,
    interval_seconds: int,
    include_updates: bool,
    max_next: int,
    ecosystem_toml: Path | None,
    central_repo: Path | None,
    execute_draft_prs: bool,
    draft_pr_title_prefix: str,
    supervise_services: bool = True,
    supervise_cooldown_seconds: int = _SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
    supervise_max_starts: int = _SUPERVISOR_DEFAULT_MAX_STARTS,
) -> dict[str, Any]:
    status = read_service_status(project_dir)
    if status.get("running"):
        return status

    paths = service_paths(project_dir)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    log_f = open(paths["log"], "a", encoding="utf-8")
    cmd = [
        sys.executable,
        "-m",
        "driftdriver.ecosystem_hub",
        "--project-dir",
        str(project_dir),
        "--workspace-root",
        str(workspace_root),
    ]
    if central_repo:
        cmd.extend(["--central-repo", str(central_repo)])
    cmd.extend(
        [
        "run-service",
        "--host",
        host,
        "--port",
        str(port),
        "--interval-seconds",
        str(interval_seconds),
        "--max-next",
        str(max_next),
        "--title-prefix",
        draft_pr_title_prefix,
        "--supervise-cooldown-seconds",
        str(max(1, int(supervise_cooldown_seconds))),
        "--supervise-max-starts",
        str(max(1, int(supervise_max_starts))),
        ]
    )
    if not supervise_services:
        cmd.append("--no-supervise-services")
    if not include_updates:
        cmd.append("--skip-updates")
    if ecosystem_toml:
        cmd.extend(["--ecosystem-toml", str(ecosystem_toml)])
    if execute_draft_prs:
        cmd.append("--execute-draft-prs")
    env = os.environ.copy()
    package_root = str(Path(__file__).resolve().parents[2])
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{package_root}:{existing_pp}" if existing_pp else package_root

    proc = subprocess.Popen(  # noqa: S603
        cmd,
        cwd=str(project_dir),
        stdout=log_f,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        env=env,
    )
    _CHILD_PROCS[proc.pid] = proc
    log_f.close()
    # Child writes pid too; this ensures there is immediate visibility.
    paths["pid"].write_text(str(proc.pid), encoding="utf-8")
    time.sleep(0.25)
    return read_service_status(project_dir)


def stop_service_process(project_dir: Path) -> dict[str, Any]:
    paths = service_paths(project_dir)
    status = read_service_status(project_dir)
    pid = status.get("pid")
    if not pid:
        return status
    pid_i = int(pid)
    try:
        os.kill(pid_i, signal.SIGTERM)
    except OSError:
        pass
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if not _process_alive(pid_i):
            break
        time.sleep(0.1)
    if _process_alive(pid_i):
        try:
            os.kill(pid_i, signal.SIGKILL)
        except OSError:
            pass
    proc = _CHILD_PROCS.pop(pid_i, None)
    if proc is not None:
        try:
            proc.wait(timeout=1.0)
        except Exception:
            pass
    try:
        paths["pid"].unlink(missing_ok=True)
    except Exception:
        pass
    return read_service_status(project_dir)


def run_service_foreground(
    *,
    project_dir: Path,
    workspace_root: Path,
    host: str,
    port: int,
    interval_seconds: int,
    include_updates: bool,
    max_next: int,
    ecosystem_toml: Path | None,
    central_repo: Path | None,
    execute_draft_prs: bool,
    draft_pr_title_prefix: str,
    supervise_services: bool,
    supervise_cooldown_seconds: int,
    supervise_max_starts: int,
) -> None:
    paths = service_paths(project_dir)
    paths["dir"].mkdir(parents=True, exist_ok=True)
    paths["pid"].write_text(str(os.getpid()), encoding="utf-8")
    _write_json(
        paths["state"],
        {
            "started_at": _iso_now(),
            "host": host,
            "port": port,
            "interval_seconds": interval_seconds,
            "include_updates": include_updates,
            "max_next": max_next,
            "central_repo": str(central_repo) if central_repo else "",
            "execute_draft_prs": execute_draft_prs,
            "draft_pr_title_prefix": draft_pr_title_prefix,
            "supervise_services": supervise_services,
            "supervise_cooldown_seconds": max(1, int(supervise_cooldown_seconds)),
            "supervise_max_starts": max(1, int(supervise_max_starts)),
        },
    )

    stop_event = threading.Event()
    live_hub = LiveStreamHub(stop_event)

    def _collector_loop() -> None:
        while not stop_event.is_set():
            try:
                snapshot = write_snapshot_once(
                    project_dir=project_dir,
                    workspace_root=workspace_root,
                    ecosystem_toml=ecosystem_toml,
                    include_updates=include_updates,
                    max_next=max_next,
                    central_repo=central_repo,
                )
                if supervise_services:
                    repos_payload = snapshot.get("repos")
                    supervisor = supervise_repo_services(
                        repos_payload=repos_payload if isinstance(repos_payload, list) else [],
                        cooldown_seconds=max(1, int(supervise_cooldown_seconds)),
                        max_starts=max(1, int(supervise_max_starts)),
                    )
                else:
                    supervisor = {
                        "enabled": False,
                        "cooldown_seconds": max(1, int(supervise_cooldown_seconds)),
                        "max_starts_per_cycle": max(1, int(supervise_max_starts)),
                        "checked_repos": 0,
                        "restart_candidates": 0,
                        "attempted": 0,
                        "started": 0,
                        "failed": 0,
                        "cooldown_skipped": 0,
                        "last_tick_at": _iso_now(),
                        "attempts": [],
                    }
                snapshot["supervisor"] = supervisor

                # Phase 0 dark-factory loop: produce policy-bounded cycle plan + decision ledger.
                wg_dir = project_dir / ".workgraph"
                try:
                    policy = load_drift_policy(wg_dir)
                    factory_cfg = policy.factory if isinstance(policy.factory, dict) else {}
                except Exception:
                    policy = None
                    factory_cfg = {}

                factory_enabled = bool(factory_cfg.get("enabled", False))
                if factory_enabled and policy is not None:
                    cycle = build_factory_cycle(
                        snapshot=snapshot,
                        policy=policy,
                        project_name=project_dir.name,
                    )
                    execution_mode = str(cycle.get("execution_mode") or "plan_only")
                    emit_followups = bool(factory_cfg.get("emit_followups", False))
                    execution = {
                        "attempted": 0,
                        "executed": 0,
                        "succeeded": 0,
                        "failed": 0,
                        "skipped": 0,
                        "hard_stop": bool(factory_cfg.get("hard_stop_on_failed_verification", True)),
                        "stopped_early": False,
                        "stop_reason": "",
                        "attempts": [],
                        "followups": {},
                    }
                    followups = {
                        "enabled": emit_followups,
                        "attempted": 0,
                        "created": 0,
                        "existing": 0,
                        "skipped": 0,
                        "errors": [],
                        "tasks": [],
                    }
                    snapshot["factory"] = {
                        "enabled": True,
                        "summary": summarize_factory_cycle(cycle),
                        "action_plan": cycle.get("action_plan") if isinstance(cycle.get("action_plan"), list) else [],
                        "execution": {
                            **execution,
                            "phase": "executing" if execution_mode != "plan_only" else "planned",
                        },
                        "followups": followups,
                        "ledger": {},
                    }
                    _write_json(paths["snapshot"], snapshot)
                    _write_json(
                        paths["heartbeat"],
                        {
                            "last_tick_at": _iso_now(),
                            "phase": "factory-executing" if execution_mode != "plan_only" else "factory-planned",
                            "supervisor": supervisor,
                        },
                    )
                    live_hub.broadcast_snapshot(snapshot)
                    if execution_mode != "plan_only":
                        execution = execute_factory_cycle(
                            cycle=cycle,
                            snapshot=snapshot,
                            policy=policy,
                            project_dir=project_dir,
                            emit_followups=emit_followups,
                            max_followups_per_repo=max(1, int(factory_cfg.get("max_followups_per_repo", 2))),
                            allow_execute_draft_prs=bool(execute_draft_prs),
                        )
                        followups = execution.get("followups") if isinstance(execution.get("followups"), dict) else followups
                    elif emit_followups:
                        followups = emit_factory_followups(
                            cycle=cycle,
                            snapshot=snapshot,
                            max_followups_per_repo=max(1, int(factory_cfg.get("max_followups_per_repo", 2))),
                        )
                        execution["followups"] = followups
                    factory_ledger = write_factory_ledger(
                        project_dir=project_dir,
                        cycle=cycle,
                        central_repo=central_repo,
                        write_decision_ledger=bool(factory_cfg.get("write_decision_ledger", True)),
                    )
                    snapshot["factory"] = {
                        "enabled": True,
                        "summary": summarize_factory_cycle(cycle),
                        "action_plan": cycle.get("action_plan") if isinstance(cycle.get("action_plan"), list) else [],
                        "execution": {
                            **execution,
                            "phase": "completed",
                        },
                        "followups": followups,
                        "ledger": factory_ledger,
                    }
                else:
                    snapshot["factory"] = {
                        "enabled": False,
                        "reason": "factory disabled in drift-policy",
                    }

                _decorate_snapshot_with_northstardrift(
                    project_dir=project_dir,
                    snapshot=snapshot,
                    central_repo=central_repo,
                )
                northstar_cfg = _northstardrift_config(project_dir)
                if bool(northstar_cfg.get("enabled", True)) and bool(northstar_cfg.get("emit_review_tasks", True)):
                    task_emit = emit_northstar_review_tasks(
                        snapshot=snapshot,
                        report=snapshot.get("northstardrift") if isinstance(snapshot.get("northstardrift"), dict) else {},
                        config=northstar_cfg,
                    )
                else:
                    task_emit = {
                        "enabled": False,
                        "attempted": 0,
                        "created": 0,
                        "existing": 0,
                        "skipped": 0,
                        "errors": [],
                        "tasks": [],
                    }
                if isinstance(snapshot.get("northstardrift"), dict):
                    snapshot["northstardrift"]["task_emit"] = task_emit
                _write_json(paths["snapshot"], snapshot)
                _write_json(paths["heartbeat"], {"last_tick_at": _iso_now(), "supervisor": supervisor})
                candidates = [
                    UpstreamCandidate(**row)
                    for row in snapshot.get("upstream_candidates", [])
                    if isinstance(row, dict)
                ]
                apply_upstream_automation(
                    service_dir=paths["dir"],
                    candidates=candidates,
                    title_prefix=draft_pr_title_prefix,
                    execute_draft_prs=execute_draft_prs,
                )
                live_hub.broadcast_snapshot(snapshot)
            except Exception as exc:
                _write_json(paths["heartbeat"], {"last_tick_at": _iso_now(), "error": str(exc)})
            stop_event.wait(max(2, interval_seconds))

    collector = threading.Thread(target=_collector_loop, name="ecosystem-hub-collector", daemon=True)
    collector.start()

    handler_cls = _handler_factory(paths["snapshot"], paths["state"], live_hub)
    server = ThreadingHTTPServer((host, port), handler_cls)

    def _graceful_shutdown(_signum: int, _frame: Any) -> None:
        stop_event.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, _graceful_shutdown)
    signal.signal(signal.SIGINT, _graceful_shutdown)

    try:
        server.serve_forever()
    finally:
        stop_event.set()
        server.server_close()
        try:
            paths["pid"].unlink(missing_ok=True)
        except Exception:
            pass


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ecosystem-hub")
    p.add_argument("--project-dir", default=str(Path.cwd()), help="Project repo root (default: cwd)")
    p.add_argument(
        "--workspace-root",
        default="",
        help="Workspace root containing speedrift repos (default: parent of project-dir)",
    )
    p.add_argument(
        "--ecosystem-toml",
        default="",
        help="Path to ecosystem.toml (default: <workspace-root>/speedrift-ecosystem/ecosystem.toml)",
    )
    p.add_argument(
        "--central-repo",
        default="",
        help="Optional central register/report repo path (default: derived from drift-policy reporting.central_repo)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    once = sub.add_parser("once", help="Collect one ecosystem snapshot and print JSON")
    once.add_argument("--skip-updates", action="store_true", help="Skip remote update checks for this run")
    once.add_argument("--max-next", type=int, default=5, help="Max next-work items per repo")
    once.add_argument("--write", default="", help="Optional file path for writing snapshot JSON")

    start = sub.add_parser("start", help="Start daemonized ecosystem hub service")
    start.add_argument("--host", default="127.0.0.1", help="Bind host for web server")
    start.add_argument("--port", type=int, default=8777, help="Bind port for web server")
    start.add_argument("--interval-seconds", type=int, default=60, help="Snapshot refresh interval")
    start.add_argument("--skip-updates", action="store_true", help="Skip remote update checks while running")
    start.add_argument("--max-next", type=int, default=5, help="Max next-work items per repo")
    start.add_argument("--execute-draft-prs", action="store_true", help="Execute draft PR creation each cycle")
    start.add_argument("--title-prefix", default="speedrift", help="Title prefix for draft PR automation")
    start.add_argument(
        "--no-supervise-services",
        action="store_true",
        help="Disable central supervision/restart of stopped repo workgraph services",
    )
    start.add_argument(
        "--supervise-cooldown-seconds",
        type=int,
        default=_SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
        help="Minimum seconds between restart attempts per repo",
    )
    start.add_argument(
        "--supervise-max-starts",
        type=int,
        default=_SUPERVISOR_DEFAULT_MAX_STARTS,
        help="Maximum repo service start attempts per collector cycle",
    )

    run_service = sub.add_parser("run-service", help="Internal: run service in foreground")
    run_service.add_argument("--host", default="127.0.0.1", help="Bind host for web server")
    run_service.add_argument("--port", type=int, default=8777, help="Bind port for web server")
    run_service.add_argument("--interval-seconds", type=int, default=60, help="Snapshot refresh interval")
    run_service.add_argument("--skip-updates", action="store_true", help="Skip remote update checks while running")
    run_service.add_argument("--max-next", type=int, default=5, help="Max next-work items per repo")
    run_service.add_argument("--execute-draft-prs", action="store_true", help="Execute draft PR creation each cycle")
    run_service.add_argument("--title-prefix", default="speedrift", help="Title prefix for draft PR automation")
    run_service.add_argument(
        "--no-supervise-services",
        action="store_true",
        help="Disable central supervision/restart of stopped repo workgraph services",
    )
    run_service.add_argument(
        "--supervise-cooldown-seconds",
        type=int,
        default=_SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
        help="Minimum seconds between restart attempts per repo",
    )
    run_service.add_argument(
        "--supervise-max-starts",
        type=int,
        default=_SUPERVISOR_DEFAULT_MAX_STARTS,
        help="Maximum repo service start attempts per collector cycle",
    )

    automate = sub.add_parser("automate", help="Ensure unattended automation is running (start if needed)")
    automate.add_argument("--host", default="127.0.0.1", help="Bind host for web server")
    automate.add_argument("--port", type=int, default=8777, help="Bind port for web server")
    automate.add_argument("--interval-seconds", type=int, default=60, help="Snapshot refresh interval")
    automate.add_argument("--skip-updates", action="store_true", help="Skip remote update checks while running")
    automate.add_argument("--max-next", type=int, default=5, help="Max next-work items per repo")
    automate.add_argument("--execute-draft-prs", action="store_true", help="Execute draft PR creation each cycle")
    automate.add_argument("--title-prefix", default="speedrift", help="Title prefix for draft PR automation")
    automate.add_argument(
        "--no-supervise-services",
        action="store_true",
        help="Disable central supervision/restart of stopped repo workgraph services",
    )
    automate.add_argument(
        "--supervise-cooldown-seconds",
        type=int,
        default=_SUPERVISOR_DEFAULT_COOLDOWN_SECONDS,
        help="Minimum seconds between restart attempts per repo",
    )
    automate.add_argument(
        "--supervise-max-starts",
        type=int,
        default=_SUPERVISOR_DEFAULT_MAX_STARTS,
        help="Maximum repo service start attempts per collector cycle",
    )

    sub.add_parser("status", help="Show daemon status")
    sub.add_parser("stop", help="Stop daemonized ecosystem hub service")

    packets = sub.add_parser("upstream-report", help="Write markdown packet of upstream contribution candidates")
    packets.add_argument("--output", default="", help="Output markdown path (default: stdout)")

    pr_open = sub.add_parser(
        "open-draft-pr",
        help="Prepare or execute draft PRs from detected upstream candidates (dry-run by default)",
    )
    pr_open.add_argument("--repo", default="", help="Filter to a single repo name")
    pr_open.add_argument("--title-prefix", default="speedrift", help="Prefix for draft PR titles")
    pr_open.add_argument(
        "--execute",
        action="store_true",
        help="Actually run gh pr create (default is dry-run output only)",
    )

    return p


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    project_dir = Path(args.project_dir).resolve()
    workspace_root = Path(args.workspace_root).resolve() if args.workspace_root else project_dir.parent
    ecosystem_toml = Path(args.ecosystem_toml).resolve() if args.ecosystem_toml else None
    central_repo = resolve_central_repo_path(project_dir, explicit_path=str(args.central_repo))

    if args.cmd == "once":
        snapshot = write_snapshot_once(
            project_dir=project_dir,
            workspace_root=workspace_root,
            ecosystem_toml=ecosystem_toml,
            include_updates=not bool(args.skip_updates),
            max_next=max(1, int(args.max_next)),
            central_repo=central_repo,
        )
        blob = json.dumps(snapshot, indent=2, sort_keys=False)
        if args.write:
            out = Path(args.write).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(blob + "\n", encoding="utf-8")
        print(blob)
        return 0

    if args.cmd == "start":
        status = start_service_process(
            project_dir=project_dir,
            workspace_root=workspace_root,
            host=str(args.host),
            port=int(args.port),
            interval_seconds=max(2, int(args.interval_seconds)),
            include_updates=not bool(args.skip_updates),
            max_next=max(1, int(args.max_next)),
            ecosystem_toml=ecosystem_toml,
            central_repo=central_repo,
            execute_draft_prs=bool(args.execute_draft_prs),
            draft_pr_title_prefix=str(args.title_prefix),
            supervise_services=not bool(args.no_supervise_services),
            supervise_cooldown_seconds=max(1, int(args.supervise_cooldown_seconds)),
            supervise_max_starts=max(1, int(args.supervise_max_starts)),
        )
        print(json.dumps(status, indent=2, sort_keys=False))
        return 0

    if args.cmd == "run-service":
        run_service_foreground(
            project_dir=project_dir,
            workspace_root=workspace_root,
            host=str(args.host),
            port=int(args.port),
            interval_seconds=max(2, int(args.interval_seconds)),
            include_updates=not bool(args.skip_updates),
            max_next=max(1, int(args.max_next)),
            ecosystem_toml=ecosystem_toml,
            central_repo=central_repo,
            execute_draft_prs=bool(args.execute_draft_prs),
            draft_pr_title_prefix=str(args.title_prefix),
            supervise_services=not bool(args.no_supervise_services),
            supervise_cooldown_seconds=max(1, int(args.supervise_cooldown_seconds)),
            supervise_max_starts=max(1, int(args.supervise_max_starts)),
        )
        return 0

    if args.cmd == "automate":
        status = start_service_process(
            project_dir=project_dir,
            workspace_root=workspace_root,
            host=str(args.host),
            port=int(args.port),
            interval_seconds=max(2, int(args.interval_seconds)),
            include_updates=not bool(args.skip_updates),
            max_next=max(1, int(args.max_next)),
            ecosystem_toml=ecosystem_toml,
            central_repo=central_repo,
            execute_draft_prs=bool(args.execute_draft_prs),
            draft_pr_title_prefix=str(args.title_prefix),
            supervise_services=not bool(args.no_supervise_services),
            supervise_cooldown_seconds=max(1, int(args.supervise_cooldown_seconds)),
            supervise_max_starts=max(1, int(args.supervise_max_starts)),
        )
        print(json.dumps({"automated": True, "status": status}, indent=2, sort_keys=False))
        return 0

    if args.cmd == "status":
        print(json.dumps(read_service_status(project_dir), indent=2, sort_keys=False))
        return 0

    if args.cmd == "stop":
        print(json.dumps(stop_service_process(project_dir), indent=2, sort_keys=False))
        return 0

    if args.cmd == "upstream-report":
        snapshot = collect_ecosystem_snapshot(
            project_dir=project_dir,
            workspace_root=workspace_root,
            ecosystem_toml=ecosystem_toml,
            include_updates=False,
            max_next=3,
            central_repo=central_repo,
        )
        candidates = [
            UpstreamCandidate(**row)
            for row in snapshot.get("upstream_candidates", [])
            if isinstance(row, dict)
        ]
        md = render_upstream_packets(candidates)
        if args.output:
            out = Path(args.output).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
        else:
            print(md, end="")
        return 0

    if args.cmd == "open-draft-pr":
        snapshot = collect_ecosystem_snapshot(
            project_dir=project_dir,
            workspace_root=workspace_root,
            ecosystem_toml=ecosystem_toml,
            include_updates=False,
            max_next=3,
            central_repo=central_repo,
        )
        candidates = [
            UpstreamCandidate(**row)
            for row in snapshot.get("upstream_candidates", [])
            if isinstance(row, dict)
        ]
        if args.repo:
            candidates = [c for c in candidates if c.repo == str(args.repo)]
        requests = build_draft_pr_requests(candidates, title_prefix=str(args.title_prefix))
        result = {
            "execute": bool(args.execute),
            "request_count": len(requests),
            "requests": run_draft_pr_requests(requests, execute=bool(args.execute)),
        }
        print(json.dumps(result, indent=2, sort_keys=False))
        return 0

    parser.error(f"unknown command: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
