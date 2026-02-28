# ABOUTME: File scope enforcement — verifies agent changes stay within declared scope
# ABOUTME: Compares git diff against task contract's allowed files/paths

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
import subprocess
import fnmatch


@dataclass
class ScopeViolation:
    """A file changed outside the declared scope."""
    file_path: str
    change_type: str  # "added", "modified", "deleted"


@dataclass
class ScopeResult:
    """Result of scope enforcement check."""
    in_scope: bool
    violations: list[ScopeViolation] = field(default_factory=list)
    checked_files: int = 0
    allowed_patterns: list[str] = field(default_factory=list)


def get_changed_files(project_dir: Path, base_ref: str = "HEAD~1") -> list[tuple[str, str]]:
    """Get list of (status, path) tuples from git diff."""
    result = subprocess.run(
        ["git", "diff", "--name-status", base_ref, "HEAD"],
        capture_output=True, text=True, cwd=str(project_dir)
    )
    if result.returncode != 0:
        return []
    changes = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            status = parts[0][0]  # M, A, D, R, etc.
            path = parts[-1]  # last part (handles renames)
            change_type = {"M": "modified", "A": "added", "D": "deleted"}.get(status, "modified")
            changes.append((change_type, path))
    return changes


def check_file_scope(
    changed_files: list[tuple[str, str]],
    allowed_patterns: list[str],
) -> ScopeResult:
    """Check if all changed files are within the allowed scope."""
    if not allowed_patterns:
        return ScopeResult(in_scope=True, checked_files=len(changed_files))

    violations = []
    for change_type, file_path in changed_files:
        if not _matches_any_pattern(file_path, allowed_patterns):
            violations.append(ScopeViolation(file_path=file_path, change_type=change_type))

    return ScopeResult(
        in_scope=len(violations) == 0,
        violations=violations,
        checked_files=len(changed_files),
        allowed_patterns=allowed_patterns,
    )


def _matches_any_pattern(file_path: str, patterns: list[str]) -> bool:
    """Check if a file path matches any of the allowed patterns."""
    for pattern in patterns:
        # PurePosixPath.match supports ** for recursive matching
        if PurePosixPath(file_path).match(pattern):
            return True
        if fnmatch.fnmatch(file_path, pattern):
            return True
        # Also check if the pattern is a prefix (directory scope)
        if file_path.startswith(pattern.rstrip("/*") + "/"):
            return True
        if file_path == pattern:
            return True
    return False


def extract_scope_from_contract(contract: dict) -> list[str]:
    """Extract allowed file patterns from a task contract."""
    patterns = []
    if "allowed_paths" in contract:
        patterns.extend(contract["allowed_paths"])
    if "allowed_files" in contract:
        patterns.extend(contract["allowed_files"])
    if "scope" in contract and isinstance(contract["scope"], list):
        patterns.extend(contract["scope"])
    return patterns


def format_scope_report(result: ScopeResult) -> str:
    """Format scope check result as a report."""
    if result.in_scope:
        return f"Scope check PASSED: {result.checked_files} files, all within scope"
    lines = [f"Scope check FAILED: {len(result.violations)} out-of-scope changes"]
    for v in result.violations:
        lines.append(f"  [{v.change_type}] {v.file_path}")
    if result.allowed_patterns:
        lines.append(f"  Allowed: {', '.join(result.allowed_patterns)}")
    return "\n".join(lines)
