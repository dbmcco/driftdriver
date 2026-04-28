# ABOUTME: Drift-to-Evaluation Bridge — translates Speedrift drift lane findings
# ABOUTME: into WG evaluation records via the wg evaluate --submit CLI.

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from speedrift_lane_sdk.lane_contract import LaneFinding, LaneResult

SEVERITY_SCORES: dict[str, float] = {
    "critical": 0.0,
    "error": 0.2,
    "warning": 0.5,
    "info": 0.8,
}

# Severity ordering for min_severity filtering (lower index = more severe).
_SEVERITY_ORDER = ("critical", "error", "warning", "info")

ALL_DIMENSIONS: tuple[str, ...] = (
    "correctness",
    "completeness",
    "style_adherence",
    "downstream_usability",
    "coordination_overhead",
    "blocking_impact",
    "efficiency",
    "strategic_alignment",
)

LANE_DIMENSION_MAP: dict[str, dict] = {
    "coredrift": {
        "primary": "correctness",
        "secondary": ["completeness", "style_adherence"],
    },
    "qadrift": {
        "primary": "completeness",
        "secondary": ["correctness", "downstream_usability"],
    },
    "plandrift": {
        "primary": "strategic_alignment",
        "secondary": ["coordination_overhead", "blocking_impact"],
    },
    "secdrift": {
        "primary": "correctness",
        "secondary": ["downstream_usability", "blocking_impact"],
    },
    "northstardrift": {
        "primary": "strategic_alignment",
        "secondary": ["efficiency", "completeness"],
    },
    "factorydrift": {
        "primary": "coordination_overhead",
        "secondary": ["efficiency", "blocking_impact"],
    },
}


@dataclass
class BridgeReport:
    """Summary of a bridge run."""

    evaluations_written: int = 0
    unattributable_findings: int = 0
    attribution_failures: list[str] = field(default_factory=list)
    evaluation_ids: list[str] = field(default_factory=list)


def severity_to_score(severity: str) -> float:
    """Map a severity string to a numeric score. Unknown severities default to 0.5."""
    return SEVERITY_SCORES.get(severity, 0.5)


def _parse_simple_yaml(text: str) -> dict[str, str]:
    """Parse trivial key: value YAML (no nesting, no lists)."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, _, value = line.partition(":")
            result[key.strip()] = value.strip()
    return result


def attribute_finding(repo_path: Path, finding: LaneFinding) -> dict | None:
    """Extract task attribution from a finding's tags and load the assignment.

    Looks for a tag matching ``task:<id>`` in the finding's tag list, then
    loads ``.workgraph/agency/assignments/<task_id>.yaml`` to resolve
    agent_id and role_id (composition_id).  Returns None when no task tag
    exists or the assignment file is missing.
    """
    task_id: str | None = None
    for tag in finding.tags:
        if tag.startswith("task:"):
            task_id = tag[5:]
            break

    if task_id is None:
        return None

    assignment_path = (
        repo_path / ".workgraph" / "agency" / "assignments" / f"{task_id}.yaml"
    )
    if not assignment_path.exists():
        return None

    data = _parse_simple_yaml(assignment_path.read_text())

    return {
        "task_id": data.get("task_id", task_id),
        "role_id": data.get("composition_id", "unknown"),
        "agent_id": data.get("agent_id", "unknown"),
    }


def build_evaluation(
    finding: LaneFinding,
    attribution: dict,
    *,
    lane: str,
) -> dict:
    """Build a WG evaluation JSON dict from a finding and its attribution."""
    task_id = attribution["task_id"]
    role_id = attribution["role_id"]
    timestamp_ns = int(time.time() * 1_000_000)
    eval_id = f"eval-drift-{lane}-{task_id}-{timestamp_ns}"

    score_val = severity_to_score(finding.severity)

    # Build dimensions: primary dimension gets the raw severity score,
    # secondary dimensions get a blended score (halfway to 1.0).
    mapping = LANE_DIMENSION_MAP.get(lane, {"primary": "correctness", "secondary": []})
    dimensions: dict[str, float] = {}
    dimensions[mapping["primary"]] = score_val
    for sec in mapping["secondary"]:
        dimensions[sec] = round((score_val + 1.0) / 2.0, 4)

    avg_score = round(sum(dimensions.values()) / len(dimensions), 4) if dimensions else score_val

    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()

    return {
        "id": eval_id,
        "task_id": task_id,
        "role_id": role_id,
        "tradeoff_id": "unknown",
        "score": avg_score,
        "dimensions": dimensions,
        "notes": f"{lane}: {finding.message}",
        "evaluator": f"speedrift:{lane}",
        "timestamp": ts,
        "source": "drift",
    }


def write_evaluation(repo_path: Path, evaluation: dict) -> Path:
    """Submit an evaluation dict to the wg evaluate record CLI.

    Uses `wg evaluate record` with explicit fields. This avoids direct
    .workgraph/agency/evaluations/ file writes,
    keeping the bridge compatible with future wg storage backends.
    """
    wg_dir = repo_path / ".workgraph"
    cmd = [
        "wg",
        "--dir",
        str(wg_dir),
        "--json",
        "evaluate",
        "record",
        "--task",
        str(evaluation.get("task_id") or ""),
        "--score",
        str(evaluation.get("score") or 0.0),
        "--source",
        str(evaluation.get("evaluator") or evaluation.get("source") or "speedrift:unknown"),
    ]
    if evaluation.get("notes"):
        cmd += ["--notes", str(evaluation["notes"])]
    dimensions = evaluation.get("dimensions")
    if isinstance(dimensions, dict):
        for name, score in dimensions.items():
            cmd += ["--dim", f"{name}={score}"]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(repo_path),
        timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"wg evaluate record failed (exit {result.returncode}): {result.stderr.strip()}"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"wg evaluate record returned invalid JSON: {result.stdout!r}") from exc
    path = data.get("path")
    if not path:
        raise RuntimeError(f"wg evaluate record did not return an evaluation path: {result.stdout!r}")
    return Path(str(path))


def bridge_findings_to_evaluations(
    repo_path: Path,
    lane_results: list[LaneResult],
    *,
    min_severity: str = "info",
) -> BridgeReport:
    """Main entry point: iterate findings, attribute, build, write evaluations."""
    report = BridgeReport()

    # Determine the severity cutoff index.
    try:
        cutoff = _SEVERITY_ORDER.index(min_severity)
    except ValueError:
        cutoff = len(_SEVERITY_ORDER) - 1  # accept everything

    for result in lane_results:
        lane = result.lane
        for finding in result.findings:
            # Severity filter: skip findings less severe than the cutoff.
            try:
                sev_idx = _SEVERITY_ORDER.index(finding.severity)
            except ValueError:
                sev_idx = len(_SEVERITY_ORDER)  # unknown = least severe

            if sev_idx > cutoff:
                continue

            # Check if finding has a task tag at all.
            has_task_tag = any(t.startswith("task:") for t in finding.tags)
            if not has_task_tag:
                report.unattributable_findings += 1
                continue

            attribution = attribute_finding(repo_path, finding)
            if attribution is None:
                # Has a task tag but the assignment file is missing.
                task_tag = next(t for t in finding.tags if t.startswith("task:"))
                report.attribution_failures.append(task_tag)
                continue

            evaluation = build_evaluation(finding, attribution, lane=lane)
            path = write_evaluation(repo_path, evaluation)
            report.evaluations_written += 1
            report.evaluation_ids.append(evaluation["id"])

    return report
