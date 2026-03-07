# ABOUTME: Check subcommand logic for driftdriver CLI.
# ABOUTME: Lane routing, findings collection, enforcement, and cmd_check/cmd_updates.

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.health import (
    blockers_done,
    compute_scoreboard,
    detect_cycle_from,
    find_duplicate_open_drift_groups,
    has_contract,
    is_active,
    is_drift_task,
    normalize_drift_key,
    rank_ready_drift_queue,
    redrift_depth,
)
from driftdriver.policy import load_drift_policy
from driftdriver.routing_models import parse_routing_response
from driftdriver.smart_routing import gather_evidence
from driftdriver.updates import (
    ECOSYSTEM_REPOS,
    check_ecosystem_updates,
    load_review_config,
    render_review_markdown,
    summarize_updates,
)
from driftdriver.workgraph import find_workgraph_dir, load_workgraph

from ._helpers import (
    _collect_findings,
    _compute_loop_safety,
    _dedupe_strings,
    _ensure_update_followup_task,
    _maybe_auto_ensure_contracts,
    _normalize_actions,
    _parse_watch_repo,
    _parse_watch_report,
    _resolve_update_sources,
    _run_update_preflight,
    _update_errors,
    _wg_log_message,
    _wrapper_commands_available,
)


def _record_check_findings(
    *,
    plugins_json: dict[str, Any],
    task_id: str,
    project_dir: Path,
) -> None:
    """Record drift findings to lessons.db immediately. Non-blocking best-effort."""
    try:
        from driftdriver.reporting import record_event_immediate

        findings = _collect_findings(plugins_json)
        if not findings:
            return

        session_id = os.environ.get("WG_SESSION_ID", "")
        project = project_dir.name

        for lane, kind in findings:
            record_event_immediate(
                event_type="drift_finding",
                content=f"{lane}: {kind}",
                session_id=session_id,
                project=project,
                metadata={
                    "lane": lane,
                    "severity": kind,
                    "task_id": task_id,
                },
            )
    except Exception:
        pass


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
    "fixdrift",
    "yagnidrift",
    "redrift",
]

LANE_STRATEGIES = ("auto", "fences", "all", "smart")
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
    wg_dir: Path | None = None,
) -> tuple[set[str], dict[str, Any]]:
    strategy = str(lane_strategy or "auto").strip().lower()
    if strategy not in LANE_STRATEGIES:
        strategy = "auto"

    if strategy == "smart":
        if wg_dir is None:
            strategy = "auto"
        else:
            evidence = gather_evidence(wg_dir)
            # Smart routing: use evidence-based pattern matching
            # Model-mediated routing requires API integration (future work)
            decision = parse_routing_response("", evidence)
            selected = set(decision.selected_lanes)
            lane_plan = {
                "strategy": "smart",
                "full_suite": False,
                "reasons": ["smart routing via evidence package"],
                "selected_plugins": [p for p in ordered_plugins if p in selected],
                "plugin_reasons": decision.reasoning,
            }
            return (selected, lane_plan)

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


def cmd_check(args: argparse.Namespace) -> int:
    if not args.task:
        print("error: --task is required", file=sys.stderr)
        return ExitCode.usage

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent
    task_id = str(args.task)
    policy = load_drift_policy(wg_dir)
    ordered_plugins = _ordered_optional_plugins(policy.order)

    coredrift = wg_dir / "coredrift"
    if not coredrift.exists():
        print("error: .workgraph/coredrift not found; run driftdriver install first", file=sys.stderr)
        return ExitCode.usage

    contract_ensure = _maybe_auto_ensure_contracts(wg_dir=wg_dir, project_dir=project_dir, policy=policy)

    task = _load_task(wg_dir=wg_dir, task_id=task_id)
    selected_plugins, lane_plan = _select_optional_plugins(
        task=task,
        ordered_plugins=ordered_plugins,
        lane_strategy=getattr(args, "lane_strategy", "auto"),
        wg_dir=wg_dir,
    )

    force_write_log = bool(args.write_log)
    force_create_followups = bool(args.create_followups)
    loop_safety = _compute_loop_safety(wg_dir=wg_dir, task_id=task_id, policy=policy)
    effective_force_create_followups = force_create_followups

    mode = policy.mode
    effective_mode = mode
    if loop_safety["followups_blocked"] and mode not in {"observe", "advise"}:
        effective_mode = "advise"
        effective_force_create_followups = False
        reason_text = ", ".join(loop_safety["reasons"]) or "loop safety guard"
        print(
            f"note: loop safety blocked follow-up creation ({reason_text}); running in advise mode for this check",
            file=sys.stderr,
        )

    speed_write_log, speed_followups = _mode_flags(mode=effective_mode, plugin="coredrift")
    speed_write_log = speed_write_log or force_write_log
    speed_followups = speed_followups or effective_force_create_followups

    update_preflight = _run_update_preflight(
        wg_dir=wg_dir,
        policy=policy,
        task_id=task_id,
        write_log=speed_write_log,
        create_followups=effective_force_create_followups,
    )

    speed_cmd = [str(coredrift), "--dir", str(project_dir), "check", "--task", task_id]
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
                mode=effective_mode,
                force_write_log=force_write_log,
                force_create_followups=effective_force_create_followups,
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
            "effective_mode": effective_mode,
            "contract_auto_ensure": contract_ensure,
            "loop_safety": loop_safety,
            "update_preflight": update_preflight,
            "lane_strategy": lane_plan["strategy"],
            "lane_plan": lane_plan,
            "policy_order": ordered_plugins,
            "plugins": plugins_json,
            "action_plan": _normalize_actions(plugins_json),
        }
        if mode == "breaker" and out_rc == ExitCode.findings:
            breaker_id = _ensure_breaker_task(wg_dir=wg_dir, task_id=task_id)
            combined["breaker_task_id"] = breaker_id
        _record_check_findings(
            plugins_json=plugins_json,
            task_id=task_id,
            project_dir=project_dir,
        )
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
            mode=effective_mode,
            force_write_log=force_write_log,
            force_create_followups=effective_force_create_followups,
        )

    has_findings = any(rc == ExitCode.findings for rc in rc_by_plugin.values())
    if has_findings:
        # Build minimal plugin info for recording (text mode has no structured reports)
        text_plugins: dict[str, Any] = {}
        for plugin_name, rc in rc_by_plugin.items():
            if rc == ExitCode.findings:
                text_plugins[plugin_name] = {
                    "ran": True,
                    "exit_code": rc,
                    "report": {"findings": [{"kind": "drift_detected"}]},
                }
        _record_check_findings(
            plugins_json=text_plugins,
            task_id=task_id,
            project_dir=project_dir,
        )
        if mode == "breaker":
            _ensure_breaker_task(wg_dir=wg_dir, task_id=task_id)
        return ExitCode.findings
    return ExitCode.ok


