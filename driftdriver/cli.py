from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import shutil
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Any

from driftdriver.install import (
    InstallResult,
    ensure_archdrift_gitignore,
    ensure_executor_guidance,
    ensure_datadrift_gitignore,
    ensure_depsdrift_gitignore,
    ensure_redrift_gitignore,
    ensure_specdrift_gitignore,
    ensure_coredrift_gitignore,
    ensure_therapydrift_gitignore,
    ensure_uxdrift_gitignore,
    ensure_yagnidrift_gitignore,
    resolve_bin,
    write_archdrift_wrapper,
    write_datadrift_wrapper,
    write_depsdrift_wrapper,
    write_drifts_wrapper,
    write_driver_wrapper,
    write_redrift_wrapper,
    write_specdrift_wrapper,
    write_coredrift_wrapper,
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
    "archdrift",
    "depsdrift",
    "uxdrift",
    "therapydrift",
    "yagnidrift",
    "redrift",
]

LANE_STRATEGIES = ("auto", "fences", "all")
FULL_SUITE_TRIGGER_FENCES = {"redrift"}
FULL_SUITE_TRIGGER_PHRASES = (
    "full suite",
    "all lanes",
    "all drifts",
    "all tools",
    "run every drift",
    "complex app",
    "complex application",
    "app redo",
    "data redo",
)
COMPLEXITY_KEYWORDS = (
    "rewrite",
    "rebuild",
    "migration",
    "respec",
    "architecture",
    "frontend",
    "backend",
    "full-stack",
    "full stack",
    "schema",
    "database",
    "ux",
    "multi-agent",
)


def _run(cmd: list[str]) -> int:
    return subprocess.call(cmd)


def _ensure_wg_init(project_dir: Path) -> None:
    wg_dir = project_dir / ".workgraph"
    if (wg_dir / "graph.jsonl").exists():
        return
    subprocess.check_call(["wg", "init"], cwd=str(project_dir))


def _load_task(*, wg_dir: Path, task_id: str) -> dict[str, Any] | None:
    wg = load_workgraph(wg_dir)
    return wg.tasks.get(task_id)


def _task_has_fence(*, task: dict[str, Any] | None, fence: str) -> bool:
    if not task:
        return False
    desc = str(task.get("description") or "")
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


def _extract_contract_int(*, description: str, key: str) -> int | None:
    m = re.search(rf"(?m)^\s*{re.escape(key)}\s*=\s*(\d+)\b", description)
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def _task_text(task: dict[str, Any] | None) -> str:
    if not task:
        return ""
    title = str(task.get("title") or "")
    desc = str(task.get("description") or "")
    tags = task.get("tags")
    tags_text = ""
    if isinstance(tags, list):
        tags_text = " ".join(str(t) for t in tags)
    return f"{title}\n{tags_text}\n{desc}".lower()


