# ABOUTME: ThreadingHTTPServer subclass, request routing, main serve loop, CLI entry point.
# ABOUTME: Service lifecycle: start, stop, foreground run, argument parsing, and daemon management.
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

from driftdriver.directives import DirectiveLog
from driftdriver.notifications import (
    NotificationDispatcher,
    load_notification_config,
    process_snapshot_notifications,
)
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

_log = __import__("logging").getLogger(__name__)

_CHILD_PROCS: dict[int, subprocess.Popen[str]] = {}


def _clear_stale_graph_locks(workspace_root: Path, *, max_depth: int = 3) -> int:
    """Remove zero-byte graph.lock files left by crashed wg processes.

    These stale locks cause every ``wg`` CLI command to hang indefinitely,
    which in turn crashes the factory execution cycle.  Runs at hub startup
    and at the beginning of each collector tick.

    Returns the number of lock files removed.
    """
    cleared = 0

    def _scan(directory: Path, depth: int) -> None:
        nonlocal cleared
        if depth > max_depth:
            return
        try:
            entries = sorted(directory.iterdir())
        except (PermissionError, OSError):
            return
        for entry in entries:
            if not entry.is_dir():
                continue
            if entry.name.startswith(".") and entry.name != ".workgraph":
                continue
            if entry.name == ".workgraph":
                lock = entry / "graph.lock"
                try:
                    if lock.exists() and lock.stat().st_size == 0:
                        lock.unlink()
                        cleared += 1
                except OSError:
                    pass
                continue
            _scan(entry, depth + 1)

    _scan(workspace_root, 0)
    if cleared:
        _log.info("Cleared %d stale graph.lock file(s) under %s", cleared, workspace_root)
    return cleared


def _run_upstream_pass1(project_dir: Path) -> None:
    """Run upstream pass1 tracker for the project_dir repo.

    Reads .driftdriver/upstream-config.toml, evaluates tracked external repos
    for new commits, and writes .driftdriver/upstream-tracker-last.json so the
    next snapshot tick surfaces upstream_eval in the hub snapshot.
    Errors are caught by caller — this function may raise.
    """
    import sys
    config_path = project_dir / ".driftdriver" / "upstream-config.toml"
    if not config_path.exists():
        return

    if sys.version_info >= (3, 11):
        import tomllib
    else:
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

    config = tomllib.loads(config_path.read_text(encoding="utf-8"))
    if not config.get("external_repos"):
        return

    from driftdriver.upstream_tracker import run_pass1

    pins_path = project_dir / ".driftdriver" / "upstream-pins.toml"
    results = run_pass1(config, pins_path)
    if results:
        _log.info("upstream pass1: %d repo(s) with new commits", len(results))


