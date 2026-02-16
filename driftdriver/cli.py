from __future__ import annotations

import argparse
import json
import subprocess
import sys
import shutil
from dataclasses import asdict
from pathlib import Path

from driftdriver.install import (
    InstallResult,
    ensure_executor_guidance,
    ensure_datadrift_gitignore,
    ensure_depsdrift_gitignore,
    ensure_specdrift_gitignore,
    ensure_speedrift_gitignore,
    ensure_therapydrift_gitignore,
    ensure_uxdrift_gitignore,
    resolve_bin,
    write_datadrift_wrapper,
    write_depsdrift_wrapper,
    write_drifts_wrapper,
    write_driver_wrapper,
    write_specdrift_wrapper,
    write_speedrift_wrapper,
    write_therapydrift_wrapper,
    write_uxdrift_wrapper,
)
from driftdriver.workgraph import find_workgraph_dir, load_workgraph


class ExitCode:
    ok = 0
    findings = 3
    usage = 2


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

    created_executor, patched_executors = ensure_executor_guidance(
        wg_dir,
        include_uxdrift=include_uxdrift,
        include_therapydrift=include_therapydrift,
    )

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

    speedrift = wg_dir / "speedrift"
    if not speedrift.exists():
        print("error: .workgraph/speedrift not found; run driftdriver install first", file=sys.stderr)
        return ExitCode.usage

    speed_cmd = [str(speedrift), "--dir", str(project_dir), "check", "--task", task_id]
    if args.write_log:
        speed_cmd.append("--write-log")
    if args.create_followups:
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

        specdrift = wg_dir / "specdrift"
        spec_ran = False
        spec_rc = 0
        spec_report = None
        if specdrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="specdrift"):
            spec_ran = True
            spec_cmd = [str(specdrift), "--dir", str(project_dir), "--json", "wg", "check", "--task", task_id]
            if args.write_log:
                spec_cmd.append("--write-log")
            if args.create_followups:
                spec_cmd.append("--create-followups")
            spec_proc = subprocess.run(spec_cmd, text=True, capture_output=True)
            spec_rc = int(spec_proc.returncode)
            if spec_rc in (0, ExitCode.findings):
                try:
                    spec_report = json.loads(spec_proc.stdout or "{}")
                except Exception:
                    spec_report = {"raw": spec_proc.stdout}
            else:
                spec_report = {"error": "specdrift failed", "exit_code": spec_rc, "stderr": (spec_proc.stderr or "")[:4000]}
                # Best-effort: don't hard-fail the unified check on optional plugin errors.
                spec_rc = 0

        datadrift = wg_dir / "datadrift"
        data_ran = False
        data_rc = 0
        data_report = None
        if datadrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="datadrift"):
            data_ran = True
            data_cmd = [str(datadrift), "--dir", str(project_dir), "--json", "wg", "check", "--task", task_id]
            if args.write_log:
                data_cmd.append("--write-log")
            if args.create_followups:
                data_cmd.append("--create-followups")
            data_proc = subprocess.run(data_cmd, text=True, capture_output=True)
            data_rc = int(data_proc.returncode)
            if data_rc in (0, ExitCode.findings):
                try:
                    data_report = json.loads(data_proc.stdout or "{}")
                except Exception:
                    data_report = {"raw": data_proc.stdout}
            else:
                data_report = {
                    "error": "datadrift failed",
                    "exit_code": data_rc,
                    "stderr": (data_proc.stderr or "")[:4000],
                }
                data_rc = 0

        depsdrift = wg_dir / "depsdrift"
        deps_ran = False
        deps_rc = 0
        deps_report = None
        if depsdrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="depsdrift"):
            deps_ran = True
            deps_cmd = [str(depsdrift), "--dir", str(project_dir), "--json", "wg", "check", "--task", task_id]
            if args.write_log:
                deps_cmd.append("--write-log")
            if args.create_followups:
                deps_cmd.append("--create-followups")
            deps_proc = subprocess.run(deps_cmd, text=True, capture_output=True)
            deps_rc = int(deps_proc.returncode)
            if deps_rc in (0, ExitCode.findings):
                try:
                    deps_report = json.loads(deps_proc.stdout or "{}")
                except Exception:
                    deps_report = {"raw": deps_proc.stdout}
            else:
                deps_report = {
                    "error": "depsdrift failed",
                    "exit_code": deps_rc,
                    "stderr": (deps_proc.stderr or "")[:4000],
                }
                deps_rc = 0

        ux_ran = False
        ux_rc = 0
        uxdrift = wg_dir / "uxdrift"
        if uxdrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="uxdrift"):
            ux_ran = True
            ux_cmd = [str(uxdrift), "wg", "--dir", str(project_dir), "check", "--task", task_id]
            if args.write_log:
                ux_cmd.append("--write-log")
            if args.create_followups:
                ux_cmd.append("--create-followups")
            ux_proc = subprocess.run(ux_cmd, text=True, capture_output=True)
            ux_rc = int(ux_proc.returncode)
            if ux_rc not in (0, ExitCode.findings):
                ux_rc = 0

        therapy_ran = False
        therapy_rc = 0
        therapy_report = None
        therapydrift = wg_dir / "therapydrift"
        if therapydrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="therapydrift"):
            therapy_ran = True
            therapy_cmd = [str(therapydrift), "--dir", str(project_dir), "--json", "wg", "check", "--task", task_id]
            if args.write_log:
                therapy_cmd.append("--write-log")
            if args.create_followups:
                therapy_cmd.append("--create-followups")
            therapy_proc = subprocess.run(therapy_cmd, text=True, capture_output=True)
            therapy_rc = int(therapy_proc.returncode)
            if therapy_rc in (0, ExitCode.findings):
                try:
                    therapy_report = json.loads(therapy_proc.stdout or "{}")
                except Exception:
                    therapy_report = {"raw": therapy_proc.stdout}
            else:
                therapy_report = {
                    "error": "therapydrift failed",
                    "exit_code": therapy_rc,
                    "stderr": (therapy_proc.stderr or "")[:4000],
                }
                therapy_rc = 0

        out_rc = (
            ExitCode.findings
            if (
                speed_rc == ExitCode.findings
                or spec_rc == ExitCode.findings
                or data_rc == ExitCode.findings
                or deps_rc == ExitCode.findings
                or ux_rc == ExitCode.findings
                or therapy_rc == ExitCode.findings
            )
            else ExitCode.ok
        )
        combined = {
            "task_id": task_id,
            "exit_code": out_rc,
            "plugins": {
                "speedrift": {"ran": True, "exit_code": speed_rc, "report": speed_report},
                "specdrift": {"ran": spec_ran, "exit_code": spec_rc, "report": spec_report},
                "datadrift": {"ran": data_ran, "exit_code": data_rc, "report": data_report},
                "depsdrift": {"ran": deps_ran, "exit_code": deps_rc, "report": deps_report},
                "uxdrift": {"ran": ux_ran, "exit_code": ux_rc, "note": "no standardized json output yet"},
                "therapydrift": {"ran": therapy_ran, "exit_code": therapy_rc, "report": therapy_report},
            },
        }
        print(json.dumps(combined, indent=2, sort_keys=False))
        return out_rc

    speed_rc = _run(speed_cmd)
    if speed_rc not in (0, ExitCode.findings):
        return speed_rc

    spec_rc = 0
    specdrift = wg_dir / "specdrift"
    if specdrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="specdrift"):
        spec_cmd = [str(specdrift), "--dir", str(project_dir), "wg", "check", "--task", task_id]
        if args.write_log:
            spec_cmd.append("--write-log")
        if args.create_followups:
            spec_cmd.append("--create-followups")
        spec_rc = _run(spec_cmd)
        if spec_rc not in (0, ExitCode.findings):
            print(f"note: specdrift failed (exit {spec_rc}); continuing", file=sys.stderr)
            spec_rc = 0

    data_rc = 0
    datadrift = wg_dir / "datadrift"
    if datadrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="datadrift"):
        data_cmd = [str(datadrift), "--dir", str(project_dir), "wg", "check", "--task", task_id]
        if args.write_log:
            data_cmd.append("--write-log")
        if args.create_followups:
            data_cmd.append("--create-followups")
        data_rc = _run(data_cmd)
        if data_rc not in (0, ExitCode.findings):
            print(f"note: datadrift failed (exit {data_rc}); continuing", file=sys.stderr)
            data_rc = 0

    deps_rc = 0
    depsdrift = wg_dir / "depsdrift"
    if depsdrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="depsdrift"):
        deps_cmd = [str(depsdrift), "--dir", str(project_dir), "wg", "check", "--task", task_id]
        if args.write_log:
            deps_cmd.append("--write-log")
        if args.create_followups:
            deps_cmd.append("--create-followups")
        deps_rc = _run(deps_cmd)
        if deps_rc not in (0, ExitCode.findings):
            print(f"note: depsdrift failed (exit {deps_rc}); continuing", file=sys.stderr)
            deps_rc = 0

    ux_rc = 0
    uxdrift = wg_dir / "uxdrift"
    if uxdrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="uxdrift"):
        ux_cmd = [str(uxdrift), "wg", "--dir", str(project_dir), "check", "--task", task_id]
        if args.write_log:
            ux_cmd.append("--write-log")
        if args.create_followups:
            ux_cmd.append("--create-followups")
        ux_rc = _run(ux_cmd)
        if ux_rc not in (0, ExitCode.findings):
            print(f"note: uxdrift failed (exit {ux_rc}); continuing", file=sys.stderr)
            ux_rc = 0

    therapy_rc = 0
    therapydrift = wg_dir / "therapydrift"
    if therapydrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="therapydrift"):
        therapy_cmd = [str(therapydrift), "--dir", str(project_dir), "wg", "check", "--task", task_id]
        if args.write_log:
            therapy_cmd.append("--write-log")
        if args.create_followups:
            therapy_cmd.append("--create-followups")
        therapy_rc = _run(therapy_cmd)
        if therapy_rc not in (0, ExitCode.findings):
            print(f"note: therapydrift failed (exit {therapy_rc}); continuing", file=sys.stderr)
            therapy_rc = 0

    if (
        speed_rc == ExitCode.findings
        or spec_rc == ExitCode.findings
        or data_rc == ExitCode.findings
        or deps_rc == ExitCode.findings
        or ux_rc == ExitCode.findings
        or therapy_rc == ExitCode.findings
    ):
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
