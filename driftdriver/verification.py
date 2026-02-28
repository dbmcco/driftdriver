# ABOUTME: Silent failure monitor - checks whether an agent actually verified its work.
# ABOUTME: Runs a suite of checks and returns a VerificationResult dataclass.

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VerificationResult:
    passed: bool
    checks: dict[str, bool]
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


def verify_task_completion(project_dir: Path, task_contract: dict) -> VerificationResult:
    """Run all verification checks and return a combined result."""
    checks: dict[str, bool] = {}
    warnings: list[str] = []
    blockers: list[str] = []

    checks["tests_ran"] = _check_tests_ran(project_dir)
    checks["diff_exists"] = _check_diff_exists(project_dir)

    todo_passed, todo_found = _check_no_todo_markers(project_dir)
    checks["no_todo_markers"] = todo_passed
    if not todo_passed:
        warnings.extend(todo_found)

    checks["contract_scope"] = _check_contract_scope(project_dir, task_contract)

    if not checks["diff_exists"]:
        blockers.append("No changes detected in git diff or recent commits")
    if not checks["contract_scope"]:
        blockers.append("Changes exceed contract scope limits")

    passed = all(checks.values())
    return VerificationResult(passed=passed, checks=checks, warnings=warnings, blockers=blockers)


def _check_tests_ran(project_dir: Path) -> bool:
    """Return True if test artifacts exist or git log shows recent test-related commits."""
    # pytest
    if (project_dir / ".pytest_cache").exists():
        return True
    # vitest
    if (project_dir / ".vitest-cache").exists():
        return True
    if (project_dir / "vitest-report.json").exists():
        return True
    # cargo nextest / cargo test
    if (project_dir / "target" / "nextest").exists():
        return True
    if (project_dir / "target" / "test-results").exists():
        return True
    # fall back to git log: any commit with "test" in message
    result = subprocess.run(
        ["git", "log", "--oneline", "-20"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if "test" in line.lower():
                return True
    return False


def _check_diff_exists(project_dir: Path) -> bool:
    """Return True if git diff HEAD is non-empty or recent commits exist beyond init."""
    result = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return True
    # Also accept if there are commits beyond the first (work was committed)
    log = subprocess.run(
        ["git", "log", "--oneline"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if log.returncode == 0 and len(log.stdout.strip().splitlines()) > 1:
        return True
    return False


def _check_no_todo_markers(project_dir: Path) -> tuple[bool, list[str]]:
    """Scan git diff for TODO/FIXME/HACK in added lines. Returns (passed, found_markers)."""
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    found: list[str] = []
    if result.returncode != 0:
        return True, found
    for line in result.stdout.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        upper = line.upper()
        for marker in ("TODO", "FIXME", "HACK"):
            if marker in upper:
                found.append(f"{marker} found: {line[1:].strip()}")
                break
    return len(found) == 0, found


def _check_contract_scope(project_dir: Path, contract: dict) -> bool:
    """Return True if diff is within contract max_files / max_loc limits."""
    max_files = contract.get("max_files")
    max_loc = contract.get("max_loc")
    if max_files is None and max_loc is None:
        return True

    result = subprocess.run(
        ["git", "diff", "--stat", "HEAD"],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return True

    lines = [l for l in result.stdout.strip().splitlines() if l.strip()]
    # Last line is summary: "N files changed, X insertions(+), Y deletions(-)"
    file_count = max(0, len(lines) - 1) if lines else 0

    if max_files is not None and file_count > int(max_files):
        return False

    if max_loc is not None:
        # Count changed lines from diff --numstat
        numstat = subprocess.run(
            ["git", "diff", "--numstat", "HEAD"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        total_loc = 0
        if numstat.returncode == 0:
            for row in numstat.stdout.splitlines():
                parts = row.split("\t")
                if len(parts) >= 2:
                    try:
                        total_loc += int(parts[0]) + int(parts[1])
                    except ValueError:
                        pass
        if total_loc > int(max_loc):
            return False

    return True