def _should_run_full_suite(*, task: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not task:
        return (False, [])

    reasons: list[str] = []
    desc = str(task.get("description") or "")
    text = _task_text(task)

    for fence in sorted(FULL_SUITE_TRIGGER_FENCES):
        if _task_has_fence(task=task, fence=fence):
            reasons.append(f"{fence} fence declared")

    phrase_hits = [p for p in FULL_SUITE_TRIGGER_PHRASES if p in text]
    if phrase_hits:
        reasons.append(f"explicit full-suite intent ({', '.join(phrase_hits[:3])})")

    complexity_points = 0
    blocked_by = task.get("blocked_by")
    if isinstance(blocked_by, list) and len(blocked_by) >= 3:
        complexity_points += 1
        reasons.append(f"{len(blocked_by)} upstream dependencies")

    max_files = _extract_contract_int(description=desc, key="max_files")
    if max_files is not None and max_files >= 30:
        complexity_points += 1
        reasons.append(f"wg-contract max_files={max_files}")

    max_loc = _extract_contract_int(description=desc, key="max_loc")
    if max_loc is not None and max_loc >= 1000:
        complexity_points += 1
        reasons.append(f"wg-contract max_loc={max_loc}")

    keyword_hits = [kw for kw in COMPLEXITY_KEYWORDS if kw in text]
    if len(keyword_hits) >= 2:
        complexity_points += 1
        reasons.append(f"complexity keywords ({', '.join(keyword_hits[:3])})")

    if phrase_hits:
        return (True, reasons)
    if any(_task_has_fence(task=task, fence=f) for f in FULL_SUITE_TRIGGER_FENCES):
        return (True, reasons)
    if complexity_points >= 2:
        return (True, reasons)
    return (False, [])


def _select_optional_plugins(
    *,
    task: dict[str, Any] | None,
    ordered_plugins: list[str],
    lane_strategy: str,
) -> tuple[set[str], dict[str, Any]]:
    strategy = str(lane_strategy or "auto").strip().lower()
    if strategy not in LANE_STRATEGIES:
        strategy = "auto"

    selected: set[str] = set()
    plugin_reasons: dict[str, str] = {}
    for plugin in ordered_plugins:
        if _task_has_fence(task=task, fence=plugin):
            selected.add(plugin)
            plugin_reasons[plugin] = "task fence"

    full_suite = False
    full_suite_reasons: list[str] = []
    if strategy == "all":
        full_suite = True
        full_suite_reasons = ["lane strategy forced all optional plugins"]
    elif strategy == "auto":
        full_suite, full_suite_reasons = _should_run_full_suite(task=task)

    if full_suite:
        for plugin in ordered_plugins:
            if plugin in selected:
                plugin_reasons[plugin] = f"{plugin_reasons[plugin]} + preflight full-suite"
            else:
                plugin_reasons[plugin] = "preflight full-suite"
            selected.add(plugin)

    lane_plan = {
        "strategy": strategy,
        "full_suite": full_suite,
        "reasons": list(full_suite_reasons),
        "selected_plugins": [p for p in ordered_plugins if p in selected],
        "plugin_reasons": {
            p: plugin_reasons.get(p, "not selected")
            for p in OPTIONAL_PLUGINS
        },
    }
    return (selected, lane_plan)


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
    enabled: bool,
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
    if not enabled:
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
    enabled: bool,
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
    if not enabled:
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

    coredrift_bin = resolve_bin(
        explicit=Path(args.coredrift_bin) if args.coredrift_bin else None,
        env_var="COREDRIFT_BIN",
        which_name="coredrift",
        candidates=[
            repo_root.parent / "coredrift" / "bin" / "coredrift",
        ],
    )
    if coredrift_bin is None:
        print("error: could not find coredrift; pass --coredrift-bin or set $COREDRIFT_BIN", file=sys.stderr)
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

    archdrift_bin = resolve_bin(
        explicit=Path(args.archdrift_bin) if args.archdrift_bin else None,
        env_var="ARCHDRIFT_BIN",
        which_name="archdrift",
        candidates=[
            repo_root.parent / "archdrift" / "bin" / "archdrift",
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
        wrapper_mode = "portable" if (shutil.which("driftdriver") and shutil.which("coredrift")) else "pinned"

    if wrapper_mode == "portable":
        if not shutil.which("driftdriver"):
            print("error: --wrapper-mode portable requires driftdriver on PATH", file=sys.stderr)
            return ExitCode.usage
        if not shutil.which("coredrift"):
            print("error: --wrapper-mode portable requires coredrift on PATH", file=sys.stderr)
            return ExitCode.usage

    wrote_driver = write_driver_wrapper(wg_dir, driver_bin=driver_bin, wrapper_mode=wrapper_mode)
    wrote_drifts = write_drifts_wrapper(wg_dir)
    wrote_coredrift = write_coredrift_wrapper(wg_dir, coredrift_bin=coredrift_bin, wrapper_mode=wrapper_mode)
    wrote_specdrift = False
    if specdrift_bin is not None:
        wrote_specdrift = write_specdrift_wrapper(wg_dir, specdrift_bin=specdrift_bin, wrapper_mode=wrapper_mode)
    wrote_datadrift = False
    if datadrift_bin is not None:
        wrote_datadrift = write_datadrift_wrapper(wg_dir, datadrift_bin=datadrift_bin, wrapper_mode=wrapper_mode)
    wrote_archdrift = False
    if archdrift_bin is not None:
        wrote_archdrift = write_archdrift_wrapper(wg_dir, archdrift_bin=archdrift_bin, wrapper_mode=wrapper_mode)
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

    updated_gitignore = ensure_coredrift_gitignore(wg_dir)
    if specdrift_bin is not None:
        updated_gitignore = ensure_specdrift_gitignore(wg_dir) or updated_gitignore
    if datadrift_bin is not None:
        updated_gitignore = ensure_datadrift_gitignore(wg_dir) or updated_gitignore
    if archdrift_bin is not None:
        updated_gitignore = ensure_archdrift_gitignore(wg_dir) or updated_gitignore
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
        include_archdrift=bool(archdrift_bin),
        include_uxdrift=include_uxdrift,
        include_therapydrift=include_therapydrift,
        include_yagnidrift=include_yagnidrift,
        include_redrift=include_redrift,
    )
    wrote_policy = ensure_drift_policy(wg_dir)

    ensured_contracts = False
    if not args.no_ensure_contracts:
        # Delegate to coredrift, since it owns the wg-contract format and defaults.
        subprocess.check_call([str(wg_dir / "coredrift"), "--dir", str(project_dir), "ensure-contracts", "--apply"])
        ensured_contracts = True

    result = InstallResult(
        wrote_drifts=wrote_drifts,
        wrote_driver=wrote_driver,
        wrote_coredrift=wrote_coredrift,
        wrote_specdrift=wrote_specdrift,
        wrote_datadrift=wrote_datadrift,
        wrote_archdrift=wrote_archdrift,
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
    task = _load_task(wg_dir=wg_dir, task_id=task_id)
    selected_plugins, lane_plan = _select_optional_plugins(
        task=task,
        ordered_plugins=ordered_plugins,
        lane_strategy=getattr(args, "lane_strategy", "auto"),
    )
    force_write_log = bool(args.write_log)
    force_create_followups = bool(args.create_followups)

    coredrift = wg_dir / "coredrift"
    if not coredrift.exists():
        print("error: .workgraph/coredrift not found; run driftdriver install first", file=sys.stderr)
        return ExitCode.usage

    speed_cmd = [str(coredrift), "--dir", str(project_dir), "check", "--task", task_id]
    speed_write_log, speed_followups = _mode_flags(mode=mode, plugin="coredrift")
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
        rc_by_plugin: dict[str, int] = {"coredrift": speed_rc}
        for plugin in ordered_plugins:
            result = _run_optional_plugin_json(
                plugin=plugin,
                enabled=(plugin in selected_plugins),
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
            "coredrift": {"ran": True, "exit_code": speed_rc, "report": speed_report},
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
            "lane_strategy": lane_plan["strategy"],
            "lane_plan": lane_plan,
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

    if lane_plan["full_suite"]:
        reason_text = ", ".join(str(r) for r in lane_plan["reasons"]) or "preflight criteria matched"
        print(f"note: lane preflight selected full suite ({reason_text})", file=sys.stderr)

    rc_by_plugin: dict[str, int] = {"coredrift": speed_rc}
    for plugin in ordered_plugins:
        rc_by_plugin[plugin] = _run_optional_plugin_text(
            plugin=plugin,
            enabled=(plugin in selected_plugins),
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

    Today this delegates to baseline coredrift's monitor+redirect orchestrator.
    """

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent

    coredrift = wg_dir / "coredrift"
    if not coredrift.exists():
        print("error: .workgraph/coredrift not found; run driftdriver install first", file=sys.stderr)
        return ExitCode.usage

    cmd = [
        str(coredrift),
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
    install.add_argument("--coredrift-bin", help="Path to coredrift bin/coredrift (required if not discoverable)")
    install.add_argument("--specdrift-bin", help="Path to specdrift bin/specdrift (optional)")
    install.add_argument("--datadrift-bin", help="Path to datadrift bin/datadrift (optional)")
    install.add_argument("--archdrift-bin", help="Path to archdrift bin/archdrift (optional)")
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
        help="Unified check (coredrift always; optional drifts selected by lane strategy)",
    )
    check.add_argument("--task", help="Task id to check")
    check.add_argument(
        "--lane-strategy",
        choices=LANE_STRATEGIES,
        default="auto",
        help="Optional lane routing: auto (default), fences, or all.",
    )
    check.add_argument("--write-log", action="store_true", help="Write summary into wg log")
    check.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for findings")
    check.set_defaults(func=cmd_check)

    orch = sub.add_parser("orchestrate", help="Run continuous drift monitor+redirect loops (delegates to coredrift)")
    orch.add_argument("--interval", default=30, help="Monitor poll interval seconds (default: 30)")
    orch.add_argument("--redirect-interval", default=5, help="Redirect poll interval seconds (default: 5)")
    orch.add_argument("--write-log", action="store_true", help="Write a drift summary to wg log (redirect agent)")
    orch.add_argument("--create-followups", action="store_true", help="Create follow-up tasks (redirect agent)")
    orch.set_defaults(func=cmd_orchestrate)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
