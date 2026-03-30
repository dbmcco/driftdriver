# ABOUTME: Deterministic daily factory report builder — no LLM generation.
# ABOUTME: Queries finding-ledger.jsonl, wg list done, llm-spend.jsonl, and gate-log.jsonl.

from __future__ import annotations

import json
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


_DEFAULT_TAIL_HOURS = 24.0
_GATE_LOG_NAME = "gate-log.jsonl"
_FINDING_LEDGER_NAME = "finding-ledger.jsonl"
_LLM_SPEND_NAME = "llm-spend.jsonl"


def _read_jsonl_window(path: Path, cutoff: float) -> list[dict[str, Any]]:
    """Read a JSONL file and return entries with ts >= cutoff."""
    if not path.exists():
        return []
    results: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("ts", 0) >= cutoff:
            results.append(rec)
    return results


def _query_findings(wg_dir: Path, cutoff: float) -> list[dict[str, Any]]:
    """Return finding-ledger entries from the last window."""
    return _read_jsonl_window(wg_dir / _FINDING_LEDGER_NAME, cutoff)


def _query_completed_drift_tasks(project_dir: Path) -> list[dict[str, Any]]:
    """Call wg list --status done --json and filter for drift: task IDs."""
    try:
        result = subprocess.run(
            ["wg", "list", "--status", "done", "--json"],
            capture_output=True,
            text=True,
            cwd=str(project_dir),
            timeout=30,
        )
        if result.returncode != 0:
            return []
        tasks: list[dict[str, Any]] = json.loads(result.stdout)
        if not isinstance(tasks, list):
            return []
        return [t for t in tasks if str(t.get("id", "")).startswith("drift:")]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        return []


def _query_llm_spend(wg_dir: Path, tail_hours: float) -> dict[str, Any]:
    """Query llm-spend.jsonl and return summary with per-agent breakdown."""
    from driftdriver.llm_meter import query_spend
    log_path = wg_dir / _LLM_SPEND_NAME
    return query_spend(log_path=log_path, tail_hours=tail_hours, by_agent=True)


def _query_gated_calls(wg_dir: Path, cutoff: float) -> dict[str, int]:
    """Return per-agent count of skipped (gated) calls from gate-log.jsonl.

    Returns empty dict if the gate-log file doesn't exist yet (signal gates
    not yet deployed).
    """
    entries = _read_jsonl_window(wg_dir / _GATE_LOG_NAME, cutoff)
    skipped: dict[str, int] = {}
    for e in entries:
        if e.get("skipped") or e.get("reason") == "no_signal" or not e.get("fired", True):
            agent = str(e.get("agent", "unknown"))
            skipped[agent] = skipped.get(agent, 0) + 1
    return skipped


def _summarize_findings(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate findings by lane and task_id creation outcome."""
    by_lane: dict[str, int] = {}
    task_created = 0
    task_skipped = 0
    task_failed = 0
    for f in findings:
        lane = str(f.get("lane", "unknown"))
        by_lane[lane] = by_lane.get(lane, 0) + 1
        created = f.get("wg_add_succeeded")
        if created is True:
            task_created += 1
        elif created is False:
            task_failed += 1
        else:
            task_skipped += 1
    return {
        "total": len(findings),
        "by_lane": by_lane,
        "task_created": task_created,
        "task_failed": task_failed,
        "task_skipped_dedup": task_skipped,
    }


def build_factory_report(
    project_dir: Path,
    *,
    tail_hours: float = _DEFAULT_TAIL_HOURS,
) -> dict[str, Any]:
    """Build and return the daily factory report dict. No LLM calls."""
    now = time.time()
    cutoff = now - (tail_hours * 3600)
    wg_dir = project_dir / ".workgraph"

    findings = _query_findings(wg_dir, cutoff)
    completed_tasks = _query_completed_drift_tasks(project_dir)
    spend = _query_llm_spend(wg_dir, tail_hours)
    gated = _query_gated_calls(wg_dir, cutoff)

    report_date = date.today().isoformat()
    generated_at = datetime.now(timezone.utc).isoformat()

    return {
        "schema": 1,
        "report_date": report_date,
        "generated_at": generated_at,
        "tail_hours": tail_hours,
        "findings": {
            "summary": _summarize_findings(findings),
            "entries": findings,
        },
        "completed_drift_tasks": completed_tasks,
        "llm_spend": {
            "total_cost_usd": spend.get("total_cost_usd", 0),
            "total_input_tokens": spend.get("total_input_tokens", 0),
            "total_output_tokens": spend.get("total_output_tokens", 0),
            "call_count": len(spend.get("records", [])),
            "by_agent": spend.get("by_agent", {}),
        },
        "gated_calls": {
            "by_agent": gated,
            "total_skipped": sum(gated.values()),
            "note": "gate-log.jsonl not yet present" if not (project_dir / ".workgraph" / _GATE_LOG_NAME).exists() else "",
        },
    }


def write_factory_daily_report(
    project_dir: Path,
    report: dict[str, Any],
) -> Path:
    """Write report to .workgraph/factory-daily-{date}.json and return path."""
    wg_dir = project_dir / ".workgraph"
    wg_dir.mkdir(parents=True, exist_ok=True)
    report_date = report.get("report_date") or date.today().isoformat()
    out_path = wg_dir / f"factory-daily-{report_date}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out_path


def send_factory_report_notification(report: dict[str, Any], report_path: Path) -> bool:
    """Send Telegram notification about the written daily report. Returns True on success."""
    try:
        from driftdriver.factory_brain.telegram import load_telegram_config, send_telegram
    except ImportError:
        return False

    cfg = load_telegram_config()
    if not cfg:
        return False

    findings_total = report.get("findings", {}).get("summary", {}).get("total", 0)
    completed_total = len(report.get("completed_drift_tasks", []))
    cost_usd = report.get("llm_spend", {}).get("total_cost_usd", 0)
    gated_total = report.get("gated_calls", {}).get("total_skipped", 0)
    report_date = report.get("report_date", "")

    msg = (
        f"*Daily Factory Report — {report_date}*\n\n"
        f"Findings produced: {findings_total}\n"
        f"Drift tasks completed: {completed_total}\n"
        f"LLM spend: ${cost_usd:.4f}\n"
        f"Gated calls (skipped): {gated_total}\n\n"
        f"Written to: `{report_path.name}`"
    )
    return send_telegram(bot_token=cfg["bot_token"], chat_id=cfg["chat_id"], message=msg)


def list_factory_daily_reports(project_dir: Path) -> list[dict[str, Any]]:
    """Return metadata for all factory-daily-*.json files, newest first."""
    wg_dir = project_dir / ".workgraph"
    if not wg_dir.exists():
        return []
    reports = []
    for path in sorted(wg_dir.glob("factory-daily-*.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            reports.append({
                "report_date": data.get("report_date"),
                "generated_at": data.get("generated_at"),
                "tail_hours": data.get("tail_hours", 24),
                "findings_total": data.get("findings", {}).get("summary", {}).get("total", 0),
                "completed_drift_tasks": len(data.get("completed_drift_tasks", [])),
                "llm_cost_usd": data.get("llm_spend", {}).get("total_cost_usd", 0),
                "gated_total": data.get("gated_calls", {}).get("total_skipped", 0),
                "path": path.name,
            })
        except (json.JSONDecodeError, OSError):
            continue
    return reports
