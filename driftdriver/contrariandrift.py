# ABOUTME: Contrarian drift lane — finds real bugs, integration gaps, dead code, security issues
# ABOUTME: Runs as a speedrift module, produces drift score and structured findings
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ContrarianFinding:
    """A single finding from the contrarian code review."""

    file: str
    line: int | None
    severity: str  # CRITICAL, HIGH, MEDIUM, LOW
    category: str  # bug, integration-gap, dead-code, security
    description: str
    suggested_fix: str = ""


@dataclass
class ContrarianReport:
    """Aggregated results from a contrarian check run."""

    findings: list[ContrarianFinding] = field(default_factory=list)
    drift_score: float = 0.0  # 0.0 = clean, 1.0 = severe issues
    summary: str = ""


def check_dead_imports(project_dir: Path) -> list[ContrarianFinding]:
    """Scan Python files for functions defined but never imported by other modules."""
    py_files = list(project_dir.rglob("*.py"))

    # Collect all defined top-level function names per file
    defined: dict[str, list[str]] = {}
    for path in py_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        names = [
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")
        ]
        if names:
            defined[str(path)] = names

    # Collect all names imported across all files
    imported: set[str] = set()
    for path in py_files:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        # Match: from X import name1, name2
        for m in re.finditer(r"from\s+\S+\s+import\s+(.+)", text):
            for name in re.split(r"[,\s]+", m.group(1)):
                name = name.strip("() \n")
                if name:
                    imported.add(name)
        # Match: import X (simple import of a name)
        for m in re.finditer(r"^\s*import\s+(\w+)", text, re.MULTILINE):
            imported.add(m.group(1))

    findings: list[ContrarianFinding] = []
    for filepath, names in defined.items():
        for name in names:
            if name not in imported:
                findings.append(
                    ContrarianFinding(
                        file=filepath,
                        line=None,
                        severity="LOW",
                        category="dead-code",
                        description=f"Function '{name}' defined but never imported by other modules",
                        suggested_fix=f"Remove '{name}' or export it intentionally",
                    )
                )
    return findings


def check_json_safety(project_dir: Path) -> list[ContrarianFinding]:
    """Scan shell scripts for unsafe JSON construction via string interpolation."""
    findings: list[ContrarianFinding] = []
    # Pattern: a line with `{"` or `{\"` combined with `$` variable expansion, not using jq
    unsafe_pattern = re.compile(r'(?:\\?"|\{).*\$\w+.*(?:\\?"|})', re.IGNORECASE)

    for path in project_dir.rglob("*.sh"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            # Skip lines that use jq (safe path)
            if "jq" in line:
                continue
            if re.search(r'[{\\"]', line) and "$" in line and re.search(r'["\']?\s*\$\w+', line):
                # Check for JSON-like structure with variable interpolation
                if re.search(r'(?:\\?"|\{)[^$]*\$\w+', line):
                    findings.append(
                        ContrarianFinding(
                            file=str(path),
                            line=lineno,
                            severity="HIGH",
                            category="security",
                            description=(
                                f"Unsafe JSON construction via string interpolation in '{path.name}' "
                                f"at line {lineno}: use jq instead"
                            ),
                            suggested_fix="Use jq -n --arg key \"$VALUE\" '{\"key\": $key}' for safe JSON construction",
                        )
                    )
    return findings


def check_error_swallowing(project_dir: Path) -> list[ContrarianFinding]:
    """Scan for silently swallowed errors in Python and shell files."""
    findings: list[ContrarianFinding] = []

    # Python: except Exception: pass (or except ...: pass with nothing else)
    except_pass_re = re.compile(
        r"except\s+(?:Exception|BaseException|\w+).*:\s*\n\s*pass\b", re.MULTILINE
    )
    for path in project_dir.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in except_pass_re.finditer(text):
            lineno = text[: m.start()].count("\n") + 1
            findings.append(
                ContrarianFinding(
                    file=str(path),
                    line=lineno,
                    severity="MEDIUM",
                    category="bug",
                    description=f"Silently swallowed exception in '{path.name}' at line {lineno}",
                    suggested_fix="Log or re-raise the exception instead of passing silently",
                )
            )

    # Shell: 2>/dev/null || true or bare || true
    shell_swallow_re = re.compile(r"2>/dev/null\s*\|\|\s*true|\|\|\s*true")
    for path in list(project_dir.rglob("*.sh")):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for lineno, line in enumerate(lines, start=1):
            if shell_swallow_re.search(line):
                findings.append(
                    ContrarianFinding(
                        file=str(path),
                        line=lineno,
                        severity="LOW",
                        category="bug",
                        description=(
                            f"Error silenced with '|| true' in '{path.name}' at line {lineno}"
                        ),
                        suggested_fix="Handle the error explicitly or add a comment explaining why suppression is intentional",
                    )
                )

    return findings


_SEVERITY_WEIGHTS: dict[str, float] = {
    "CRITICAL": 0.3,
    "HIGH": 0.2,
    "MEDIUM": 0.1,
    "LOW": 0.05,
}

_SEVERITY_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


def run_contrarian_check(project_dir: Path) -> ContrarianReport:
    """Run all contrarian checks and aggregate into a single report."""
    all_findings: list[ContrarianFinding] = []
    all_findings.extend(check_dead_imports(project_dir))
    all_findings.extend(check_json_safety(project_dir))
    all_findings.extend(check_error_swallowing(project_dir))

    score = sum(_SEVERITY_WEIGHTS.get(f.severity, 0.05) for f in all_findings)
    score = min(score, 1.0)

    counts = {sev: sum(1 for f in all_findings if f.severity == sev) for sev in _SEVERITY_ORDER}
    parts = [f"{c} {s}" for s, c in counts.items() if c > 0]
    summary = f"{len(all_findings)} finding(s): {', '.join(parts)}" if parts else "No issues found"

    return ContrarianReport(findings=all_findings, drift_score=score, summary=summary)


def format_report(report: ContrarianReport) -> str:
    """Format a ContrarianReport as a human-readable string grouped by severity."""
    if not report.findings:
        return f"Contrarian Drift: CLEAN (score={report.drift_score:.2f})\n{report.summary}\n"

    by_severity: dict[str, list[ContrarianFinding]] = {s: [] for s in _SEVERITY_ORDER}
    for finding in report.findings:
        bucket = by_severity.get(finding.severity)
        if bucket is not None:
            bucket.append(finding)
        else:
            by_severity.setdefault(finding.severity, []).append(finding)

    lines: list[str] = [
        f"Contrarian Drift Report (score={report.drift_score:.2f})",
        f"Summary: {report.summary}",
        "",
    ]

    for severity in _SEVERITY_ORDER:
        group = by_severity.get(severity, [])
        if not group:
            continue
        lines.append(f"[{severity}]")
        for f in group:
            loc = f":{f.line}" if f.line is not None else ""
            lines.append(f"  {f.file}{loc}  [{f.category}]  {f.description}")
            if f.suggested_fix:
                lines.append(f"    Fix: {f.suggested_fix}")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    """CLI entry point: run contrarian check on the given directory."""
    import sys

    project_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    report = run_contrarian_check(project_dir)
    print(format_report(report))
    sys.exit(0 if report.drift_score == 0.0 else 1)


if __name__ == "__main__":
    main()
