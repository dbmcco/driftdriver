# ABOUTME: QA drift lane — evaluates test quality, coverage gaps, and testing practices
# ABOUTME: Runs as a speedrift module, produces drift score and structured findings
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class QAFinding:
    file: str
    category: str  # coverage-gap, false-confidence, mock-violation, missing-integration
    severity: str  # HIGH, MEDIUM, LOW
    description: str


@dataclass
class QAReport:
    findings: list[QAFinding] = field(default_factory=list)
    drift_score: float = 0.0
    modules_tested: int = 0
    modules_untested: int = 0
    mock_count: int = 0
    summary: str = ""


_MOCK_PATTERNS = re.compile(
    r"unittest\.mock|@patch\b|MagicMock|jest\.mock|vi\.mock"
)
_FALSE_CONFIDENCE_IMPORT = re.compile(r"^\s*import\s+\w+", re.MULTILINE)
_FALSE_CONFIDENCE_EXISTS = re.compile(r"os\.path\.exists")
_FALSE_CONFIDENCE_WEAK = re.compile(r"assert\s+True\b|assert\s+\w+\s+is\s+not\s+None\b")
_SUBPROCESS_PATTERN = re.compile(r"\bsubprocess\b")
_FILE_IO_PATTERN = re.compile(r"\bopen\s*\(|Path\s*\(.*\)\s*\.\s*(?:read|write|open)")
_TEMPDIR_PATTERN = re.compile(r"tempfile|tmp_path|TemporaryDirectory")


def _collect_test_files(project_dir: Path) -> list[Path]:
    """Find all test_*.py files under tests/ and src/."""
    test_files: list[Path] = []
    for glob in ("tests/**/test_*.py", "test_*.py", "src/**/test_*.py"):
        test_files.extend(project_dir.glob(glob))
    return test_files


def find_untested_modules(project_dir: Path) -> list[QAFinding]:
    """Find .py source files without corresponding test_*.py files."""
    findings: list[QAFinding] = []

    src_dir = project_dir / "src"
    if not src_dir.exists():
        return findings

    test_files = _collect_test_files(project_dir)
    tested_names: set[str] = set()
    for tf in test_files:
        name = tf.stem  # e.g. "test_mymodule"
        if name.startswith("test_"):
            tested_names.add(name[5:])  # strip "test_"

    for py_file in src_dir.rglob("*.py"):
        if py_file.name.startswith("_"):
            continue
        module_name = py_file.stem
        if module_name not in tested_names:
            findings.append(
                QAFinding(
                    file=str(py_file.relative_to(project_dir)),
                    category="coverage-gap",
                    severity="HIGH",
                    description=f"No test file found for module '{module_name}'",
                )
            )

    # TypeScript: .ts files in src/ without corresponding .test.ts
    for ts_file in src_dir.rglob("*.ts"):
        if ts_file.name.endswith(".test.ts") or ts_file.name.endswith(".d.ts"):
            continue
        stem = ts_file.stem
        test_candidates = list(src_dir.rglob(f"{stem}.test.ts"))
        if not test_candidates:
            findings.append(
                QAFinding(
                    file=str(ts_file.relative_to(project_dir)),
                    category="coverage-gap",
                    severity="HIGH",
                    description=f"No .test.ts found for '{stem}.ts'",
                )
            )

    return findings


def check_mock_usage(project_dir: Path) -> list[QAFinding]:
    """Scan test files for mock imports that violate no-mock policy."""
    findings: list[QAFinding] = []

    test_files = _collect_test_files(project_dir)
    for tf in test_files:
        try:
            content = tf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _MOCK_PATTERNS.search(content):
            findings.append(
                QAFinding(
                    file=str(tf.relative_to(project_dir) if tf.is_relative_to(project_dir) else tf),
                    category="mock-violation",
                    severity="MEDIUM",
                    description="Test file uses mocking (unittest.mock/@patch/MagicMock/jest.mock/vi.mock)",
                )
            )

    return findings


