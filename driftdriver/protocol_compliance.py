# ABOUTME: Speedrift protocol compliance checker for repos.
# ABOUTME: Detects agents working outside workgraph, missing drift installs, untracked commits.
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ComplianceReport:
    compliant: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    repo: str = ""


def check_compliance(
    project_dir: Path,
    *,
    check_recent_commits: int = 0,
) -> ComplianceReport:
    """Check a repo for speedrift protocol compliance.

    Checks:
    - .workgraph/ directory exists
    - driftdriver wrappers installed (.workgraph/drifts/)
    - Recent commits reference a workgraph task (optional)
    """
    violations: list[dict[str, Any]] = []
    repo_name = project_dir.name

    wg_dir = project_dir / ".workgraph"
    if not wg_dir.is_dir():
        violations.append({
            "kind": "missing_workgraph",
            "message": "No .workgraph/ directory — repo not initialized with workgraph",
            "severity": "high",
        })
        return ComplianceReport(compliant=False, violations=violations, repo=repo_name)

    # Check driftdriver wrappers
    drifts_dir = wg_dir / "drifts"
    if not drifts_dir.is_dir() or not (drifts_dir / "check").exists():
        violations.append({
            "kind": "missing_driftdriver",
            "message": "No .workgraph/drifts/check — driftdriver not installed",
            "severity": "medium",
        })

    # Check recent commits for task references
    if check_recent_commits > 0:
        _check_commits(project_dir, check_recent_commits, violations)

    return ComplianceReport(
        compliant=len(violations) == 0,
        violations=violations,
        repo=repo_name,
    )


def _check_commits(project_dir: Path, count: int, violations: list[dict[str, Any]]) -> None:
    """Check recent git commits for task ID references."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project_dir), "log", f"-{count}", "--format=%H %s"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return

    # Pattern: task IDs typically look like "task-name" or "#123" or "wg:task-id"
    task_ref_pattern = re.compile(r"(wg:|task[:\-]|#\d+|\[[\w-]+\])", re.IGNORECASE)

    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        parts = line.split(" ", 1)
        if len(parts) < 2:
            continue
        sha, message = parts[0], parts[1]
        if not task_ref_pattern.search(message):
            violations.append({
                "kind": "untasked_commit",
                "message": f"Commit {sha[:8]} has no task reference: {message[:80]}",
                "severity": "low",
                "commit": sha,
            })
