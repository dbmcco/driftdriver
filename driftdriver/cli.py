from __future__ import annotations

import argparse
import json
import subprocess
import sys
import shutil
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Any

from driftdriver.install import (
    InstallResult,
    ensure_executor_guidance,
    ensure_datadrift_gitignore,
    ensure_depsdrift_gitignore,
    ensure_redrift_gitignore,
    ensure_specdrift_gitignore,
    ensure_speedrift_gitignore,
    ensure_therapydrift_gitignore,
    ensure_uxdrift_gitignore,
    ensure_yagnidrift_gitignore,
    resolve_bin,
    write_datadrift_wrapper,
    write_depsdrift_wrapper,
    write_drifts_wrapper,
    write_driver_wrapper,
    write_redrift_wrapper,
    write_specdrift_wrapper,
    write_speedrift_wrapper,
    write_therapydrift_wrapper,
    write_uxdrift_wrapper,
    write_yagnidrift_wrapper,
)
from driftdriver.policy import ensure_drift_policy, load_drift_policy
from driftdriver.workgraph import find_workgraph_dir, load_workgraph


class ExitCode:
    ok = 0
    findings = 3
    usage = 2


OPTIONAL_PLUGINS = [
    "specdrift",
    "datadrift",
    "depsdrift",
    "uxdrift",
    "therapydrift",
    "yagnidrift",
    "redrift",
]


def _run(cmd: list[str]) -> int:
    return subprocess.call(cmd)


def _ensure_wg_init(project_dir: Path) -> None:
    wg_dir = project_dir / ".workgraph"
    if (wg_dir / "graph.jsonl").exists():
        return
    subprocess.check_call(["wg", "init"], cwd=str(project_dir))


def _task_has_fence(*, wg_dir: Path, task_id: str, fence: str) -> bool:
    wg = load_workgraph(wg_dir)
    t = wg.tasks.get(task_id)
    if not t:
        return False
    desc = str(t.get("description") or "")
    return f"```{fence}" in desc


def _ordered_optional_plugins(policy_order: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for raw in policy_order:
        plugin = str(raw or "").strip()
        if plugin in OPTIONAL_PLUGINS and plugin not in seen:
            ordered.append(plugin)
            seen.add(plugin)
    for plugin in OPTIONAL_PLUGINS:
        if plugin not in seen:
            ordered.append(plugin)
    return ordered


def _plugin_supports_json(plugin: str) -> bool:
    return plugin != "uxdrift"


def _plugin_cmd(
    *,
    plugin: str,
    plugin_bin: Path,
    project_dir: Path,
    task_id: str,
    want_json: bool,
    write_log: bool,
    create_followups: bool,
) -> list[str]:
    if plugin == "uxdrift":
        cmd = [str(plugin_bin), "wg", "--dir", str(project_dir), "check", "--task", task_id]
    else:
        cmd = [str(plugin_bin), "--dir", str(project_dir)]
        if want_json and _plugin_supports_json(plugin):
            cmd.append("--json")
        cmd.extend(["wg", "check", "--task", task_id])
    if write_log:
        cmd.append("--write-log")
    if create_followups:
        cmd.append("--create-followups")
    return cmd


def _run_optional_plugin_json(
    *,
    plugin: str,
    wg_dir: Path,
    project_dir: Path,
    task_id: str,
    mode: str,
    force_write_log: bool,
    force_create_followups: bool,
) -> dict[str, Any]:
    plugin_bin = wg_dir / plugin
    if not plugin_bin.exists():
        return {"ran": False, "exit_code": 0, "report": None}
    if not _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence=plugin):
        return {"ran": False, "exit_code": 0, "report": None}

    write_log, create_followups = _mode_flags(mode=mode, plugin=plugin)
    write_log = write_log or force_write_log
    create_followups = create_followups or force_create_followups
    cmd = _plugin_cmd(
        plugin=plugin,
        plugin_bin=plugin_bin,
        project_dir=project_dir,
        task_id=task_id,
        want_json=True,
        write_log=write_log,
        create_followups=create_followups,
    )
    proc = subprocess.run(cmd, text=True, capture_output=True)
    rc = int(proc.returncode)
    if rc in (ExitCode.ok, ExitCode.findings):
        if _plugin_supports_json(plugin):
            try:
                report: Any = json.loads(proc.stdout or "{}")
            except Exception:
                report = {"raw": proc.stdout}
            return {"ran": True, "exit_code": rc, "report": report}
        return {"ran": True, "exit_code": rc, "report": None}

    # Optional plugins are best-effort: preserve an error report, but do not fail unified checks.
    err_report = {
        "error": f"{plugin} failed",
        "exit_code": rc,
        "stderr": (proc.stderr or "")[:4000],
    }
    return {"ran": True, "exit_code": 0, "report": err_report}