def _port_is_available(host: str, port: int) -> bool:
    """Check whether *port* can be bound. Returns False if already in use."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return True
    except OSError:
        return False


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

    # Notification dispatcher — persists across ticks for cooldown tracking.
    wg_dir_for_notify = project_dir / ".workgraph"
    policy_toml_path = wg_dir_for_notify / "drift-policy.toml"
    notify_config = load_notification_config(policy_toml_path)
    notify_dispatcher = NotificationDispatcher(notify_config)
    outcome_ledger = wg_dir_for_notify / "drift-outcomes.jsonl"

    directive_log = DirectiveLog(project_dir / ".workgraph" / "service" / "directives")

    # Factory brain — model-mediated decisions (optional, safe to fail)
    _factory_brain = None
    try:
        from driftdriver.factory_brain.hub_integration import FactoryBrain

        brain_data_dir = Path.home() / ".config" / "workgraph" / "factory-brain"
        brain_data_dir.mkdir(parents=True, exist_ok=True)
        _factory_brain = FactoryBrain(
            hub_data_dir=brain_data_dir,
            workspace_roots=[workspace_root],
            dry_run=False,
        )
        _log.info("Factory brain initialized (roster: %s)", _factory_brain.roster_file)
    except Exception:
        _log.debug("Factory brain not available — skipping", exc_info=True)

    # Auto-restart: track source file timestamps at startup.
    # If any driftdriver source changes, the hub restarts itself.
    _driftdriver_src = Path(__file__).resolve().parent.parent
    _startup_mtimes: dict[str, float] = {}
    for _pyf in _driftdriver_src.rglob("*.py"):
        try:
            _startup_mtimes[str(_pyf)] = _pyf.stat().st_mtime
        except OSError:
            pass
    _startup_time = time.time()

    def _source_changed() -> bool:
        """Check if any driftdriver source file changed since startup."""
        for path_str, mtime in _startup_mtimes.items():
            try:
                if Path(path_str).stat().st_mtime > mtime:
                    return True
            except OSError:
                pass
        # Also check for new files
        for pyf in _driftdriver_src.rglob("*.py"):
            if str(pyf) not in _startup_mtimes and pyf.stat().st_mtime > _startup_time:
                return True
        return False

    def _collector_loop() -> None:
        while not stop_event.is_set():
            # Auto-restart check: if source code changed, restart the process.
            try:
                if _source_changed():
                    _log.info("Source code changed — restarting hub process")
                    import os
                    os.execv(sys.executable, [sys.executable] + sys.argv)
            except Exception:
                pass  # Never crash the collector for a restart check

            # Sweep stale graph.lock files each tick — crashed wg processes
            # leave empty lock files that hang all subsequent wg commands.
            _clear_stale_graph_locks(workspace_root)

            # Run pass1 upstream tracker: detect upstream changes and write state
            # file for snapshot to read. Errors are logged, never block the tick.
            try:
                _run_upstream_pass1(project_dir)
            except Exception:
                _log.debug("upstream pass1 failed", exc_info=True)

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
                        directive_log=directive_log,
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

                # Governance enforcement: stop daemons on repos that shouldn't have them.
                if supervise_services:
                    try:
                        from driftdriver.governancedrift import enforce_daemon_posture

                        enforcement = enforce_daemon_posture(
                            repos_payload=repos_payload if isinstance(repos_payload, list) else [],
                            directive_log=directive_log,
                        )
                        snapshot["daemon_enforcement"] = enforcement
                        if enforcement.get("actions"):
                            _log.info(
                                "Daemon enforcement: stopped %d service(s) (%s)",
                                len(enforcement["actions"]),
                                ", ".join(a["repo"] for a in enforcement["actions"]),
                            )
                    except Exception:
                        _log.debug("Daemon enforcement failed", exc_info=True)
                        snapshot["daemon_enforcement"] = {"enabled": False, "error": "enforcement failed"}
                else:
                    snapshot["daemon_enforcement"] = {"enabled": False}

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

                # Factory brain — model-mediated decisions
                if _factory_brain is not None:
                    try:
                        brain_results = _factory_brain.tick(
                            snapshot=snapshot,
                            heuristic_recommendation=cycle if factory_enabled and policy is not None else None,
                        )
                        if brain_results:
                            _log.info("Factory brain: %d tier invocations", len(brain_results))
                        snapshot["factory_brain"] = {
                            "enabled": True,
                            "tick_results": len(brain_results),
                        }
                    except Exception:
                        _log.exception("Factory brain tick failed")
                        snapshot["factory_brain"] = {
                            "enabled": True,
                            "error": "tick failed",
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

                # Proactive notifications — fire alerts for significant findings.
                try:
                    notify_result = process_snapshot_notifications(
                        snapshot,
                        notify_config,
                        outcome_ledger_path=outcome_ledger if outcome_ledger.exists() else None,
                        dispatcher=notify_dispatcher,
                    )
                    snapshot["notifications"] = notify_result
                except Exception:
                    snapshot["notifications"] = {"enabled": False, "error": "dispatch failed"}
            except Exception as exc:
                _write_json(paths["heartbeat"], {"last_tick_at": _iso_now(), "error": str(exc)})
            stop_event.wait(max(2, interval_seconds))

    collector = threading.Thread(target=_collector_loop, name="ecosystem-hub-collector", daemon=True)
    collector.start()

    from driftdriver.ecosystem_hub.activity import scan_all_repos
    from driftdriver.ecosystem_hub.activity_cache import read_activity_digest, write_activity_digest
    from driftdriver.ecosystem_hub.activity_summarizer import summarize_all
    from driftdriver.ecosystem_hub.discovery import (
        _load_ecosystem_repos,
        _discover_active_workspace_repos,
    )

    _ACTIVITY_INTERVAL = 15 * 60  # 15 minutes

    def _activity_scanner_loop() -> None:
        while not stop_event.is_set():
            try:
                # Build repo map the same way the collector does
                ecosystem_file = ecosystem_toml or (workspace_root / "speedrift-ecosystem" / "ecosystem.toml")
                repo_map: dict[str, Path] = _load_ecosystem_repos(ecosystem_file, workspace_root)
                if project_dir.name not in repo_map:
                    repo_map[project_dir.name] = project_dir
                discovered = _discover_active_workspace_repos(workspace_root, existing=set(repo_map.keys()))
                repo_map.update(discovered)

                raw_digests = scan_all_repos(repo_map)
                existing = read_activity_digest(paths["activity"])
                existing_by_name = {r["name"]: r for r in existing.get("repos", [])}
                merged = []
                for d in raw_digests:
                    prev = existing_by_name.get(d["name"], {})
                    if prev.get("summary_hash") == d.get("last_commit_hash") and prev.get("summary"):
                        d = {**d, "summary": prev["summary"], "summary_hash": prev["summary_hash"]}
                    merged.append(d)
                # Carry forward previous entries for repos that failed this scan
                # (git error → silently skipped, previous data preserved per spec)
                raw_names = {d["name"] for d in raw_digests}
                for name, prev_entry in existing_by_name.items():
                    if name not in raw_names:
                        merged.append(prev_entry)
                summarized = summarize_all(merged)
                write_activity_digest(paths["activity"], {
                    "generated_at": _iso_now(),
                    "repos": summarized,
                })
            except Exception:
                _log.debug("Activity scanner error", exc_info=True)
            stop_event.wait(_ACTIVITY_INTERVAL)

    activity_scanner = threading.Thread(
        target=_activity_scanner_loop,
        name="activity-scanner",
        daemon=True,
    )
    activity_scanner.start()

    if not _port_is_available(host, port):
        _log.error(
            "Port %d on %s is already in use — refusing to start a second hub. "
            "Kill the existing process or choose a different port.",
            port,
            host,
        )
        raise SystemExit(1)

    handler_cls = _handler_factory(paths["snapshot"], paths["state"], live_hub, paths["activity"])
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
