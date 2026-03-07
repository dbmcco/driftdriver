# ABOUTME: Shared helper functions for driftdriver CLI subpackage.
# ABOUTME: Update preflight, contract ensure, loop safety, findings normalization.

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftdriver.health import (
    compute_scoreboard,
    detect_cycle_from,
    has_contract,
    is_active,
    rank_ready_drift_queue,
    redrift_depth,
)
from driftdriver.updates import (
    ECOSYSTEM_REPOS,
    check_ecosystem_updates,
    load_review_config,
    summarize_updates,
)
from driftdriver.workgraph import load_workgraph


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
    from driftdriver.drift_task_guard import guarded_add_drift_task

    followup_id = f"drift-self-update-{task_id}"
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
    guarded_add_drift_task(
        wg_dir=wg_dir,
        task_id=followup_id,
        title=f"self-update decision: {task_id}",
        description=desc,
        lane_tag="updates",
        after=task_id,
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
