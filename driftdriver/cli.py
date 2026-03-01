from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import sys
import shutil
from contextlib import redirect_stdout
from datetime import datetime, timezone
from dataclasses import asdict
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
from driftdriver.install import (
    InstallResult,
    ensure_amplifier_autostart_hook,
    ensure_amplifier_executor,
    install_amplifier_adapter,
    install_claude_code_hooks,
    install_codex_adapter,
    install_handler_scripts,
    install_lessons_mcp_config,
    install_opencode_hooks,
    install_session_driver_executor,
    ensure_archdrift_gitignore,
    ensure_executor_guidance,
    ensure_datadrift_gitignore,
    ensure_depsdrift_gitignore,
    ensure_fixdrift_gitignore,
    ensure_contrariandrift_gitignore,
    ensure_qadrift_gitignore,
    ensure_redrift_gitignore,
    ensure_specdrift_gitignore,
    ensure_coredrift_gitignore,
    ensure_therapydrift_gitignore,
    ensure_uxdrift_gitignore,
    ensure_yagnidrift_gitignore,
    resolve_bin,
    write_archdrift_wrapper,
    write_contrariandrift_wrapper,
    write_qadrift_wrapper,
    write_datadrift_wrapper,
    write_depsdrift_wrapper,
    write_drifts_wrapper,
    write_driver_wrapper,
    write_fixdrift_wrapper,
    write_redrift_wrapper,
    write_specdrift_wrapper,
    write_coredrift_wrapper,
    write_therapydrift_wrapper,
    write_uxdrift_wrapper,
    write_yagnidrift_wrapper,
)
from driftdriver.policy import ensure_drift_policy, load_drift_policy
from driftdriver.routing_models import parse_routing_response
from driftdriver.smart_routing import gather_evidence
from driftdriver.updates import (
    ECOSYSTEM_REPOS,
    check_ecosystem_updates,
    load_review_config,
    render_review_markdown,
    summarize_updates,
)
from driftdriver import wire
from driftdriver.project_profiles import build_profile, format_profile_report
from driftdriver.pm_coordination import get_ready_tasks
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