def _run_optional_plugin_text(
    *,
    plugin: str,
    wg_dir: Path,
    project_dir: Path,
    task_id: str,
    mode: str,
    force_write_log: bool,
    force_create_followups: bool,
) -> int:
    plugin_bin = wg_dir / plugin
    if not plugin_bin.exists():
        return 0
    if not _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence=plugin):
        return 0

    write_log, create_followups = _mode_flags(mode=mode, plugin=plugin)
    write_log = write_log or force_write_log
    create_followups = create_followups or force_create_followups
    cmd = _plugin_cmd(
        plugin=plugin,
        plugin_bin=plugin_bin,
        project_dir=project_dir,
        task_id=task_id,
        want_json=False,
        write_log=write_log,
        create_followups=create_followups,
    )
    rc = int(_run(cmd))
    if rc in (ExitCode.ok, ExitCode.findings):
        return rc
    print(f"note: {plugin} failed (exit {rc}); continuing", file=sys.stderr)
    return 0


def _mode_flags(*, mode: str, plugin: str) -> tuple[bool, bool]:
    """
    Returns (write_log, create_followups) for a plugin under the policy mode.
    """

    m = str(mode or "redirect").strip().lower()
    if m == "observe":
        return (False, False)
    if m == "advise":
        return (True, False)
    if m == "redirect":
        return (True, True)
    if m == "heal":
        if plugin == "therapydrift":
            return (True, True)
        return (True, False)
    if m == "breaker":
        return (True, False)
    return (True, True)