def cmd_updates(args: argparse.Namespace) -> int:
    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    policy = load_drift_policy(wg_dir)
    enabled = bool(policy.updates_enabled)
    force = bool(getattr(args, "force", False))

    if not enabled and not force:
        message = "Update checks disabled in drift-policy.toml ([updates].enabled = false)."
        if args.json:
            print(
                json.dumps(
                    {
                        "enabled": False,
                        "checked": False,
                        "skipped": True,
                        "has_updates": False,
                        "updates": [],
                        "errors": [],
                        "message": message,
                    },
                    indent=2,
                    sort_keys=False,
                )
            )
        else:
            print(message)
        return ExitCode.ok

    try:
        sources = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=getattr(args, "config", None),
            watch_repo_specs=list(getattr(args, "watch_repo", []) or []),
            watch_user_specs=list(getattr(args, "watch_user", []) or []),
            watch_report_specs=list(getattr(args, "watch_report", []) or []),
            report_keyword_specs=list(getattr(args, "report_keyword", []) or []),
            user_repo_limit=getattr(args, "user_repo_limit", None),
        )
    except Exception as e:
        print(f"update source configuration error: {e}", file=sys.stderr)
        return ExitCode.usage

    interval = int(policy.updates_check_interval_seconds)
    if interval < 0:
        interval = 0
    result = check_ecosystem_updates(
        wg_dir=wg_dir,
        interval_seconds=interval,
        force=force,
        repos=sources["repos"],
        users=sources["users"],
        reports=sources["reports"],
        report_keywords=sources["report_keywords"],
        user_repo_limit=int(sources["user_repo_limit"]),
    )
    errors = _update_errors(result)
    has_updates = bool(result.get("has_updates"))
    has_discoveries = bool(result.get("has_discoveries"))
    has_findings = has_updates or has_discoveries

    review_path = getattr(args, "write_review", "")
    if review_path:
        try:
            review_out = Path(str(review_path))
            review_out.parent.mkdir(parents=True, exist_ok=True)
            review_out.write_text(render_review_markdown(result), encoding="utf-8")
        except Exception as e:
            print(f"note: could not write review markdown ({review_path}): {e}", file=sys.stderr)

    if args.json:
        output: dict[str, Any] = {
            "enabled": enabled,
            "checked": True,
            "force": force,
            "skipped": bool(result.get("skipped")),
            "checked_at": result.get("checked_at"),
            "interval_seconds": int(result.get("interval_seconds", interval)),
            "elapsed_seconds": int(result.get("elapsed_seconds", 0)),
            "has_updates": has_updates,
            "has_discoveries": has_discoveries,
            "has_findings": has_findings,
            "updates": result.get("updates") or [],
            "user_findings": result.get("user_findings") or [],
            "report_findings": result.get("report_findings") or [],
            "errors": errors,
            "sources": {
                "config_exists": bool(sources.get("config_exists")),
                "config_path": str(sources.get("config_path") or ""),
                "repos": len(sources.get("repos") or {}),
                "users": len(sources.get("users") or []),
                "reports": len(sources.get("reports") or []),
            },
        }
        if has_findings:
            output["summary"] = summarize_updates(result)
        print(json.dumps(output, indent=2, sort_keys=False))
        return ExitCode.findings if has_findings else ExitCode.ok

    if bool(result.get("skipped")):
        elapsed = int(result.get("elapsed_seconds", 0))
        interval_seconds = int(result.get("interval_seconds", interval))
        print(f"Update check skipped: interval not elapsed ({elapsed}s < {interval_seconds}s).")
    elif has_findings:
        print(summarize_updates(result))
    else:
        print("No ecosystem updates detected.")

    if errors:
        print("Update check errors:", file=sys.stderr)
        for error in errors[:6]:
            print(f"- {error}", file=sys.stderr)

    return ExitCode.findings if has_findings else ExitCode.ok