def _update_errors(result: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    sections = (
        ("repos", "tool"),
        ("user_checks", "user"),
        ("report_checks", "name"),
    )
    for section, label_key in sections:
        rows = result.get(section)
        if not isinstance(rows, list):
            continue
        for entry in rows:
            if not isinstance(entry, dict):
                continue
            error = str(entry.get("error") or "").strip()
            if not error:
                continue
            label = str(entry.get(label_key) or "unknown")
            errors.append(f"{label}: {error}")
    return errors


def _dedupe_strings(items: list[str]) -> list[str]:
    out: list[str] = []
    for raw in items:
        value = str(raw).strip()
        if value and value not in out:
            out.append(value)
    return out


def _parse_watch_repo(spec: str) -> tuple[str, str]:
    raw = str(spec).strip()
    if not raw:
        raise ValueError("empty --watch-repo spec")
    if "=" in raw:
        key, repo = raw.split("=", 1)
        tool = key.strip()
        remote = repo.strip()
    else:
        remote = raw
        tool = remote.split("/")[-1].strip()
    if not tool or not remote or "/" not in remote:
        raise ValueError(f"invalid --watch-repo spec: {spec!r} (expected tool=owner/repo)")
    return (tool, remote)


def _parse_watch_report(spec: str) -> dict[str, Any]:
    raw = str(spec).strip()
    if not raw:
        raise ValueError("empty --watch-report spec")
    if "=" in raw:
        name, url = raw.split("=", 1)
        report_name = name.strip() or url.strip()
        report_url = url.strip()
    else:
        report_name = raw
        report_url = raw
    if not report_url:
        raise ValueError(f"invalid --watch-report spec: {spec!r}")
    return {"name": report_name, "url": report_url, "keywords": []}


def _resolve_update_sources(
    *,
    wg_dir: Path,
    config_path: str | None,
    watch_repo_specs: list[str],
    watch_user_specs: list[str],
    watch_report_specs: list[str],
    report_keyword_specs: list[str],
    user_repo_limit: int | None,
) -> dict[str, Any]:
    cfg = load_review_config(wg_dir, config_path)

    cfg_repos = cfg.get("repos") if isinstance(cfg.get("repos"), dict) else None
    if cfg_repos is not None:
        repos: dict[str, str] = dict(cfg_repos)
    else:
        repos = dict(ECOSYSTEM_REPOS)
        extra = cfg.get("extra_repos")
        if isinstance(extra, dict):
            for k, v in extra.items():
                key = str(k).strip()
                val = str(v).strip()
                if key and val:
                    repos[key] = val

    for spec in watch_repo_specs:
        tool, remote = _parse_watch_repo(spec)
        repos[tool] = remote

    users: list[str] = []
    cfg_users = cfg.get("github_users")
    if isinstance(cfg_users, list):
        users.extend(str(x).strip().lstrip("@") for x in cfg_users)
    users.extend(str(x).strip().lstrip("@") for x in watch_user_specs)
    users = _dedupe_strings(users)

    reports: list[dict[str, Any]] = []
    cfg_reports = cfg.get("reports")
    if isinstance(cfg_reports, list):
        for row in cfg_reports:
            if isinstance(row, dict):
                reports.append(
                    {
                        "name": str(row.get("name") or row.get("url") or "").strip(),
                        "url": str(row.get("url") or "").strip(),
                        "keywords": row.get("keywords") if isinstance(row.get("keywords"), list) else [],
                    }
                )
    for spec in watch_report_specs:
        reports.append(_parse_watch_report(spec))
    dedup_reports: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for row in reports:
        url = str(row.get("url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        name = str(row.get("name") or url).strip() or url
        kws_raw = row.get("keywords")
        kws: list[str] = []
        if isinstance(kws_raw, list):
            kws = _dedupe_strings([str(x).strip() for x in kws_raw])
        dedup_reports.append({"name": name, "url": url, "keywords": kws})

    keywords: list[str] = []
    cfg_keywords = cfg.get("report_keywords")
    if isinstance(cfg_keywords, list):
        keywords.extend(str(x).strip() for x in cfg_keywords)
    keywords.extend(str(x).strip() for x in report_keyword_specs)
    keywords = _dedupe_strings(keywords)

    cfg_limit = cfg.get("user_repo_limit")
    try:
        configured_limit = int(cfg_limit) if cfg_limit is not None else 10
    except Exception:
        configured_limit = 10
    limit = int(user_repo_limit) if user_repo_limit is not None else configured_limit
    limit = max(1, min(limit, 100))

    return {
        "repos": repos,
        "users": users,
        "reports": dedup_reports,
        "report_keywords": keywords,
        "user_repo_limit": limit,
        "config_exists": bool(cfg.get("exists")),
        "config_path": str(cfg.get("source_path") or ""),
    }


def _wg_log_message(*, wg_dir: Path, task_id: str, message: str) -> None:
    try:
        subprocess.check_call(
            ["wg", "--dir", str(wg_dir), "log", task_id, message],
            stdout=subprocess.DEVNULL,
        )
    except Exception:
        print("note: could not write update preflight summary into wg log", file=sys.stderr)


def _ensure_update_followup_task(*, wg_dir: Path, task_id: str, summary: str) -> str:
    followup_id = f"drift-self-update-{task_id}"
    try:
        subprocess.check_output(
            ["wg", "--dir", str(wg_dir), "show", followup_id, "--json"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return followup_id
    except Exception:
        pass

    ts = datetime.now(timezone.utc).isoformat()
    desc = (
        "Speedrift ecosystem updates were detected during driftdriver preflight.\n\n"
        f"Origin task: {task_id}\n"
        f"Detected at: {ts}\n\n"
        "Decision needed: should the model/toolchain self-update now?\n\n"
        "Expected action:\n"
        "- review new commits in ecosystem repos\n"
        "- decide update now vs defer and log rationale\n"
        "- if updating, rerun `./.workgraph/drifts check --task "
        + task_id
        + " --write-log --create-followups`\n\n"
        "Preflight summary:\n"
        f"{summary}\n"
    )
    subprocess.check_call(
        [
            "wg",
            "--dir",
            str(wg_dir),
            "add",
            f"self-update decision: {task_id}",
            "--id",
            followup_id,
            "--blocked-by",
            task_id,
            "-d",
            desc,
            "-t",
            "drift",
            "-t",
            "updates",
        ]
    )
    return followup_id


def _run_update_preflight(
    *,
    wg_dir: Path,
    policy: Any,
    task_id: str,
    write_log: bool,
    create_followups: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "enabled": bool(getattr(policy, "updates_enabled", True)),
        "checked": False,
        "skipped": False,
        "has_updates": False,
        "has_discoveries": False,
        "updates": [],
        "user_findings": [],
        "report_findings": [],
        "errors": [],
        "summary": None,
        "followup_task_id": None,
    }
    if not out["enabled"]:
        return out

    interval = int(getattr(policy, "updates_check_interval_seconds", 21600))
    if interval < 0:
        interval = 0

    try:
        sources = _resolve_update_sources(
            wg_dir=wg_dir,
            config_path=None,
            watch_repo_specs=[],
            watch_user_specs=[],
            watch_report_specs=[],
            report_keyword_specs=[],
            user_repo_limit=None,
        )
    except Exception as e:
        out["errors"] = [str(e)]
        print(f"note: ecosystem update preflight config error: {e}", file=sys.stderr)
        return out

    try:
        result = check_ecosystem_updates(
            wg_dir=wg_dir,
            interval_seconds=interval,
            force=False,
            repos=sources["repos"],
            users=sources["users"],
            reports=sources["reports"],
            report_keywords=sources["report_keywords"],
            user_repo_limit=int(sources["user_repo_limit"]),
        )
    except Exception as e:
        out["errors"] = [str(e)]
        print(f"note: ecosystem update preflight failed: {e}", file=sys.stderr)
        return out
    out["checked"] = True
    out["skipped"] = bool(result.get("skipped"))
    out["checked_at"] = result.get("checked_at")
    out["interval_seconds"] = int(result.get("interval_seconds", interval))
    out["elapsed_seconds"] = int(result.get("elapsed_seconds", 0))
    out["has_discoveries"] = bool(result.get("has_discoveries"))
    out["has_updates"] = bool(result.get("has_updates")) or out["has_discoveries"]
    out["updates"] = result.get("updates") or []
    out["user_findings"] = result.get("user_findings") or []
    out["report_findings"] = result.get("report_findings") or []
    out["errors"] = _update_errors(result)

    if out["errors"]:
        print("note: ecosystem update preflight had lookup errors:", file=sys.stderr)
        for error in out["errors"][:6]:
            print(f"  - {error}", file=sys.stderr)

    if out["has_updates"]:
        summary = summarize_updates(result)
        out["summary"] = summary
        print(summary, file=sys.stderr)
        if write_log:
            _wg_log_message(wg_dir=wg_dir, task_id=task_id, message=summary.replace("\n", " | "))
        should_create_followup = create_followups or bool(getattr(policy, "updates_create_followup", False))
        if should_create_followup:
            try:
                out["followup_task_id"] = _ensure_update_followup_task(
                    wg_dir=wg_dir,
                    task_id=task_id,
                    summary=summary,
                )
            except Exception as e:
                print(f"note: could not create update follow-up task: {e}", file=sys.stderr)

    return out


def _maybe_auto_ensure_contracts(*, wg_dir: Path, project_dir: Path, policy: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "enabled": bool(getattr(policy, "contracts_auto_ensure", True)),
        "attempted": False,
        "applied": False,
        "error": None,
    }
    if not out["enabled"]:
        return out

    coredrift = wg_dir / "coredrift"
    if not coredrift.exists():
        out["error"] = "coredrift wrapper not found"
        return out

    out["attempted"] = True
    cmd = [str(coredrift), "--dir", str(project_dir), "ensure-contracts", "--apply"]
    proc = subprocess.run(cmd, text=True, capture_output=True)
    if int(proc.returncode) != 0:
        out["error"] = (proc.stderr or proc.stdout or "ensure-contracts failed").strip()[:1000]
        print("note: contract auto-ensure failed; continuing", file=sys.stderr)
        return out

    out["applied"] = True
    return out


def _compute_loop_safety(*, wg_dir: Path, task_id: str, policy: Any) -> dict[str, Any]:
    wg = load_workgraph(wg_dir)
    tasks = list(wg.tasks.values())
    tasks_by_id = {str(t.get("id") or ""): t for t in tasks}

    depth = redrift_depth(task_id) if str(task_id).startswith("redrift-") else 0
    max_depth = int(getattr(policy, "loop_max_redrift_depth", 2))
    if max_depth < 0:
        max_depth = 0

    ready_queue = rank_ready_drift_queue(tasks, limit=10_000)
    ready_count = len(ready_queue)
    max_ready = int(getattr(policy, "loop_max_ready_drift_followups", 20))
    if max_ready < 0:
        max_ready = 0

    has_cycle = detect_cycle_from(task_id, tasks_by_id)
    reasons: list[str] = []
    if depth > max_depth:
        reasons.append(f"redrift_depth_exceeded ({depth} > {max_depth})")
    if ready_count > max_ready:
        reasons.append(f"ready_drift_queue_exceeded ({ready_count} > {max_ready})")
    if has_cycle:
        reasons.append("blocked_by_cycle_detected")

    block = bool(getattr(policy, "loop_block_followup_creation", True)) and bool(reasons)
    return {
        "max_redrift_depth": max_depth,
        "observed_redrift_depth": depth,
        "max_ready_drift_followups": max_ready,
        "ready_drift_followups": ready_count,
        "blocked_by_cycle": has_cycle,
        "followups_blocked": block,
        "reasons": reasons,
    }


def _wrapper_commands_available(*, wrapper: Path) -> list[str]:
    if not wrapper.exists():
        return []
    proc = subprocess.run([str(wrapper), "--help"], text=True, capture_output=True)
    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    found: list[str] = []
    for name in ("install", "check", "updates", "doctor", "queue", "run", "orchestrate"):
        if re.search(rf"\b{name}\b", text):
            found.append(name)
    return found


def _collect_findings(plugins: dict[str, Any]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for plugin, payload in plugins.items():
        report = payload.get("report") if isinstance(payload, dict) else None
        if not isinstance(report, dict):
            continue
        findings = report.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            kind = str(finding.get("kind") or "").strip()
            if not kind:
                continue
            pairs.append((plugin, kind))
    return pairs


def _normalize_actions(plugins: dict[str, Any]) -> list[dict[str, str]]:
    kind_to_action = {
        "missing_contract": "scope",
        "scope_drift": "scope",
        "hardening_in_core": "harden",
        "dependency_drift": "respec",
        "repeated_fix_attempts": "fix",
        "unresolved_fix_followups": "fix",
        "missing_repro_evidence": "fix",
        "missing_root_cause_evidence": "fix",
        "missing_regression_evidence": "fix",
        "missing_redrift_artifacts": "respec",
        "phase_incomplete_analyze": "respec",
        "phase_incomplete_respec": "respec",
        "phase_incomplete_design": "respec",
        "phase_incomplete_build": "respec",
        "repeated_drift_signals": "harden",
        "unresolved_drift_followups": "harden",
        "missing_recovery_plan": "harden",
    }
    out: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for plugin, kind in _collect_findings(plugins):
        action = kind_to_action.get(kind, "ignore-with-rationale")
        key = (action, kind)
        if key in seen:
            continue
        seen.add(key)
        out.append({"action": action, "kind": kind, "source": plugin})
    return out


def _doctor_report(*, wg_dir: Path, policy: Any) -> dict[str, Any]:
    wg = load_workgraph(wg_dir)
    tasks = list(wg.tasks.values())
    wrappers = {
        "driftdriver": (wg_dir / "driftdriver").exists(),
        "drifts": (wg_dir / "drifts").exists(),
        "coredrift": (wg_dir / "coredrift").exists(),
    }
    commands = _wrapper_commands_available(wrapper=wg_dir / "drifts")
    score = compute_scoreboard(tasks)

    active_tasks = [t for t in tasks if is_active(t)]
    missing_contract_ids = [str(t.get("id") or "") for t in active_tasks if not has_contract(t)]

    issues: list[dict[str, str]] = []
    required_commands = {"check", "updates", "doctor", "queue", "run"}
    missing_commands = sorted(list(required_commands - set(commands)))
    if missing_commands:
        issues.append(
            {
                "severity": "high",
                "kind": "wrapper_outdated",
                "message": f"drifts wrapper misses commands: {', '.join(missing_commands)}",
            }
        )

    if score["active_contract_coverage"] < 0.9:
        issues.append(
            {
                "severity": "high" if score["active_contract_coverage"] < 0.7 else "medium",
                "kind": "contract_coverage",
                "message": f"active contract coverage is {score['active_contract_coverage']:.2f}",
            }
        )

    max_depth = int(getattr(policy, "loop_max_redrift_depth", 2))
    if int(score["max_redrift_depth"]) > max_depth:
        issues.append(
            {
                "severity": "high",
                "kind": "loop_depth",
                "message": f"max redrift depth {score['max_redrift_depth']} exceeds policy limit {max_depth}",
            }
        )

    max_ready = int(getattr(policy, "loop_max_ready_drift_followups", 20))
    if int(score["ready_drift"]) > max_ready:
        issues.append(
            {
                "severity": "high",
                "kind": "queue_pressure",
                "message": f"ready drift queue {score['ready_drift']} exceeds policy limit {max_ready}",
            }
        )

    duplicate_groups = score.get("duplicate_open_drift_groups") or []
    if duplicate_groups:
        issues.append(
            {
                "severity": "medium",
                "kind": "duplicate_followups",
                "message": f"{len(duplicate_groups)} duplicate open drift groups detected",
            }
        )

    status = "healthy"
    if any(i["severity"] == "high" for i in issues):
        status = "risk"
    elif issues:
        status = "watch"

    return {
        "status": status,
        "wrappers": wrappers,
        "commands_available": commands,
        "scoreboard": score,
        "active_missing_contract_count": len(missing_contract_ids),
        "active_missing_contract_sample": missing_contract_ids[:10],
        "issues": issues,
    }


def _compact_plan(*, tasks: list[dict[str, Any]], max_ready: int, max_redrift_depth: int) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for task in tasks:
        if not is_drift_task(task) or not is_active(task):
            continue
        key = normalize_drift_key(task)
        grouped.setdefault(key, []).append(task)

    def _created_epoch(task: dict[str, Any]) -> int:
        raw = str(task.get("created_at") or "").strip()
        if not raw:
            return 0
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except Exception:
            return 0
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp())

    duplicate_groups: list[dict[str, Any]] = []
    abandon_ids: list[str] = []
    for key, rows in grouped.items():
        if len(rows) <= 1:
            continue
        ordered = sorted(
            rows,
            key=lambda t: (
                0 if str(t.get("status") or "") == "in-progress" else 1,
                _created_epoch(t),
                str(t.get("id") or ""),
            ),
        )
        keep = str(ordered[0].get("id") or "")
        drop = [str(t.get("id") or "") for t in ordered[1:] if str(t.get("id") or "")]
        if drop:
            duplicate_groups.append(
                {
                    "key": key,
                    "keep_task_id": keep,
                    "abandon_task_ids": drop,
                }
            )
            abandon_ids.extend(drop)

    depth_exceeded_ids: list[str] = []
    depth_limit = max(0, int(max_redrift_depth))
    for task in tasks:
        if not is_drift_task(task) or not is_active(task):
            continue
        task_id = str(task.get("id") or "")
        if (
            task_id.startswith("redrift-")
            and redrift_depth(task_id) > depth_limit
            and str(task.get("status") or "") != "in-progress"
        ):
            depth_exceeded_ids.append(task_id)

    if depth_exceeded_ids:
        abandon_ids.extend(depth_exceeded_ids)

    ready = rank_ready_drift_queue(tasks, limit=10_000)
    safe_max_ready = max(0, int(max_ready))
    overflow = ready[safe_max_ready:] if len(ready) > safe_max_ready else []
    overflow_ids = [str(item.get("task_id") or "") for item in overflow if str(item.get("task_id") or "")]

    # Don't defer tasks that are already being abandoned as duplicates.
    abandon_set = set(abandon_ids)
    overflow_ids = [tid for tid in overflow_ids if tid not in abandon_set]

    return {
        "duplicate_groups": duplicate_groups,
        "depth_exceeded_redrift_task_ids": sorted(set(depth_exceeded_ids)),
        "abandon_task_ids": sorted(set(abandon_ids)),
        "ready_drift_before": len(ready),
        "max_ready_drift": safe_max_ready,
        "max_redrift_depth": depth_limit,
        "defer_task_ids": overflow_ids,
    }


def _repair_wrappers(*, wg_dir: Path) -> int:
    include_ux = (wg_dir / "uxdrift").exists()
    include_therapy = (wg_dir / "therapydrift").exists()
    include_fix = (wg_dir / "fixdrift").exists()
    include_yagni = (wg_dir / "yagnidrift").exists()
    include_redrift = (wg_dir / "redrift").exists()
    args = argparse.Namespace(
        dir=str(wg_dir.parent),
        json=False,
        coredrift_bin=None,
        specdrift_bin=None,
        datadrift_bin=None,
        archdrift_bin=None,
        depsdrift_bin=None,
        with_uxdrift=include_ux,
        uxdrift_bin=None,
        with_therapydrift=include_therapy,
        therapydrift_bin=None,
        with_fixdrift=include_fix,
        fixdrift_bin=None,
        with_yagnidrift=include_yagni,
        yagnidrift_bin=None,
        with_redrift=include_redrift,
        redrift_bin=None,
        with_amplifier_executor=(wg_dir / "executors" / "amplifier.toml").exists(),
        with_claude_code_hooks=(wg_dir.parent / ".claude" / "hooks.json").exists(),
        wrapper_mode="portable",
        no_ensure_contracts=False,
    )
    return cmd_install(args)


def _invoke_check_json(args: argparse.Namespace) -> tuple[int, dict[str, Any]]:
    capture = io.StringIO()
    with redirect_stdout(capture):
        rc = cmd_check(args)
    raw = capture.getvalue().strip()
    if not raw:
        return (int(rc), {})
    try:
        return (int(rc), json.loads(raw))
    except Exception:
        return (int(rc), {"raw": raw})


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

    include_fixdrift = bool(args.with_fixdrift or args.fixdrift_bin)
    fixdrift_bin = resolve_bin(
        explicit=Path(args.fixdrift_bin) if args.fixdrift_bin else None,
        env_var="FIXDRIFT_BIN",
        which_name="fixdrift",
        candidates=[
            repo_root.parent / "fixdrift" / "bin" / "fixdrift",
        ],
    )
    if include_fixdrift and fixdrift_bin is None:
        # Best-effort: don't fail install.
        include_fixdrift = False

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

    contrariandrift_bin = resolve_bin(
        explicit=None,
        env_var="CONTRARIANDRIFT_BIN",
        which_name="contrariandrift",
        candidates=[
            repo_root.parent / "contrariandrift" / "bin" / "contrariandrift",
        ],
    )

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

    handler_written, handler_count = install_handler_scripts(wg_dir)

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
    wrote_fixdrift = False
    if include_fixdrift and fixdrift_bin is not None:
        wrote_fixdrift = write_fixdrift_wrapper(
            wg_dir,
            fixdrift_bin=fixdrift_bin,
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
    wrote_contrariandrift = False
    if contrariandrift_bin is not None:
        wrote_contrariandrift = write_contrariandrift_wrapper(
            wg_dir,
            contrariandrift_bin=contrariandrift_bin,
            wrapper_mode=wrapper_mode,
        )
    wrote_qadrift = write_qadrift_wrapper(wg_dir)

    wrote_amplifier_executor = False
    wrote_amplifier_runner = False
    wrote_amplifier_autostart_hook = False
    wrote_amplifier_autostart_hooks_json = False
    if bool(getattr(args, "with_amplifier_executor", False)):
        wrote_amplifier_executor, wrote_amplifier_runner = ensure_amplifier_executor(wg_dir, bundle_name="speedrift")
        wrote_amplifier_autostart_hook, wrote_amplifier_autostart_hooks_json = ensure_amplifier_autostart_hook(project_dir)

    wrote_claude_code_hooks = False
    if bool(getattr(args, "with_claude_code_hooks", False)):
        wrote_claude_code_hooks = install_claude_code_hooks(project_dir)

    wrote_session_driver_executor = False
    wrote_session_driver_runner = False
    if bool(getattr(args, "all_clis", False)):
        wrote_claude_code_hooks = install_claude_code_hooks(project_dir) or wrote_claude_code_hooks
        install_codex_adapter(project_dir)
        install_opencode_hooks(project_dir)
        install_amplifier_adapter(project_dir)
        wrote_session_driver_executor, wrote_session_driver_runner = install_session_driver_executor(wg_dir)

    if bool(getattr(args, "with_lessons_mcp", False)):
        install_lessons_mcp_config(wg_dir)

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
    if include_fixdrift:
        updated_gitignore = ensure_fixdrift_gitignore(wg_dir) or updated_gitignore
    if include_yagnidrift:
        updated_gitignore = ensure_yagnidrift_gitignore(wg_dir) or updated_gitignore
    if include_redrift:
        updated_gitignore = ensure_redrift_gitignore(wg_dir) or updated_gitignore
    updated_gitignore = ensure_contrariandrift_gitignore(wg_dir) or updated_gitignore
    updated_gitignore = ensure_qadrift_gitignore(wg_dir) or updated_gitignore

    created_executor, patched_executors = ensure_executor_guidance(
        wg_dir,
        include_archdrift=bool(archdrift_bin),
        include_uxdrift=include_uxdrift,
        include_therapydrift=include_therapydrift,
        include_fixdrift=include_fixdrift,
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
        wrote_fixdrift=wrote_fixdrift,
        wrote_yagnidrift=wrote_yagnidrift,
        wrote_redrift=wrote_redrift,
        wrote_contrariandrift=wrote_contrariandrift,
        wrote_qadrift=wrote_qadrift,
        wrote_handlers=handler_written,
        wrote_amplifier_executor=wrote_amplifier_executor,
        wrote_amplifier_runner=wrote_amplifier_runner,
        wrote_amplifier_autostart_hook=wrote_amplifier_autostart_hook,
        wrote_amplifier_autostart_hooks_json=wrote_amplifier_autostart_hooks_json,
        wrote_session_driver_executor=wrote_session_driver_executor,
        wrote_session_driver_runner=wrote_session_driver_runner,
        wrote_claude_code_hooks=wrote_claude_code_hooks,
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
        if include_fixdrift:
            enabled.append("fixdrift")
        if include_yagnidrift:
            enabled.append("yagnidrift")
        if include_redrift:
            enabled.append("redrift")
        if bool(getattr(args, "with_amplifier_executor", False)):
            enabled.append("amplifier-executor")
        if bool(getattr(args, "with_claude_code_hooks", False)):
            enabled.append("claude-code-hooks")
        if bool(getattr(args, "all_clis", False)):
            enabled.append("all-clis")
        if bool(getattr(args, "with_lessons_mcp", False)):
            enabled.append("lessons-mcp")
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

    if any(rc == ExitCode.findings for rc in rc_by_plugin.values()):
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


def cmd_queue(args: argparse.Namespace) -> int:
    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    wg = load_workgraph(wg_dir)
    tasks = list(wg.tasks.values())

    limit = int(getattr(args, "limit", 10))
    if limit < 1:
        limit = 1
    ready = rank_ready_drift_queue(tasks, limit=limit)
    duplicates = find_duplicate_open_drift_groups(tasks)
    out = {
        "ready_drift": ready,
        "duplicate_open_drift_groups": duplicates,
        "scoreboard": compute_scoreboard(tasks),
    }

    as_json = bool(getattr(args, "json", False))
    if as_json:
        print(json.dumps(out, indent=2, sort_keys=False))
        return ExitCode.ok

    print(f"Ready drift queue: {len(ready)}")
    for item in ready:
        print(f"- {item['task_id']} [p={item['priority']}] {item['title']}")

    if duplicates:
        print(f"\nDuplicate drift groups: {len(duplicates)}")
        for group in duplicates[:5]:
            print(f"- {group['key']} ({group['count']}): {', '.join(group['task_ids'][:4])}")
    return ExitCode.ok


def cmd_compact(args: argparse.Namespace) -> int:
    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    policy = load_drift_policy(wg_dir)
    wg = load_workgraph(wg_dir)
    tasks = list(wg.tasks.values())

    max_ready_default = int(getattr(policy, "loop_max_ready_drift_followups", 20))
    max_ready_raw = getattr(args, "max_ready", None)
    max_ready = max_ready_default if max_ready_raw is None else int(max_ready_raw)
    if max_ready < 0:
        max_ready = 0
    max_redrift_depth = int(getattr(policy, "loop_max_redrift_depth", 2))
    if max_redrift_depth < 0:
        max_redrift_depth = 0
    defer_hours = int(getattr(args, "defer_hours", 24))
    if defer_hours < 1:
        defer_hours = 1

    plan = _compact_plan(tasks=tasks, max_ready=max_ready, max_redrift_depth=max_redrift_depth)
    applied_abandoned: list[str] = []
    applied_deferred: list[str] = []
    errors: list[str] = []

    if bool(getattr(args, "apply", False)):
        for task_id in plan["abandon_task_ids"]:
            try:
                subprocess.check_call(["wg", "--dir", str(wg_dir), "abandon", task_id], stdout=subprocess.DEVNULL)
                applied_abandoned.append(task_id)
            except Exception as e:
                errors.append(f"abandon {task_id}: {e}")

        for task_id in plan["defer_task_ids"]:
            try:
                subprocess.check_call(
                    ["wg", "--dir", str(wg_dir), "reschedule", task_id, "--after", str(defer_hours)],
                    stdout=subprocess.DEVNULL,
                )
                applied_deferred.append(task_id)
            except Exception as e:
                errors.append(f"reschedule {task_id}: {e}")

    after_tasks = list(load_workgraph(wg_dir).tasks.values())
    score_before = compute_scoreboard(tasks)
    score_after = compute_scoreboard(after_tasks)

    report = {
        "applied": bool(getattr(args, "apply", False)),
        "defer_hours": defer_hours,
        "plan": plan,
        "applied_abandoned": applied_abandoned,
        "applied_deferred": applied_deferred,
        "errors": errors,
        "scoreboard_before": score_before,
        "scoreboard_after": score_after,
    }

    as_json = bool(getattr(args, "json", False))
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(f"Applied: {report['applied']}")
        print(
            f"Plan: abandon={len(plan['abandon_task_ids'])} defer={len(plan['defer_task_ids'])} "
            f"(ready {plan['ready_drift_before']} -> target {plan['max_ready_drift']})"
        )
        if report["applied"]:
            print(f"Applied abandon={len(applied_abandoned)} defer={len(applied_deferred)}")
        if errors:
            print("Errors:")
            for item in errors[:8]:
                print(f"- {item}")
        print(
            "Scoreboard: "
            f"{score_before.get('status')} -> {score_after.get('status')}, "
            f"ready_drift {score_before.get('ready_drift')} -> {score_after.get('ready_drift')}"
        )

    if errors:
        return ExitCode.usage
    return ExitCode.ok


def cmd_doctor(args: argparse.Namespace) -> int:
    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    project_dir = wg_dir.parent
    policy = load_drift_policy(wg_dir)
    notes: list[str] = []

    if bool(getattr(args, "fix", False)):
        rc = _repair_wrappers(wg_dir=wg_dir)
        if rc != ExitCode.ok:
            notes.append("wrapper repair failed")
        ensured = _maybe_auto_ensure_contracts(wg_dir=wg_dir, project_dir=project_dir, policy=policy)
        if ensured.get("error"):
            notes.append("contract auto-ensure failed during fix")

    report = _doctor_report(wg_dir=wg_dir, policy=policy)
    if notes:
        report["notes"] = notes

    as_json = bool(getattr(args, "json", False))
    if as_json:
        print(json.dumps(report, indent=2, sort_keys=False))
    else:
        print(f"Doctor status: {report['status']}")
        score = report.get("scoreboard") or {}
        print(
            "Scoreboard: "
            f"active={score.get('active_tasks', 0)} "
            f"active_drift={score.get('active_drift', 0)} "
            f"ready_drift={score.get('ready_drift', 0)} "
            f"contract_coverage={float(score.get('active_contract_coverage', 0.0)):.2f}"
        )
        issues = report.get("issues") or []
        if issues:
            print("Issues:")
            for issue in issues:
                print(f"- [{issue['severity']}] {issue['kind']}: {issue['message']}")
        else:
            print("Issues: none")

    return ExitCode.findings if report.get("status") != "healthy" else ExitCode.ok


def cmd_run(args: argparse.Namespace) -> int:
    if not args.task:
        print("error: --task is required", file=sys.stderr)
        return ExitCode.usage

    check_args = argparse.Namespace(
        dir=args.dir,
        task=args.task,
        lane_strategy=getattr(args, "lane_strategy", "auto"),
        write_log=True,
        create_followups=True,
        json=True,
    )
    rc, check_report = _invoke_check_json(check_args)

    wg_dir = find_workgraph_dir(Path(args.dir) if args.dir else None)
    wg = load_workgraph(wg_dir)
    tasks = list(wg.tasks.values())
    max_next = int(getattr(args, "max_next", 3))
    if max_next < 1:
        max_next = 1
    next_actions = rank_ready_drift_queue(tasks, limit=max_next)
    duplicates = find_duplicate_open_drift_groups(tasks)
    out = {
        "exit_code": rc,
        "check": check_report,
        "next_actions": next_actions,
        "duplicate_open_drift_groups": duplicates,
        "scoreboard": compute_scoreboard(tasks),
    }

    as_json = bool(getattr(args, "json", False))
    if as_json:
        print(json.dumps(out, indent=2, sort_keys=False))
        return int(rc)

    print(f"Run exit code: {rc}")
    action_plan = check_report.get("action_plan") if isinstance(check_report, dict) else None
    if isinstance(action_plan, list) and action_plan:
        print("Normalized actions:")
        for item in action_plan[:5]:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "")
            kind = str(item.get("kind") or "")
            source = str(item.get("source") or "")
            print(f"- {action}: {kind} ({source})")
    else:
        print("Normalized actions: none")

    print("Next actions:")
    if not next_actions:
        print("- none")
    else:
        for item in next_actions:
            print(f"- {item['task_id']} [p={item['priority']}] {item['title']}")

    if duplicates:
        print(f"Duplicate open drift groups: {len(duplicates)}")
    return int(rc)


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


def cmd_wire_verify(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_verify(project_dir)
    print(json.dumps(result))
    return 0 if result.get("passed") else 1


def cmd_wire_loop_check(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_loop_check(project_dir, args.tool_name, args.tool_input)
    print(json.dumps(result))
    return 1 if result.get("detected") else 0


def cmd_wire_enrich(args: argparse.Namespace) -> int:
    result = wire.cmd_enrich(args.task_id, args.task_description, args.project, [])
    print(json.dumps(result))
    return 0


def cmd_wire_bridge(args: argparse.Namespace) -> int:
    result = wire.cmd_bridge(Path(args.events_file), args.session_id, args.project)
    print(json.dumps(result))
    return 0


def cmd_wire_distill(args: argparse.Namespace) -> int:
    result = wire.cmd_distill([], [])
    print(json.dumps(result))
    return 0


def cmd_wire_rollback_eval(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_rollback_eval(args.drift_score, args.task_id, project_dir)
    print(json.dumps(result))
    return 0


def cmd_profile(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    profile = build_profile(project_dir.name, [])
    print(format_profile_report(profile))
    return 0


def cmd_ready(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    tasks = get_ready_tasks(project_dir)
    if args.json:
        print(json.dumps(tasks))
    else:
        for t in tasks:
            print(f"  {t.get('id', '?')}  {t.get('title', '')}")
    return 0


def cmd_wire_prime(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_prime(project_dir)
    print(result)
    return 0


def cmd_wire_recover(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_recover(project_dir)
    print(json.dumps([r.__dict__ if hasattr(r, "__dict__") else r for r in result]))
    return 0


def cmd_wire_scope_check(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    patterns = args.allowed_patterns.split(",") if args.allowed_patterns else []
    result = wire.cmd_scope_check(project_dir, patterns)
    print(result)
    return 0


def cmd_wire_reflect(args: argparse.Namespace) -> int:
    project_dir = Path(args.dir) if args.dir else Path.cwd()
    result = wire.cmd_reflect(project_dir)
    print(result)
    return 0


def _build_parser() -> argparse.ArgumentParser:
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
        "--with-fixdrift",
        action="store_true",
        help="Best-effort: enable fixdrift integration if found",
    )
    install.add_argument("--fixdrift-bin", help="Path to fixdrift bin/fixdrift (enables fixdrift integration)")
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
        "--with-amplifier-executor",
        action="store_true",
        help="Install .workgraph/executors/amplifier.toml + autostart hooks for Amplifier sessions",
    )
    install.add_argument(
        "--with-claude-code-hooks",
        action="store_true",
        help="Install .claude/hooks.json adapter for Claude Code lifecycle events",
    )
    install.add_argument(
        "--all-clis",
        action="store_true",
        help="Install all CLI adapter hooks at once (claude-code, codex, opencode, amplifier, session-driver)",
    )
    install.add_argument(
        "--with-lessons-mcp",
        action="store_true",
        help="Configure lessons-mcp in .mcp.json in the project root",
    )
    install.add_argument("--json", action="store_true", help="JSON output")
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
    check.add_argument("--json", action="store_true", help="JSON output")
    check.add_argument("--write-log", action="store_true", help="Write summary into wg log")
    check.add_argument("--create-followups", action="store_true", help="Create follow-up tasks for findings")
    check.set_defaults(func=cmd_check)

    updates = sub.add_parser("updates", help="Check Speedrift ecosystem repos for upstream updates")
    updates.add_argument("--json", action="store_true", help="JSON output")
    updates.add_argument("--force", action="store_true", help="Ignore interval and check remotes now")
    updates.add_argument(
        "--config",
        help="Path to ecosystem review JSON config (default: .workgraph/.driftdriver/ecosystem-review.json)",
    )
    updates.add_argument(
        "--watch-repo",
        action="append",
        default=[],
        help="Extra repo watch target in the form tool=owner/repo (repeatable)",
    )
    updates.add_argument(
        "--watch-user",
        action="append",
        default=[],
        help="GitHub user to scan for new/updated repos (repeatable)",
    )
    updates.add_argument(
        "--watch-report",
        action="append",
        default=[],
        help="Report URL to watch, optionally named as name=url (repeatable)",
    )
    updates.add_argument(
        "--report-keyword",
        action="append",
        default=[],
        help="Keyword to surface from watched report content (repeatable)",
    )
    updates.add_argument(
        "--user-repo-limit",
        type=int,
        help="Max repos per watched GitHub user to inspect (default: config value or 10)",
    )
    updates.add_argument(
        "--write-review",
        help="Write a markdown review report to this path",
    )
    updates.set_defaults(func=cmd_updates)

    queue = sub.add_parser("queue", help="Show ranked ready drift follow-ups and duplicate groups")
    queue.add_argument("--json", action="store_true", help="JSON output")
    queue.add_argument("--limit", type=int, default=10, help="Maximum queue items to display (default: 10)")
    queue.set_defaults(func=cmd_queue)

    doctor = sub.add_parser("doctor", help="Health audit for wrappers, contracts, drift queue pressure, and loop risk")
    doctor.add_argument("--json", action="store_true", help="JSON output")
    doctor.add_argument("--fix", action="store_true", help="Reinstall wrappers + run contract hygiene before reporting")
    doctor.set_defaults(func=cmd_doctor)

    compact = sub.add_parser(
        "compact",
        help="Compact drift queue by abandoning duplicate follow-ups and deferring overflow ready items",
    )
    compact.add_argument("--json", action="store_true", help="JSON output")
    compact.add_argument("--apply", action="store_true", help="Apply compaction actions (default: dry-run)")
    compact.add_argument(
        "--max-ready",
        type=int,
        help="Ready drift queue cap for overflow defer (default: policy loop_safety.max_ready_drift_followups)",
    )
    compact.add_argument("--defer-hours", type=int, default=24, help="Reschedule overflow items by this many hours")
    compact.set_defaults(func=cmd_compact)

    run = sub.add_parser("run", help="One-shot operation: check + normalized actions + next queued drift tasks")
    run.add_argument("--task", help="Task id to run")
    run.add_argument(
        "--lane-strategy",
        choices=LANE_STRATEGIES,
        default="auto",
        help="Optional lane routing: auto (default), fences, or all.",
    )
    run.add_argument("--max-next", type=int, default=3, help="Max queued next actions to print (default: 3)")
    run.add_argument("--json", action="store_true", help="JSON output")
    run.set_defaults(func=cmd_run)

    orch = sub.add_parser("orchestrate", help="Run continuous drift monitor+redirect loops (delegates to coredrift)")
    orch.add_argument("--interval", type=int, default=30, help="Monitor poll interval seconds (default: 30)")
    orch.add_argument("--redirect-interval", type=int, default=5, help="Redirect poll interval seconds (default: 5)")
    orch.add_argument("--write-log", action="store_true", help="Write a drift summary to wg log (redirect agent)")
    orch.add_argument("--create-followups", action="store_true", help="Create follow-up tasks (redirect agent)")
    orch.set_defaults(func=cmd_orchestrate)

    verify_p = sub.add_parser("verify", help="Run verification checks on the project")
    verify_p.set_defaults(func=cmd_wire_verify)

    loop_check_p = sub.add_parser("loop-check", help="Record a tool action and detect loops")
    loop_check_p.add_argument("--tool-name", default="unknown", help="Tool name")
    loop_check_p.add_argument("--tool-input", default="", help="Tool input string")
    loop_check_p.set_defaults(func=cmd_wire_loop_check)

    enrich_p = sub.add_parser("enrich", help="Enrich a task contract with prior learnings")
    enrich_p.add_argument("--task-id", default="", help="Task ID")
    enrich_p.add_argument("--task-description", default="", help="Task description")
    enrich_p.add_argument("--project", default="", help="Project name")
    enrich_p.set_defaults(func=cmd_wire_enrich)

    bridge_p = sub.add_parser("bridge", help="Parse events file and emit Lessons MCP calls")
    bridge_p.add_argument("--events-file", default="events.jsonl", help="Path to JSONL events file")
    bridge_p.add_argument("--session-id", default="", help="Session ID")
    bridge_p.add_argument("--project", default="", help="Project name")
    bridge_p.set_defaults(func=cmd_wire_bridge)

    distill_p = sub.add_parser("distill", help="Distill events into knowledge entries")
    distill_p.set_defaults(func=cmd_wire_distill)

    rollback_p = sub.add_parser("rollback-eval", help="Evaluate drift score and return rollback decision")
    rollback_p.add_argument("--drift-score", type=float, default=0.0, help="Drift score (0.0-1.0)")
    rollback_p.add_argument("--task-id", default="", help="Task ID")
    rollback_p.set_defaults(func=cmd_wire_rollback_eval)

    profile_p = sub.add_parser("profile", help="Build and display a project profile report")
    profile_p.set_defaults(func=cmd_profile)

    ready_p = sub.add_parser("ready", help="List ready tasks from the workgraph")
    ready_p.set_defaults(func=cmd_ready)

    prime_p = sub.add_parser("prime", help="Prime knowledge context for current task scope")
    prime_p.set_defaults(func=cmd_wire_prime)

    recover_p = sub.add_parser("recover", help="List interrupted tasks that can be recovered")
    recover_p.set_defaults(func=cmd_wire_recover)

    scope_check_p = sub.add_parser("scope-check", help="Check if current changes are within declared scope")
    scope_check_p.add_argument("--allowed-patterns", default="", help="Comma-separated allowed file patterns")
    scope_check_p.set_defaults(func=cmd_wire_scope_check)

    reflect_p = sub.add_parser("reflect", help="Run self-reflect on recent task")
    reflect_p.set_defaults(func=cmd_wire_reflect)

    autopilot_p = sub.add_parser("autopilot", help="Run project autopilot: goal → tasks → workers → drift → done")
    autopilot_p.add_argument("--goal", required=True, help="High-level goal to decompose and execute")
    autopilot_p.add_argument("--max-parallel", type=int, default=4, help="Max parallel workers (default: 4)")
    autopilot_p.add_argument("--worker-timeout", type=int, default=1800, help="Worker timeout in seconds (default: 1800)")
    autopilot_p.add_argument("--dry-run", action="store_true", help="Print plan without dispatching workers")
    autopilot_p.add_argument("--skip-decompose", action="store_true", help="Skip goal decomposition, use existing wg tasks")
    autopilot_p.add_argument("--skip-review", action="store_true", help="Skip milestone review after completion")
    autopilot_p.set_defaults(func=cmd_autopilot)

    return p


def cmd_autopilot(args: argparse.Namespace) -> int:
    """Run the project autopilot."""
    from driftdriver.autopilot_state import (
        clear_run_state,
        save_run_state,
        save_worker_event,
    )
    from driftdriver.project_autopilot import (
        AutopilotConfig,
        AutopilotRun,
        decompose_goal,
        discover_session_driver,
        generate_report,
        run_autopilot_loop,
        run_milestone_review,
    )

    project_dir = Path(args.dir) if args.dir else Path.cwd()
    wg_dir = project_dir / ".workgraph"
    if not wg_dir.exists():
        print("Error: no .workgraph found. Run `wg init` first.", file=sys.stderr)
        return 1

    config = AutopilotConfig(
        project_dir=project_dir,
        max_parallel=args.max_parallel,
        worker_timeout=args.worker_timeout,
        dry_run=args.dry_run,
        goal=args.goal,
    )

    # Step 1: Decompose goal into workgraph tasks (unless --skip-decompose)
    if not args.skip_decompose:
        print(f"[autopilot] Decomposing goal: {args.goal}")
        scripts_dir = discover_session_driver()
        response = decompose_goal(args.goal, project_dir, scripts_dir)
        print(f"[autopilot] Decomposition complete:\n{response[:500]}")

        # Ensure contracts on new tasks
        coredrift = wg_dir / "coredrift"
        if coredrift.exists():
            subprocess.run(
                [str(coredrift), "ensure-contracts", "--apply"],
                capture_output=True,
                text=True,
                cwd=str(project_dir),
            )

    # Clear previous state for fresh run
    clear_run_state(project_dir)

    # Step 2: Run autopilot loop
    run = AutopilotRun(config=config)
    print("[autopilot] Starting execution loop...")
    run = run_autopilot_loop(run)

    # Persist worker events for completed workers
    for tid, ctx in run.workers.items():
        save_worker_event(project_dir, ctx, ctx.status)

    # Save final run state
    save_run_state(project_dir, run)

    # Step 3: Milestone review — evidence-based verification
    if run.completed_tasks and not args.skip_review:
        scripts_dir = discover_session_driver()
        review = run_milestone_review(run, scripts_dir)
        review_file = (wg_dir / ".autopilot" / "milestone-review.md")
        review_file.parent.mkdir(parents=True, exist_ok=True)
        review_file.write_text(review)
        print(f"[autopilot] Milestone review saved to: {review_file}")

    # Step 4: Generate report
    report = generate_report(run)
    report_path = wg_dir / ".autopilot"
    report_path.mkdir(parents=True, exist_ok=True)
    report_file = report_path / "latest-report.md"
    report_file.write_text(report)

    print(f"\n{report}")
    print(f"Report saved to: {report_file}")

    if run.escalated_tasks:
        print("\n[autopilot] Some tasks need human judgment. Review the report above.")
        return 3

    if run.failed_tasks:
        return 1

    return 0


def main(argv: list[str] | None = None) -> int:
    p = _build_parser()
    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
