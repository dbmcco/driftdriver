from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from driftdriver.install import (
    InstallResult,
    ensure_executor_guidance,
    ensure_speedrift_gitignore,
    ensure_uxrift_gitignore,
    resolve_bin,
    write_driver_wrapper,
    write_rifts_wrapper,
    write_speedrift_wrapper,
    write_uxrift_wrapper,
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

    # Resolve tool bins.
    repo_root = Path(__file__).resolve().parents[1]
    driver_bin = repo_root / "bin" / "driftdriver"

    speedrift_bin = resolve_bin(
        explicit=Path(args.speedrift_bin) if args.speedrift_bin else None,
        env_var="SPEEDRIFT_BIN",
        which_name="speedrift",
        candidates=[
            repo_root.parent / "speedrift" / "bin" / "speedrift",
            Path("/Users/braydon/projects/experiments/speedrift/bin/speedrift"),
        ],
    )
    if speedrift_bin is None:
        print("error: could not find speedrift; pass --speedrift-bin or set $SPEEDRIFT_BIN", file=sys.stderr)
        return ExitCode.usage

    include_uxrift = bool(args.with_uxrift or args.uxrift_bin)
    uxrift_bin = resolve_bin(
        explicit=Path(args.uxrift_bin) if args.uxrift_bin else None,
        env_var="UXRIFT_BIN",
        which_name="uxrift",
        candidates=[
            repo_root.parent / "uxrift" / "bin" / "uxrift",
            Path("/Users/braydon/projects/experiments/uxrift/bin/uxrift"),
        ],
    )
    if include_uxrift and uxrift_bin is None:
        # Best-effort: don't fail install.
        include_uxrift = False

    wrote_driver = write_driver_wrapper(wg_dir, driver_bin=driver_bin)
    wrote_rifts = write_rifts_wrapper(wg_dir)
    wrote_speedrift = write_speedrift_wrapper(wg_dir, speedrift_bin=speedrift_bin)
    wrote_uxrift = False
    if include_uxrift and uxrift_bin is not None:
        wrote_uxrift = write_uxrift_wrapper(wg_dir, uxrift_bin=uxrift_bin)

    updated_gitignore = ensure_speedrift_gitignore(wg_dir)
    if include_uxrift:
        updated_gitignore = ensure_uxrift_gitignore(wg_dir) or updated_gitignore

    created_executor, patched_executors = ensure_executor_guidance(wg_dir, include_uxrift=include_uxrift)

    ensured_contracts = False
    if not args.no_ensure_contracts:
        # Delegate to speedrift, since it owns the wg-contract format and defaults.
        subprocess.check_call([str(wg_dir / "speedrift"), "--dir", str(project_dir), "ensure-contracts", "--apply"])
        ensured_contracts = True

    result = InstallResult(
        wrote_rifts=wrote_rifts,
        wrote_driver=wrote_driver,
        wrote_speedrift=wrote_speedrift,
        wrote_uxrift=wrote_uxrift,
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
        if include_uxrift:
            msg += " (with uxrift)"
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
        speed_cmd.append("--json")

    speed_rc = _run(speed_cmd)
    if speed_rc not in (0, ExitCode.findings):
        return speed_rc

    ux_rc = 0
    uxrift = wg_dir / "uxrift"
    if uxrift.exists() and _task_has_fence(wg_dir=wg_dir, task_id=task_id, fence="uxrift"):
        ux_cmd = [str(uxrift), "wg", "--dir", str(project_dir), "check", "--task", task_id]
        if args.write_log:
            ux_cmd.append("--write-log")
        if args.create_followups:
            ux_cmd.append("--create-followups")
        # uxrift json output isn't standardized yet; keep best-effort.
        ux_rc = _run(ux_cmd)
        if ux_rc not in (0, ExitCode.findings):
            print(f"note: uxrift failed (exit {ux_rc}); continuing", file=sys.stderr)
            ux_rc = 0

    if speed_rc == ExitCode.findings or ux_rc == ExitCode.findings:
        return ExitCode.findings
    return ExitCode.ok


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="driftdriver")
    p.add_argument("--dir", help="Project directory (or .workgraph dir). Defaults to cwd search.")
    p.add_argument("--json", action="store_true", help="JSON output (where supported)")

    sub = p.add_subparsers(dest="cmd", required=True)

    install = sub.add_parser("install", help="Install Driftdriver into a workgraph repo")
    install.add_argument("--speedrift-bin", help="Path to speedrift bin/speedrift (required if not discoverable)")
    install.add_argument("--with-uxrift", action="store_true", help="Best-effort: enable uxrift integration if found")
    install.add_argument("--uxrift-bin", help="Path to uxrift bin/uxrift (enables uxrift integration)")
    install.add_argument("--no-ensure-contracts", action="store_true", help="Do not inject default contracts into tasks")
    install.set_defaults(func=cmd_install)

    check = sub.add_parser("check", help="Unified check (speedrift always, uxrift best-effort)")
    check.add_argument("--task", help="Task id to check")
    check.add_argument("--write-log", action="store_true", help="Write summary into wg log")
    check.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for findings")
    check.set_defaults(func=cmd_check)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