def check_false_confidence(project_dir: Path) -> list[QAFinding]:
    """Detect tests that provide false confidence: import-only, existence, or weak assertions."""
    findings: list[QAFinding] = []

    test_files = _collect_test_files(project_dir)
    for tf in test_files:
        try:
            content = tf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        has_import_only = bool(_FALSE_CONFIDENCE_IMPORT.search(content))
        has_weak = bool(_FALSE_CONFIDENCE_WEAK.search(content))
        has_exists = bool(_FALSE_CONFIDENCE_EXISTS.search(content))

        if has_weak or (has_import_only and not has_weak and "assert" not in content.replace("assert True", "").replace("assert result is not None", "")):
            severity = "MEDIUM"
            desc = "Test uses weak assertions (assert True / assert result is not None)"
            if has_weak:
                findings.append(
                    QAFinding(
                        file=str(tf.relative_to(project_dir) if tf.is_relative_to(project_dir) else tf),
                        category="false-confidence",
                        severity=severity,
                        description=desc,
                    )
                )

        # import-only check: file has import statements but assertions only check imports
        if has_import_only and "assert" not in content:
            findings.append(
                QAFinding(
                    file=str(tf.relative_to(project_dir) if tf.is_relative_to(project_dir) else tf),
                    category="false-confidence",
                    severity="MEDIUM",
                    description="Test file only checks imports with no real assertions",
                )
            )

        if has_exists:
            findings.append(
                QAFinding(
                    file=str(tf.relative_to(project_dir) if tf.is_relative_to(project_dir) else tf),
                    category="false-confidence",
                    severity="LOW",
                    description="Test uses os.path.exists checks (tests file existence, not behavior)",
                )
            )

    return findings


def check_integration_coverage(project_dir: Path) -> list[QAFinding]:
    """Flag source files that use subprocess/file I/O without integration tests."""
    findings: list[QAFinding] = []

    src_dir = project_dir / "src"
    if not src_dir.exists():
        return findings

    test_files = _collect_test_files(project_dir)
    has_tempdir_in_tests = any(
        _TEMPDIR_PATTERN.search(tf.read_text(encoding="utf-8", errors="replace"))
        for tf in test_files
        if tf.exists()
    )

    for src_file in src_dir.rglob("*.py"):
        try:
            content = src_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        if _SUBPROCESS_PATTERN.search(content):
            # Check for any integration test that exercises this module
            module_name = src_file.stem
            tested = any(
                module_name in tf.read_text(encoding="utf-8", errors="replace")
                for tf in test_files
                if tf.exists()
            )
            if not tested:
                findings.append(
                    QAFinding(
                        file=str(src_file.relative_to(project_dir)),
                        category="missing-integration",
                        severity="HIGH",
                        description=f"'{src_file.name}' calls subprocess but has no integration test",
                    )
                )

        if _FILE_IO_PATTERN.search(content) and not has_tempdir_in_tests:
            findings.append(
                QAFinding(
                    file=str(src_file.relative_to(project_dir)),
                    category="missing-integration",
                    severity="MEDIUM",
                    description=f"'{src_file.name}' uses file I/O but no tests use temp directories",
                )
            )

    return findings


_SEVERITY_SCORE = {"HIGH": 0.2, "MEDIUM": 0.1, "LOW": 0.05}


def run_qa_check(project_dir: Path) -> QAReport:
    """Run all QA checks and return a QAReport with drift score."""
    findings: list[QAFinding] = []
    findings.extend(find_untested_modules(project_dir))
    findings.extend(check_mock_usage(project_dir))
    findings.extend(check_false_confidence(project_dir))
    findings.extend(check_integration_coverage(project_dir))

    score = min(1.0, sum(_SEVERITY_SCORE.get(f.severity, 0.0) for f in findings))

    src_dir = project_dir / "src"
    all_py = list(src_dir.rglob("*.py")) if src_dir.exists() else []
    untested_files = {f.file for f in findings if f.category == "coverage-gap"}
    modules_untested = len(untested_files)
    modules_tested = max(0, len(all_py) - modules_untested)

    mock_count = sum(1 for f in findings if f.category == "mock-violation")

    high = sum(1 for f in findings if f.severity == "HIGH")
    medium = sum(1 for f in findings if f.severity == "MEDIUM")
    low = sum(1 for f in findings if f.severity == "LOW")
    summary = f"{len(findings)} findings: {high} HIGH, {medium} MEDIUM, {low} LOW"

    return QAReport(
        findings=findings,
        drift_score=score,
        modules_tested=modules_tested,
        modules_untested=modules_untested,
        mock_count=mock_count,
        summary=summary,
    )


def format_report(report: QAReport) -> str:
    """Format a QAReport as a human-readable string grouped by category."""
    lines: list[str] = []
    lines.append(f"QA Drift Report — score: {report.drift_score:.2f}")
    lines.append(f"Modules tested: {report.modules_tested}  untested: {report.modules_untested}")
    lines.append(f"Mock violations: {report.mock_count}")
    lines.append(f"Summary: {report.summary}")
    lines.append("")

    by_category: dict[str, list[QAFinding]] = {}
    for finding in report.findings:
        by_category.setdefault(finding.category, []).append(finding)

    for category, items in sorted(by_category.items()):
        lines.append(f"[{category}]")
        for item in items:
            lines.append(f"  [{item.severity}] {item.file}: {item.description}")
        lines.append("")

    return "\n".join(lines)
