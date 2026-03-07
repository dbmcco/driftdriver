# ABOUTME: QA drift lane — evaluates test quality, coverage gaps, and testing practices
# ABOUTME: Runs as a speedrift module, produces drift score and structured findings
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any


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


_PROGRAM_SEVERITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(parts: list[str]) -> str:
    key = "|".join(str(part or "").strip().lower() for part in parts)
    return sha1(key.encode("utf-8")).hexdigest()  # noqa: S324 - non-crypto identity hash


def _quality_prompt(repo_name: str, finding: dict[str, Any]) -> str:
    fingerprint = str(finding.get("fingerprint") or "")
    category = str(finding.get("category") or "quality-finding")
    severity = str(finding.get("severity") or "medium")
    evidence = str(finding.get("evidence") or "")
    recommendation = str(finding.get("recommendation") or "")
    return (
        f"In `{repo_name}`, triage qadrift finding `{fingerprint}` ({severity}/{category}). "
        f"Evidence: {evidence}. Determine root cause, smallest safe remediation, validation plan, "
        f"and exact Workgraph task/dependency updates. Recommendation seed: {recommendation}"
    )


def _normalize_program_cfg(policy_cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(policy_cfg) if isinstance(policy_cfg, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "max_findings_per_repo": max(1, int(raw.get("max_findings_per_repo", 40))),
        "emit_review_tasks": bool(raw.get("emit_review_tasks", True)),
        "max_review_tasks_per_repo": max(1, int(raw.get("max_review_tasks_per_repo", 3))),
        "include_playwright": bool(raw.get("include_playwright", True)),
        "include_test_health": bool(raw.get("include_test_health", True)),
        "include_workgraph_health": bool(raw.get("include_workgraph_health", True)),
    }


def _looks_like_web_repo(repo_path: Path) -> bool:
    if not (repo_path / "package.json").exists():
        return False
    for marker in ("next.config.js", "next.config.mjs", "vite.config.ts", "playwright.config.ts", "playwright.config.js"):
        if (repo_path / marker).exists():
            return True
    src_dir = repo_path / "src"
    if src_dir.exists():
        for suffix in (".tsx", ".jsx", ".vue", ".svelte"):
            if any(src_dir.rglob(f"*{suffix}")):
                return True
    return False


def run_program_quality_scan(
    *,
    repo_name: str,
    repo_path: Path,
    repo_snapshot: dict[str, Any] | None = None,
    policy_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _normalize_program_cfg(policy_cfg)
    if not cfg["enabled"]:
        return {
            "repo": repo_name,
            "path": str(repo_path),
            "generated_at": _iso_now(),
            "enabled": False,
            "summary": {
                "findings_total": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "at_risk": False,
                "quality_score": 100,
                "narrative": "qadrift disabled by policy",
            },
            "findings": [],
            "top_findings": [],
            "recommended_reviews": [],
            "model_contract": {},
            "errors": [],
        }

    snapshot = dict(repo_snapshot) if isinstance(repo_snapshot, dict) else {}
    findings: list[dict[str, Any]] = []

    # Reuse existing qadrift lane heuristics as the deterministic quality baseline.
    qa = run_qa_check(repo_path)
    for row in qa.findings:
        sev = str(row.severity or "MEDIUM").strip().lower()
        sev = sev if sev in ("high", "medium", "low") else "medium"
        fp = _fingerprint([repo_name, row.category, row.file, row.description])
        findings.append(
            {
                "fingerprint": fp,
                "category": str(row.category or "qa"),
                "severity": sev,
                "title": f"QA signal: {row.category}",
                "evidence": f"{row.file}: {row.description}",
                "recommendation": "Add or strengthen deterministic tests and keep evidence in repo-local CI checks.",
            }
        )

    if bool(cfg["include_workgraph_health"]):
        # Filter out drift-generated in-progress tasks to avoid feedback loop:
        # qadrift detects "stalled" → creates drift task → task sits idle → qadrift
        # detects more "stalled" → creates another drift task → repeat forever.
        in_progress = snapshot.get("in_progress") or []
        non_drift_in_progress = [
            t for t in in_progress
            if not any(tag in ("drift", "qadrift", "secdrift", "plandrift", "northstardrift")
                       for tag in (t.get("tags") or []))
        ]
        stalled_for_real = bool(snapshot.get("stalled")) and len(non_drift_in_progress) > 0
        if stalled_for_real:
            # Stable fingerprint: repo + category only, so dedup works across scans.
            fp = _fingerprint([repo_name, "work-stalled"])
            reasons = snapshot.get("stall_reasons")
            reason_line = "; ".join(str(item) for item in reasons[:2]) if isinstance(reasons, list) else "no reason"
            findings.append(
                {
                    "fingerprint": fp,
                    "category": "work-stalled",
                    "severity": "high",
                    "title": "Workgraph execution is stalled",
                    "evidence": reason_line,
                    "recommendation": "Unblock stalled tasks and tighten dependency chains before adding new scope.",
                }
            )
        missing_deps = max(0, int(snapshot.get("missing_dependencies") or 0))
        blocked_open = max(0, int(snapshot.get("blocked_open") or 0))
        if missing_deps > 0 or blocked_open > 0:
            fp = _fingerprint([repo_name, "dependency-gaps"])
            findings.append(
                {
                    "fingerprint": fp,
                    "category": "dependency-gaps",
                    "severity": "medium",
                    "title": "Dependency chain quality gaps detected",
                    "evidence": f"missing_dependencies={missing_deps}; blocked_open={blocked_open}",
                    "recommendation": "Repair task dependency integrity and verify ready queue transitions.",
                }
            )
        has_work = bool(non_drift_in_progress) or bool(snapshot.get("ready"))
        if has_work and bool(snapshot.get("workgraph_exists")) and not bool(snapshot.get("service_running")):
            fp = _fingerprint([repo_name, "executor-offline"])
            findings.append(
                {
                    "fingerprint": fp,
                    "category": "executor-offline",
                    "severity": "medium",
                    "title": "Workgraph service is stopped while work exists",
                    "evidence": "ready/in-progress tasks present but service_running=false",
                    "recommendation": "Restore service supervision and confirm heartbeat continuity.",
                }
            )

    if bool(cfg["include_test_health"]):
        has_src = (repo_path / "src").exists()
        has_tests_dir = (repo_path / "tests").exists()
        has_py_tests = any(repo_path.glob("tests/**/test_*.py")) or any(repo_path.glob("test_*.py"))
        has_js_tests = any(repo_path.glob("**/*.test.ts")) or any(repo_path.glob("**/*.spec.ts")) or any(repo_path.glob("**/*.test.js"))
        if has_src and not has_tests_dir and not has_py_tests and not has_js_tests:
            fp = _fingerprint([repo_name, "tests-missing"])
            findings.append(
                {
                    "fingerprint": fp,
                    "category": "tests-missing",
                    "severity": "high",
                    "title": "Source exists without test surface",
                    "evidence": "src/ present but no tests directory or obvious test files",
                    "recommendation": "Create baseline behavioral tests before expanding feature work.",
                }
            )

    if bool(cfg["include_playwright"]) and _looks_like_web_repo(repo_path):
        playwright_cfg = (repo_path / "playwright.config.ts").exists() or (repo_path / "playwright.config.js").exists()
        e2e_tests = any(repo_path.glob("tests/**/*.spec.ts")) or any(repo_path.glob("e2e/**/*.spec.ts"))
        if not playwright_cfg or not e2e_tests:
            fp = _fingerprint([repo_name, "ux-e2e-coverage"])
            findings.append(
                {
                    "fingerprint": fp,
                    "category": "ux-e2e-coverage",
                    "severity": "medium",
                    "title": "Web repo lacks robust Playwright regression surface",
                    "evidence": f"playwright_config={playwright_cfg}; e2e_specs={e2e_tests}",
                    "recommendation": "Add critical user-flow e2e coverage with Playwright and baseline screenshots.",
                }
            )

    deduped: dict[str, dict[str, Any]] = {}
    for row in findings:
        fp = str(row.get("fingerprint") or "").strip()
        if fp and fp not in deduped:
            deduped[fp] = row

    ordered = sorted(
        deduped.values(),
        key=lambda row: (
            -_PROGRAM_SEVERITY_RANK.get(str(row.get("severity") or "").lower(), 0),
            str(row.get("category") or ""),
            str(row.get("evidence") or ""),
        ),
    )

    top_findings = ordered[: int(cfg["max_findings_per_repo"])]
    for row in top_findings:
        row["model_prompt"] = _quality_prompt(repo_name, row)

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for row in top_findings:
        sev = str(row.get("severity") or "").lower()
        if sev in counts:
            counts[sev] += 1
    total = len(top_findings)
    raw_total = len(ordered)
    penalty = (counts["critical"] * 25) + (counts["high"] * 15) + (counts["medium"] * 8) + (counts["low"] * 3)
    quality_score = max(0, 100 - min(95, penalty))
    at_risk = counts["critical"] > 0 or counts["high"] > 0 or quality_score < 75 or raw_total > total
    recommended_reviews = [row for row in top_findings if _PROGRAM_SEVERITY_RANK.get(str(row.get("severity") or "").lower(), 0) >= 2][:10]

    narrative = (
        f"qadrift evaluated `{repo_name}`: {total} prioritized findings "
        f"(critical={counts['critical']}, high={counts['high']}, medium={counts['medium']}, low={counts['low']}). "
        f"Quality score={quality_score}. Raw findings observed={raw_total}."
    )
    model_contract = {
        "decision_owner": "model",
        "triage_objective": "Prioritize quality remediations while preserving active work continuity.",
        "required_outputs": [
            "root_cause",
            "smallest_safe_fix",
            "validation_steps",
            "workgraph_updates",
        ],
        "prompt_seed": (
            "Review qadrift findings, request needed context, and generate prioritized corrective actions "
            "without overriding local in-progress work."
        ),
    }
    return {
        "repo": repo_name,
        "path": str(repo_path),
        "generated_at": _iso_now(),
        "enabled": True,
        "summary": {
            "findings_total": total,
            "raw_findings_total": raw_total,
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "at_risk": at_risk,
            "quality_score": quality_score,
            "narrative": narrative,
            "modules_tested": qa.modules_tested,
            "modules_untested": qa.modules_untested,
        },
        "findings": ordered,
        "top_findings": top_findings,
        "recommended_reviews": recommended_reviews,
        "model_contract": model_contract,
        "errors": [],
    }


_LANE_SEVERITY_MAP = {
    "HIGH": "error",
    "MEDIUM": "warning",
    "LOW": "info",
    "CRITICAL": "critical",
}


def _map_severity(finding: QAFinding) -> str:
    """Map QAFinding severity (HIGH/MEDIUM/LOW) to lane contract level."""
    return _LANE_SEVERITY_MAP.get(finding.severity.upper(), "info")


def run_as_lane(project_dir: Path) -> "LaneResult":
    """Run qadrift and return results in the standard lane contract format.

    Wraps ``run_qa_check`` so that qadrift can be invoked through the
    unified ``LaneResult`` interface used by all drift lanes.
    """
    from driftdriver.lane_contract import LaneFinding, LaneResult

    try:
        report = run_qa_check(project_dir)
    except Exception as exc:
        return LaneResult(
            lane="qadrift",
            findings=[LaneFinding(message=f"qadrift error: {exc}", severity="error")],
            exit_code=1,
            summary=f"qadrift failed: {exc}",
        )

    findings = []
    for f in report.findings:
        findings.append(LaneFinding(
            message=f.description,
            severity=_map_severity(f),
            file=f.file,
            line=0,
            tags=[f.category],
        ))

    exit_code = 1 if findings else 0
    return LaneResult(
        lane="qadrift",
        findings=findings,
        exit_code=exit_code,
        summary=report.summary or f"{len(findings)} findings",
    )


def _run_cmd(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: float = 40.0,
) -> tuple[int, str, str]:
    def _invoke(actual_cmd: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            actual_cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        proc = _invoke(cmd)
    except FileNotFoundError as exc:
        if cmd and str(cmd[0]) == "wg":
            candidates = [
                str(Path.home() / ".cargo" / "bin" / "wg"),
                "/opt/homebrew/bin/wg",
                "/usr/local/bin/wg",
            ]
            seen: set[str] = set()
            for candidate in candidates:
                if candidate in seen:
                    continue
                seen.add(candidate)
                if not Path(candidate).exists():
                    continue
                try:
                    proc = _invoke([candidate, *cmd[1:]])
                    return int(proc.returncode), str(proc.stdout or "").strip(), str(proc.stderr or "").strip()
                except FileNotFoundError:
                    continue
        return 127, "", str(exc)
    except Exception as exc:
        return 1, "", str(exc)
    return int(proc.returncode), str(proc.stdout or "").strip(), str(proc.stderr or "").strip()


def emit_quality_review_tasks(
    *,
    repo_path: Path,
    report: dict[str, Any],
    max_tasks: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "enabled": True,
        "attempted": 0,
        "created": 0,
        "existing": 0,
        "skipped": 0,
        "errors": [],
        "tasks": [],
    }
    wg_dir = repo_path / ".workgraph"
    if not wg_dir.exists():
        out["errors"].append(f"{repo_path.name}: .workgraph missing")
        return out

    reviews = report.get("recommended_reviews")
    review_rows = [row for row in reviews if isinstance(row, dict)] if isinstance(reviews, list) else []
    for row in review_rows[: max(1, int(max_tasks))]:
        fingerprint = str(row.get("fingerprint") or "")
        if not fingerprint:
            out["skipped"] = int(out["skipped"]) + 1
            continue
        task_id = f"qadrift-{fingerprint[:14]}"
        title = f"qadrift: {str(row.get('severity') or 'medium')} {str(row.get('category') or 'finding')}"
        prompt = str(row.get("model_prompt") or "")
        desc = (
            "Program-level qadrift review task.\n\n"
            f"Finding: {row.get('title')}\n"
            f"Severity: {row.get('severity')}\n"
            f"Evidence: {row.get('evidence')}\n"
            f"Recommendation: {row.get('recommendation')}\n\n"
            f"Suggested agent prompt:\n{prompt}\n"
        )

        out["attempted"] = int(out["attempted"]) + 1
        show_rc, _, show_err = _run_cmd(
            ["wg", "--dir", str(wg_dir), "show", task_id, "--json"],
            cwd=repo_path,
            timeout=20.0,
        )
        if show_rc == 0:
            out["existing"] = int(out["existing"]) + 1
            out["tasks"].append({"task_id": task_id, "status": "existing"})
            continue

        add_rc, add_out, add_err = _run_cmd(
            [
                "wg",
                "--dir",
                str(wg_dir),
                "add",
                title,
                "--id",
                task_id,
                "-d",
                desc,
                "-t",
                "drift",
                "-t",
                "qadrift",
                "-t",
                "quality",
                "-t",
                "review",
            ],
            cwd=repo_path,
            timeout=30.0,
        )
        if add_rc == 0:
            out["created"] = int(out["created"]) + 1
            out["tasks"].append({"task_id": task_id, "status": "created"})
        else:
            err = (add_err or add_out or show_err or "").strip()
            out["errors"].append(f"{repo_path.name}: could not create {task_id}: {err[:220]}")

    out["tasks"] = list(out.get("tasks") or [])[:80]
    out["errors"] = list(out.get("errors") or [])[:80]
    return out