def _ensure_breaker_task(*, wg_dir: Path, task_id: str) -> str:
    """
    Create deterministic breaker escalation task if missing.
    Returns the task id.
    """

    breaker_id = f"drift-breaker-{task_id}"
    try:
        subprocess.check_output(
            ["wg", "--dir", str(wg_dir), "show", breaker_id, "--json"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return breaker_id
    except Exception:
        pass

    ts = datetime.now(timezone.utc).isoformat()
    desc = (
        "Circuit-breaker escalation for repeated drift.\n\n"
        f"Origin task: {task_id}\n"
        f"Triggered at: {ts}\n\n"
        "Run a bounded recovery pass:\n"
        "- review open drift follow-ups\n"
        "- tighten wg-contract touch scope\n"
        "- close or merge stale remediation tasks\n"
        "- re-run `./.workgraph/drifts check --task "
        + task_id
        + " --write-log --create-followups`\n"
    )
    subprocess.check_call(
        [
            "wg",
            "--dir",
            str(wg_dir),
            "add",
            f"breaker: {task_id}",
            "--id",
            breaker_id,
            "--blocked-by",
            task_id,
            "-d",
            desc,
            "-t",
            "drift",
            "-t",
            "breaker",
        ]
    )
    return breaker_id


def cmd_install(args: argparse.Namespace) -> int:
    project_dir = Path.cwd()
    if args.dir:
        project_dir = Path(args.dir)
        if project_dir.name == ".workgraph":
            project_dir = project_dir.parent

    _ensure_wg_init(project_dir)

    wg_dir = find_workgraph_dir(project_dir)

    wrapper_mode = str(getattr(args, "wrapper_mode", "auto") or "auto").strip().lower()
    if wrapper_mode not in ("auto", "pinned", "portable"):
        print("error: --wrapper-mode must be one of: auto, pinned, portable", file=sys.stderr)
        return ExitCode.usage

    # Resolve tool bins.
    repo_root = Path(__file__).resolve().parents[1]
    driver_bin = resolve_bin(
        explicit=None,
        env_var="DRIFTDRIVER_BIN",
        which_name="driftdriver",
        candidates=[repo_root / "bin" / "driftdriver"],
    )
    if driver_bin is None:
        print("error: could not find driftdriver; set $DRIFTDRIVER_BIN", file=sys.stderr)
        return ExitCode.usage

    speedrift_bin = resolve_bin(
        explicit=Path(args.speedrift_bin) if args.speedrift_bin else None,
        env_var="SPEEDRIFT_BIN",
        which_name="speedrift",
        candidates=[
            repo_root.parent / "speedrift" / "bin" / "speedrift",
        ],
    )
    if speedrift_bin is None:
        print("error: could not find speedrift; pass --speedrift-bin or set $SPEEDRIFT_BIN", file=sys.stderr)
        return ExitCode.usage

    specdrift_bin = resolve_bin(
        explicit=Path(args.specdrift_bin) if args.specdrift_bin else None,
        env_var="SPECDRIFT_BIN",
        which_name="specdrift",
        candidates=[
            repo_root.parent / "specdrift" / "bin" / "specdrift",
        ],
    )

    include_uxdrift = bool(args.with_uxdrift or args.uxdrift_bin)
    uxdrift_bin = resolve_bin(
        explicit=Path(args.uxdrift_bin) if args.uxdrift_bin else None,
        env_var="UXDRIFT_BIN",
        which_name="uxdrift",
        candidates=[
            repo_root.parent / "uxdrift" / "bin" / "uxdrift",
        ],
    )
    if include_uxdrift and uxdrift_bin is None:
        # Best-effort: don't fail install.
        include_uxdrift = False

    include_therapydrift = bool(args.with_therapydrift or args.therapydrift_bin)
    therapydrift_bin = resolve_bin(
        explicit=Path(args.therapydrift_bin) if args.therapydrift_bin else None,
        env_var="THERAPYDRIFT_BIN",
        which_name="therapydrift",
        candidates=[
            repo_root.parent / "therapydrift" / "bin" / "therapydrift",
        ],
    )
    if include_therapydrift and therapydrift_bin is None:
        # Best-effort: don't fail install.
        include_therapydrift = False

    include_yagnidrift = bool(args.with_yagnidrift or args.yagnidrift_bin)
    yagnidrift_bin = resolve_bin(
        explicit=Path(args.yagnidrift_bin) if args.yagnidrift_bin else None,
        env_var="YAGNIDRIFT_BIN",
        which_name="yagnidrift",
        candidates=[
            repo_root.parent / "yagnidrift" / "bin" / "yagnidrift",
        ],
    )
    if include_yagnidrift and yagnidrift_bin is None:
        # Best-effort: don't fail install.
        include_yagnidrift = False

    include_redrift = bool(args.with_redrift or args.redrift_bin)
    redrift_bin = resolve_bin(
        explicit=Path(args.redrift_bin) if args.redrift_bin else None,
        env_var="REDRIFT_BIN",
        which_name="redrift",
        candidates=[
            repo_root.parent / "redrift" / "bin" / "redrift",
        ],
    )
    if include_redrift and redrift_bin is None:
        # Best-effort: don't fail install.
        include_redrift = False

    datadrift_bin = resolve_bin(
        explicit=Path(args.datadrift_bin) if args.datadrift_bin else None,
        env_var="DATADRIFT_BIN",
        which_name="datadrift",
        candidates=[
            repo_root.parent / "datadrift" / "bin" / "datadrift",
        ],
    )

    depsdrift_bin = resolve_bin(
        explicit=Path(args.depsdrift_bin) if args.depsdrift_bin else None,
        env_var="DEPSDRIFT_BIN",
        which_name="depsdrift",
        candidates=[
            repo_root.parent / "depsdrift" / "bin" / "depsdrift",
        ],
    )

    if wrapper_mode == "auto":
        # Choose portable only when the core tools are installed on PATH.
        wrapper_mode = "portable" if (shutil.which("driftdriver") and shutil.which("speedrift")) else "pinned"

    if wrapper_mode == "portable":
        if not shutil.which("driftdriver"):
            print("error: --wrapper-mode portable requires driftdriver on PATH", file=sys.stderr)
            return ExitCode.usage
        if not shutil.which("speedrift"):
            print("error: --wrapper-mode portable requires speedrift on PATH", file=sys.stderr)
            return ExitCode.usage

    wrote_driver = write_driver_wrapper(wg_dir, driver_bin=driver_bin, wrapper_mode=wrapper_mode)
    wrote_drifts = write_drifts_wrapper(wg_dir)
    wrote_speedrift = write_speedrift_wrapper(wg_dir, speedrift_bin=speedrift_bin, wrapper_mode=wrapper_mode)
    wrote_specdrift = False
    if specdrift_bin is not None:
        wrote_specdrift = write_specdrift_wrapper(wg_dir, specdrift_bin=specdrift_bin, wrapper_mode=wrapper_mode)
    wrote_datadrift = False
    if datadrift_bin is not None:
        wrote_datadrift = write_datadrift_wrapper(wg_dir, datadrift_bin=datadrift_bin, wrapper_mode=wrapper_mode)
    wrote_depsdrift = False
    if depsdrift_bin is not None:
        wrote_depsdrift = write_depsdrift_wrapper(wg_dir, depsdrift_bin=depsdrift_bin, wrapper_mode=wrapper_mode)
    wrote_uxdrift = False
    if include_uxdrift and uxdrift_bin is not None:
        wrote_uxdrift = write_uxdrift_wrapper(wg_dir, uxdrift_bin=uxdrift_bin, wrapper_mode=wrapper_mode)
    wrote_therapydrift = False
    if include_therapydrift and therapydrift_bin is not None:
        wrote_therapydrift = write_therapydrift_wrapper(
            wg_dir,
            therapydrift_bin=therapydrift_bin,
            wrapper_mode=wrapper_mode,
        )
    wrote_yagnidrift = False
    if include_yagnidrift and yagnidrift_bin is not None:
        wrote_yagnidrift = write_yagnidrift_wrapper(
            wg_dir,
            yagnidrift_bin=yagnidrift_bin,
            wrapper_mode=wrapper_mode,
        )
    wrote_redrift = False
    if include_redrift and redrift_bin is not None:
        wrote_redrift = write_redrift_wrapper(
            wg_dir,
            redrift_bin=redrift_bin,
            wrapper_mode=wrapper_mode,
        )

    updated_gitignore = ensure_speedrift_gitignore(wg_dir)
    if specdrift_bin is not None:
        updated_gitignore = ensure_specdrift_gitignore(wg_dir) or updated_gitignore
    if datadrift_bin is not None:
        updated_gitignore = ensure_datadrift_gitignore(wg_dir) or updated_gitignore
    if depsdrift_bin is not None:
        updated_gitignore = ensure_depsdrift_gitignore(wg_dir) or updated_gitignore
    if include_uxdrift:
        updated_gitignore = ensure_uxdrift_gitignore(wg_dir) or updated_gitignore
    if include_therapydrift:
        updated_gitignore = ensure_therapydrift_gitignore(wg_dir) or updated_gitignore
    if include_yagnidrift:
        updated_gitignore = ensure_yagnidrift_gitignore(wg_dir) or updated_gitignore
    if include_redrift:
        updated_gitignore = ensure_redrift_gitignore(wg_dir) or updated_gitignore

    created_executor, patched_executors = ensure_executor_guidance(
        wg_dir,
        include_uxdrift=include_uxdrift,
        include_therapydrift=include_therapydrift,
        include_yagnidrift=include_yagnidrift,
        include_redrift=include_redrift,
    )
    wrote_policy = ensure_drift_policy(wg_dir)

    ensured_contracts = False
    if not args.no_ensure_contracts:
        # Delegate to speedrift, since it owns the wg-contract format and defaults.
        subprocess.check_call([str(wg_dir / "speedrift"), "--dir", str(project_dir), "ensure-contracts", "--apply"])
        ensured_contracts = True

    result = InstallResult(
        wrote_drifts=wrote_drifts,
        wrote_driver=wrote_driver,
        wrote_speedrift=wrote_speedrift,
        wrote_specdrift=wrote_specdrift,
        wrote_datadrift=wrote_datadrift,
        wrote_depsdrift=wrote_depsdrift,
        wrote_uxdrift=wrote_uxdrift,
        wrote_therapydrift=wrote_therapydrift,
        wrote_yagnidrift=wrote_yagnidrift,
        wrote_redrift=wrote_redrift,
        wrote_policy=wrote_policy,
        updated_gitignore=updated_gitignore,
        created_executor=created_executor,
        patched_executors=patched_executors,
        ensured_contracts=ensured_contracts,
    )
    if args.json:
        import json

        print(json.dumps(asdict(result), indent=2, sort_keys=False))
    else:
        msg = f"Installed Driftdriver into {wg_dir}"
        enabled: list[str] = []
        if include_uxdrift:
            enabled.append("uxdrift")
        if include_therapydrift:
            enabled.append("therapydrift")
        if include_yagnidrift:
            enabled.append("yagnidrift")
        if include_redrift:
            enabled.append("redrift")
        if enabled:
            msg += f" (with {', '.join(enabled)})"
        print(msg)

    return ExitCode.ok


def cmd_check(args: argparse.Namespace) -> int:
    if not args.task:
        print("error: --task is required", file=sys.stderr)
        return ExitCode.usage

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent
    task_id = str(args.task)
    policy = load_drift_policy(wg_dir)
    mode = policy.mode
    ordered_plugins = _ordered_optional_plugins(policy.order)
    force_write_log = bool(args.write_log)
    force_create_followups = bool(args.create_followups)

    speedrift = wg_dir / "speedrift"
    if not speedrift.exists():
        print("error: .workgraph/speedrift not found; run driftdriver install first", file=sys.stderr)
        return ExitCode.usage

    speed_cmd = [str(speedrift), "--dir", str(project_dir), "check", "--task", task_id]
    speed_write_log, speed_followups = _mode_flags(mode=mode, plugin="speedrift")
    speed_write_log = speed_write_log or force_write_log
    speed_followups = speed_followups or force_create_followups
    if speed_write_log:
        speed_cmd.append("--write-log")
    if speed_followups:
        speed_cmd.append("--create-followups")
    if args.json:
        # JSON mode: capture sub-tool outputs and emit a single combined JSON object.
        speed_cmd.append("--json")
        speed_proc = subprocess.run(speed_cmd, text=True, capture_output=True)
        speed_rc = int(speed_proc.returncode)
        if speed_rc not in (0, ExitCode.findings):
            sys.stderr.write(speed_proc.stderr or "")
            return speed_rc
        try:
            speed_report = json.loads(speed_proc.stdout or "{}")
        except Exception:
            speed_report = {"raw": speed_proc.stdout}

        plugin_results: dict[str, dict[str, Any]] = {}
        rc_by_plugin: dict[str, int] = {"speedrift": speed_rc}
        for plugin in ordered_plugins:
            result = _run_optional_plugin_json(
                plugin=plugin,
                wg_dir=wg_dir,
                project_dir=project_dir,
                task_id=task_id,
                mode=mode,
                force_write_log=force_write_log,
                force_create_followups=force_create_followups,
            )
            plugin_results[plugin] = result
            rc_by_plugin[plugin] = int(result.get("exit_code", 0))

        out_rc = (
            ExitCode.findings
            if any(rc == ExitCode.findings for rc in rc_by_plugin.values())
            else ExitCode.ok
        )
        plugins_json: dict[str, Any] = {
            "speedrift": {"ran": True, "exit_code": speed_rc, "report": speed_report},
        }
        for plugin in OPTIONAL_PLUGINS:
            result = plugin_results.get(plugin, {"ran": False, "exit_code": 0, "report": None})
            if plugin == "uxdrift":
                plugins_json[plugin] = {
                    "ran": bool(result.get("ran")),
                    "exit_code": int(result.get("exit_code", 0)),
                    "note": "no standardized json output yet",
                }
            else:
                plugins_json[plugin] = {
                    "ran": bool(result.get("ran")),
                    "exit_code": int(result.get("exit_code", 0)),
                    "report": result.get("report"),
                }

        combined = {
            "task_id": task_id,
            "exit_code": out_rc,
            "mode": mode,
            "policy_order": ordered_plugins,
            "plugins": plugins_json,
        }
        if mode == "breaker" and out_rc == ExitCode.findings:
            breaker_id = _ensure_breaker_task(wg_dir=wg_dir, task_id=task_id)
            combined["breaker_task_id"] = breaker_id
        print(json.dumps(combined, indent=2, sort_keys=False))
        return out_rc

    speed_rc = _run(speed_cmd)
    if speed_rc not in (0, ExitCode.findings):
        return speed_rc

    rc_by_plugin: dict[str, int] = {"speedrift": speed_rc}
    for plugin in ordered_plugins:
        rc_by_plugin[plugin] = _run_optional_plugin_text(
            plugin=plugin,
            wg_dir=wg_dir,
            project_dir=project_dir,
            task_id=task_id,
            mode=mode,
            force_write_log=force_write_log,
            force_create_followups=force_create_followups,
        )

    if any(rc == ExitCode.findings for rc in rc_by_plugin.values()):
        if mode == "breaker":
            _ensure_breaker_task(wg_dir=wg_dir, task_id=task_id)
        return ExitCode.findings
    return ExitCode.ok


def cmd_orchestrate(args: argparse.Namespace) -> int:
    """
    Run drift "pit wall" loops.

    Today this delegates to baseline speedrift's monitor+redirect orchestrator.
    """

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent

    speedrift = wg_dir / "speedrift"
    if not speedrift.exists():
        print("error: .workgraph/speedrift not found; run driftdriver install first", file=sys.stderr)
        return ExitCode.usage

    cmd = [
        str(speedrift),
        "--dir",
        str(project_dir),
        "orchestrate",
        "--interval",
        str(int(args.interval)),
        "--redirect-interval",
        str(int(args.redirect_interval)),
    ]
    if args.write_log:
        cmd.append("--write-log")
    if args.create_followups:
        cmd.append("--create-followups")

    return int(_run(cmd))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="driftdriver")
    p.add_argument("--dir", help="Project directory (or .workgraph dir). Defaults to cwd search.")
    p.add_argument("--json", action="store_true", help="JSON output (where supported)")

    sub = p.add_subparsers(dest="cmd", required=True)

    install = sub.add_parser("install", help="Install Driftdriver into a workgraph repo")
    install.add_argument("--speedrift-bin", help="Path to speedrift bin/speedrift (required if not discoverable)")
    install.add_argument("--specdrift-bin", help="Path to specdrift bin/specdrift (optional)")
    install.add_argument("--datadrift-bin", help="Path to datadrift bin/datadrift (optional)")
    install.add_argument("--depsdrift-bin", help="Path to depsdrift bin/depsdrift (optional)")
    install.add_argument("--with-uxdrift", action="store_true", help="Best-effort: enable uxdrift integration if found")
    install.add_argument("--uxdrift-bin", help="Path to uxdrift bin/uxdrift (enables uxdrift integration)")
    install.add_argument(
        "--with-therapydrift",
        action="store_true",
        help="Best-effort: enable therapydrift integration if found",
    )
    install.add_argument("--therapydrift-bin", help="Path to therapydrift bin/therapydrift (enables therapydrift integration)")
    install.add_argument(
        "--with-yagnidrift",
        action="store_true",
        help="Best-effort: enable yagnidrift integration if found",
    )
    install.add_argument("--yagnidrift-bin", help="Path to yagnidrift bin/yagnidrift (enables yagnidrift integration)")
    install.add_argument(
        "--with-redrift",
        action="store_true",
        help="Best-effort: enable redrift integration if found",
    )
    install.add_argument("--redrift-bin", help="Path to redrift bin/redrift (enables redrift integration)")
    install.add_argument(
        "--wrapper-mode",
        choices=["auto", "pinned", "portable"],
        default="auto",
        help="Wrapper style: pinned paths (dev) or portable PATH-based (commit-safe). Default: auto.",
    )
    install.add_argument("--no-ensure-contracts", action="store_true", help="Do not inject default contracts into tasks")
    install.set_defaults(func=cmd_install)

    check = sub.add_parser(
        "check",
        help="Unified check (speedrift always; optional drifts run when task declares fenced specs)",
    )
    check.add_argument("--task", help="Task id to check")
    check.add_argument("--write-log", action="store_true", help="Write summary into wg log")
    check.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for findings")
    check.set_defaults(func=cmd_check)

    orch = sub.add_parser("orchestrate", help="Run continuous drift monitor+redirect loops (delegates to speedrift)")
    orch.add_argument("--interval", default=30, help="Monitor poll interval seconds (default: 30)")
    orch.add_argument("--redirect-interval", default=5, help="Redirect poll interval seconds (default: 5)")
    orch.add_argument("--write-log", action="store_true", help="Write a drift summary to wg log (redirect agent)")
    orch.add_argument("--create-followups", action="store_true", help="Create follow-up tasks (redirect agent)")
    orch.set_defaults(func=cmd_orchestrate)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
