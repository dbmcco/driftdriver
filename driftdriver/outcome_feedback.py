# ABOUTME: Outcome feedback loop: compares pre vs post drift check findings to auto-record outcomes.
# ABOUTME: Bridges the gap between drift recommendations and what actually happened after agent action.

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from driftdriver.outcome import DriftOutcome, write_outcome

SEVERITY_ORDER = ("info", "warning", "error", "critical")


def _severity_rank(severity: str) -> int:
    """Return numeric rank for severity. Unknown values map to info (0)."""
    s = str(severity or "").strip().lower()
    try:
        return SEVERITY_ORDER.index(s)
    except ValueError:
        return 0


def _sanitize_task_id(task_id: str) -> str:
    """Sanitize a task ID for use as a filename component."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(task_id))


def extract_findings_from_check(check_json: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract structured findings from a driftdriver check JSON result.

    Returns a flat list of finding dicts, each annotated with 'lane'.
    """
    plugins = check_json.get("plugins")
    if not isinstance(plugins, dict):
        return []

    findings: list[dict[str, Any]] = []
    for lane, plugin_data in plugins.items():
        if not isinstance(plugin_data, dict):
            continue
        if not plugin_data.get("ran"):
            continue
        report = plugin_data.get("report")
        if not isinstance(report, dict):
            continue
        raw_findings = report.get("findings")
        if not isinstance(raw_findings, list):
            continue
        for finding in raw_findings:
            if not isinstance(finding, dict):
                continue
            kind = str(finding.get("kind") or "").strip()
            if not kind:
                continue
            entry = dict(finding)
            entry["lane"] = lane
            findings.append(entry)
    return findings


def classify_finding_outcome(
    pre_finding: dict[str, Any],
    post_findings: list[dict[str, Any]],
) -> str:
    """Classify the outcome of a single pre-task finding given post-task findings.

    Returns one of: resolved, ignored, worsened.
    """
    kind = str(pre_finding.get("kind") or "").strip()
    if not kind:
        return "resolved"

    lane = str(pre_finding.get("lane") or "")
    pre_severity = str(pre_finding.get("severity") or "")

    # Look for same kind+lane in post findings
    for post in post_findings:
        post_kind = str(post.get("kind") or "").strip()
        post_lane = str(post.get("lane") or "")
        if post_kind == kind and post_lane == lane:
            # Finding persists. Check if severity escalated.
            post_severity = str(post.get("severity") or "")
            if pre_severity and post_severity:
                if _severity_rank(post_severity) > _severity_rank(pre_severity):
                    return "worsened"
            return "ignored"

    return "resolved"


def record_outcomes_from_check(
    *,
    project_dir: Path,
    task_id: str,
    pre_check: dict[str, Any],
    post_check: dict[str, Any],
    actor_id: str = "",
) -> list[dict[str, str]]:
    """Compare pre-task and post-task check findings, record an outcome for each pre finding.

    Returns a list of result dicts with keys: lane, finding_key, outcome, recommendation, action_taken.
    """
    pre_findings = extract_findings_from_check(pre_check)
    if not pre_findings:
        return []

    post_findings = extract_findings_from_check(post_check)

    ledger = Path(project_dir) / ".workgraph" / "drift-outcomes.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, str]] = []
    for pre_f in pre_findings:
        kind = str(pre_f.get("kind") or "").strip()
        lane = str(pre_f.get("lane") or "")
        message = str(pre_f.get("message") or kind)

        outcome_value = classify_finding_outcome(pre_f, post_findings)

        drift_outcome = DriftOutcome(
            task_id=str(task_id),
            lane=lane,
            finding_key=kind,
            recommendation=message,
            action_taken=f"task-completing ({outcome_value})",
            outcome=outcome_value,
            evidence=[f"pre-check finding: {kind}", f"post-check comparison"],
            actor_id=actor_id,
        )
        write_outcome(ledger, drift_outcome)

        results.append({
            "lane": lane,
            "finding_key": kind,
            "outcome": outcome_value,
            "recommendation": message,
            "action_taken": drift_outcome.action_taken,
        })

    return results


def save_check_snapshot(
    wg_dir: Path,
    task_id: str,
    check_data: dict[str, Any],
) -> Path:
    """Save a check JSON snapshot for later comparison at task-completing time.

    Stored in .workgraph/check-snapshots/<sanitized_task_id>.json.
    Returns the path to the saved file.
    """
    snapshots_dir = wg_dir / "check-snapshots"
    snapshots_dir.mkdir(parents=True, exist_ok=True)

    filename = _sanitize_task_id(task_id) + ".json"
    snapshot_path = snapshots_dir / filename

    tmp = snapshot_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(check_data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    tmp.replace(snapshot_path)

    return snapshot_path


def load_check_snapshot(
    wg_dir: Path,
    task_id: str,
) -> dict[str, Any] | None:
    """Load a previously saved check snapshot for a task.

    Returns None if no snapshot exists.
    """
    snapshots_dir = wg_dir / "check-snapshots"
    filename = _sanitize_task_id(task_id) + ".json"
    snapshot_path = snapshots_dir / filename

    if not snapshot_path.exists():
        return None

    try:
        return json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
