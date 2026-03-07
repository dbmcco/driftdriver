# ABOUTME: Post-drift enforcement evaluation — determines block/warn/pass verdicts
# ABOUTME: Extracted from policy.py to separate routing decisions from enforcement actions

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from driftdriver.policy import DriftPolicy

SEVERITY_RANK = {"info": 0, "warning": 1, "error": 2, "critical": 3}


def collect_enforcement_findings(
    plugins_json: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract all findings with severity from combined plugins output.

    Walks every plugin entry in plugins_json, collects findings from
    report.findings lists, and normalises severity (defaulting to 'info'
    when absent or unrecognised).

    Only includes plugins that actually ran (ran=True).
    """
    out: list[dict[str, Any]] = []
    for _name, payload in plugins_json.items():
        if not isinstance(payload, dict):
            continue
        if not payload.get("ran"):
            continue
        report = payload.get("report")
        if not isinstance(report, dict):
            continue
        findings = report.get("findings")
        if not isinstance(findings, list):
            continue
        for finding in findings:
            if not isinstance(finding, dict):
                continue
            sev = str(finding.get("severity", "info")).strip().lower()
            if sev not in SEVERITY_RANK:
                sev = "info"
            out.append({**finding, "severity": sev})
    return out


def evaluate_enforcement(
    policy: "DriftPolicy",
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Evaluate findings against enforcement thresholds.

    Returns dict with:
      blocked: bool — True if enforcement requires blocking
      warnings: list[str] — human-readable warning messages
      exit_code: int — 0 (clean), 1 (warnings), 2 (blocked)
      counts: dict — {info: N, warning: N, error: N, critical: N}
    """
    cfg = policy.enforcement
    if not cfg.get("enabled", False):
        return {"blocked": False, "warnings": [], "exit_code": 0, "counts": {}}

    counts: dict[str, int] = {"info": 0, "warning": 0, "error": 0, "critical": 0}
    for f in findings:
        sev = str(f.get("severity", "info")).strip().lower()
        if sev not in counts:
            sev = "info"
        counts[sev] += 1

    warnings: list[str] = []
    blocked = False

    if cfg.get("block_on_critical", True) and counts["critical"] > 0:
        blocked = True
        warnings.append(f"BLOCKED: {counts['critical']} critical finding(s) require resolution")

    if cfg.get("warn_on_error", True) and counts["error"] > 0:
        warnings.append(f"WARNING: {counts['error']} error-level finding(s)")

    max_warnings = int(cfg.get("max_unresolved_warnings", 10))
    total_actionable = counts["warning"] + counts["error"] + counts["critical"]
    if total_actionable > max_warnings:
        warnings.append(
            f"WARNING: {total_actionable} unresolved findings exceed threshold of {max_warnings}"
        )

    if blocked:
        exit_code = 2
    elif warnings:
        exit_code = 1
    else:
        exit_code = 0

    return {
        "blocked": blocked,
        "warnings": warnings,
        "exit_code": exit_code,
        "counts": counts,
    }
