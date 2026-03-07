from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any, TYPE_CHECKING
from urllib.error import URLError
from urllib.request import Request, urlopen

from driftdriver.drift_task_guard import guarded_add_drift_task

if TYPE_CHECKING:
    from driftdriver.lane_contract import LaneResult


_TEXT_SUFFIX_ALLOW = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".env",
    ".md",
    ".txt",
    ".sh",
    ".zsh",
    ".bash",
}

_IGNORE_DIRS = {
    ".git",
    ".workgraph",
    "node_modules",
    "dist",
    "build",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
}

_SENSITIVE_FILENAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
}

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    (
        "aws-access-key",
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
        "high",
        "high",
    ),
    (
        "github-token",
        re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
        "high",
        "high",
    ),
    (
        "private-key-material",
        re.compile(r"-----BEGIN (?:RSA|EC|OPENSSH) PRIVATE KEY-----"),
        "critical",
        "high",
    ),
    (
        "generic-secret-assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd)\b\s*[:=]\s*[\"'][^\"'\n]{8,}[\"']"
        ),
        "medium",
        "medium",
    ),
]

_SEVERITY_ORDER = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(parts: list[str]) -> str:
    base = "|".join(str(item or "").strip().lower() for item in parts)
    return sha1(base.encode("utf-8")).hexdigest()  # noqa: S324 - non-crypto identity hash


def _is_text_candidate(path: Path) -> bool:
    if path.suffix.lower() in _TEXT_SUFFIX_ALLOW:
        return True
    name = path.name.lower()
    if name in _SENSITIVE_FILENAMES:
        return True
    return any(token in name for token in ("secret", "token", "credential", "password"))


def _iter_repo_files(repo_path: Path, *, max_files: int) -> list[Path]:
    out: list[Path] = []
    if not repo_path.exists():
        return out
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in _IGNORE_DIRS]
        for filename in files:
            path = Path(root) / filename
            rel = path.relative_to(repo_path)
            if any(part in _IGNORE_DIRS for part in rel.parts):
                continue
            out.append(path)
            if len(out) >= max_files:
                return out
    return out


def _read_text(path: Path, *, max_bytes: int) -> str:
    try:
        raw = path.read_bytes()
    except OSError:
        return ""
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


def _placeholder_value(value: str) -> bool:
    low = str(value or "").strip().lower()
    if not low:
        return True
    placeholders = (
        "example",
        "sample",
        "dummy",
        "changeme",
        "replace-me",
        "replace_me",
        "test-key",
        "test_token",
    )
    return any(token in low for token in placeholders)


def _scan_secret_patterns(
    *,
    repo_name: str,
    repo_path: Path,
    max_files: int,
    max_file_bytes: int,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in _iter_repo_files(repo_path, max_files=max_files):
        rel = str(path.relative_to(repo_path))
        if not _is_text_candidate(path):
            continue
        text = _read_text(path, max_bytes=max_file_bytes)
        if not text:
            continue
        for category, pattern, severity, confidence in _SECRET_PATTERNS:
            matches = list(pattern.finditer(text))
            for match in matches[:4]:
                snippet = match.group(0).strip()
                if _placeholder_value(snippet):
                    continue
                line = text.count("\n", 0, match.start()) + 1
                fp = _fingerprint([repo_name, category, rel, str(line), snippet[:80]])
                findings.append(
                    {
                        "fingerprint": fp,
                        "category": category,
                        "severity": severity,
                        "confidence": confidence,
                        "title": f"Potential secret detected ({category})",
                        "evidence": snippet[:200],
                        "file": rel,
                        "line": line,
                        "recommendation": "Move secret to secure runtime config and rotate compromised credentials if applicable.",
                    }
                )
    return findings


def _scan_sensitive_artifacts(*, repo_name: str, repo_path: Path, max_files: int) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for path in _iter_repo_files(repo_path, max_files=max_files):
        rel = path.relative_to(repo_path)
        name = path.name.lower()
        if name in _SENSITIVE_FILENAMES or path.suffix.lower() in {".pem", ".p12", ".key"}:
            fp = _fingerprint([repo_name, "sensitive-artifact", str(rel)])
            findings.append(
                {
                    "fingerprint": fp,
                    "category": "sensitive-artifact",
                    "severity": "high",
                    "confidence": "high",
                    "title": "Sensitive key material file present in repo tree",
                    "evidence": str(rel),
                    "file": str(rel),
                    "line": 0,
                    "recommendation": "Remove secret material from repo history and store keys in a dedicated secret manager.",
                }
            )
    return findings


def _scan_dependency_posture(*, repo_name: str, repo_path: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    lock_expectations = [
        (
            ["package.json"],
            ["package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb"],
            "node-lock-missing",
        ),
        (
            ["pyproject.toml", "requirements.txt", "requirements-dev.txt"],
            ["uv.lock", "poetry.lock", "requirements.lock"],
            "python-lock-missing",
        ),
        (
            ["Cargo.toml"],
            ["Cargo.lock"],
            "cargo-lock-missing",
        ),
    ]
    for manifests, locks, category in lock_expectations:
        manifest_hits = [m for m in manifests if (repo_path / m).exists()]
        if not manifest_hits:
            continue
        has_lock = any((repo_path / lock).exists() for lock in locks)
        if has_lock:
            continue
        fp = _fingerprint([repo_name, category, ",".join(sorted(manifest_hits))])
        findings.append(
            {
                "fingerprint": fp,
                "category": category,
                "severity": "medium",
                "confidence": "high",
                "title": "Dependency manifest detected without lockfile",
                "evidence": f"manifests={','.join(manifest_hits)}",
                "file": manifest_hits[0],
                "line": 0,
                "recommendation": "Generate and commit a lockfile to keep dependency resolution deterministic and auditable.",
            }
        )
    return findings


def _scan_pentest_headers(*, repo_name: str, target_urls: list[str], timeout_seconds: float = 4.0) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not target_urls:
        return findings
    required_headers = {
        "strict-transport-security": "HSTS",
        "content-security-policy": "CSP",
        "x-frame-options": "X-Frame-Options",
        "x-content-type-options": "X-Content-Type-Options",
        "referrer-policy": "Referrer-Policy",
    }
    for target in target_urls[:12]:
        url = str(target or "").strip()
        if not url:
            continue
        req = Request(url=url, method="HEAD")
        try:
            with urlopen(req, timeout=timeout_seconds) as resp:  # noqa: S310 - policy-controlled target
                headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
        except URLError as exc:
            fp = _fingerprint([repo_name, "pentest-unreachable", url])
            findings.append(
                {
                    "fingerprint": fp,
                    "category": "pentest-unreachable",
                    "severity": "low",
                    "confidence": "medium",
                    "title": "Pentest target unreachable",
                    "evidence": f"{url}: {exc}",
                    "file": "",
                    "line": 0,
                    "recommendation": "Verify target URL and network path before relying on active surface scan coverage.",
                }
            )
            continue
        missing = [label for key, label in required_headers.items() if key not in headers]
        if not missing:
            continue
        fp = _fingerprint([repo_name, "pentest-header-missing", url, ",".join(sorted(missing))])
        findings.append(
            {
                "fingerprint": fp,
                "category": "pentest-header-missing",
                "severity": "medium",
                "confidence": "high",
                "title": "Security response headers missing on HTTP target",
                "evidence": f"{url}: missing {', '.join(missing)}",
                "file": "",
                "line": 0,
                "recommendation": "Harden response headers at ingress/app layer and verify with an automated baseline scan.",
            }
        )
    return findings


def _security_prompt(repo_name: str, finding: dict[str, Any]) -> str:
    category = str(finding.get("category") or "security-finding")
    severity = str(finding.get("severity") or "medium")
    file_path = str(finding.get("file") or "n/a")
    evidence = str(finding.get("evidence") or "")
    recommendation = str(finding.get("recommendation") or "")
    fingerprint = str(finding.get("fingerprint") or "")
    return (
        f"In `{repo_name}`, triage secdrift finding `{fingerprint}` ({severity}/{category}) at `{file_path}`. "
        f"Evidence: {evidence}. Determine root cause, safest remediation path, and exact Workgraph task updates. "
        f"Recommendation seed: {recommendation}"
    )


def _normalize_policy(policy_cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(policy_cfg) if isinstance(policy_cfg, dict) else {}
    target_urls_raw = raw.get("target_urls")
    target_urls = [str(item).strip() for item in target_urls_raw if str(item).strip()] if isinstance(target_urls_raw, list) else []
    return {
        "enabled": bool(raw.get("enabled", True)),
        "max_findings_per_repo": max(1, int(raw.get("max_findings_per_repo", 40))),
        "scan_max_files": max(20, int(raw.get("scan_max_files", 320))),
        "scan_max_file_bytes": max(2048, int(raw.get("scan_max_file_bytes", 262144))),
        "run_pentest": bool(raw.get("run_pentest", False)),
        "allow_network_scans": bool(raw.get("allow_network_scans", False)),
        "target_urls": target_urls[:20],
        "emit_review_tasks": bool(raw.get("emit_review_tasks", True)),
        "max_review_tasks_per_repo": max(1, int(raw.get("max_review_tasks_per_repo", 3))),
        "hard_stop_on_critical": bool(raw.get("hard_stop_on_critical", False)),
    }


def run_secdrift_scan(
    *,
    repo_name: str,
    repo_path: Path,
    policy_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _normalize_policy(policy_cfg)
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
                "risk_score": 0,
                "narrative": "secdrift disabled by policy",
            },
            "findings": [],
            "top_findings": [],
            "recommended_reviews": [],
            "model_contract": {},
            "errors": [],
        }

    findings = []
    findings.extend(
        _scan_secret_patterns(
            repo_name=repo_name,
            repo_path=repo_path,
            max_files=int(cfg["scan_max_files"]),
            max_file_bytes=int(cfg["scan_max_file_bytes"]),
        )
    )
    findings.extend(
        _scan_sensitive_artifacts(
            repo_name=repo_name,
            repo_path=repo_path,
            max_files=int(cfg["scan_max_files"]),
        )
    )
    findings.extend(_scan_dependency_posture(repo_name=repo_name, repo_path=repo_path))

    if bool(cfg["run_pentest"]) and bool(cfg["allow_network_scans"]):
        findings.extend(
            _scan_pentest_headers(
                repo_name=repo_name,
                target_urls=list(cfg["target_urls"]),
            )
        )

    deduped: dict[str, dict[str, Any]] = {}
    for row in findings:
        if not isinstance(row, dict):
            continue
        fp = str(row.get("fingerprint") or "").strip()
        if not fp:
            continue
        if fp not in deduped:
            deduped[fp] = row

    ordered = sorted(
        deduped.values(),
        key=lambda row: (
            -_SEVERITY_ORDER.get(str(row.get("severity") or "").lower(), 0),
            str(row.get("category") or ""),
            str(row.get("file") or ""),
            str(row.get("line") or ""),
        ),
    )
    max_findings = int(cfg["max_findings_per_repo"])
    top_findings = ordered[:max_findings]
    for row in top_findings:
        row["model_prompt"] = _security_prompt(repo_name, row)

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for row in ordered:
        sev = str(row.get("severity") or "").lower()
        if sev in counts:
            counts[sev] += 1
    findings_total = len(ordered)
    risk_score = min(100, (counts["critical"] * 28) + (counts["high"] * 16) + (counts["medium"] * 8) + (counts["low"] * 3))
    at_risk = counts["critical"] > 0 or counts["high"] > 0 or risk_score >= 24
    recommended_reviews = [row for row in top_findings if _SEVERITY_ORDER.get(str(row.get("severity") or "").lower(), 0) >= 2][:10]

    narrative = (
        f"secdrift scanned `{repo_name}`: {findings_total} findings "
        f"(critical={counts['critical']}, high={counts['high']}, medium={counts['medium']}, low={counts['low']}). "
        f"Risk score={risk_score}."
    )
    model_contract = {
        "decision_owner": "model",
        "triage_objective": "Select smallest safe remediation and explicit workgraph updates for each security finding.",
        "required_outputs": [
            "root_cause",
            "remediation_steps",
            "verification_plan",
            "workgraph_task_updates",
        ],
        "prompt_seed": (
            "Review secdrift findings, request any missing context, and return prioritized remediation plans "
            "without applying destructive changes."
        ),
    }
    return {
        "repo": repo_name,
        "path": str(repo_path),
        "generated_at": _iso_now(),
        "enabled": True,
        "summary": {
            "findings_total": findings_total,
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "at_risk": at_risk,
            "risk_score": risk_score,
            "narrative": narrative,
        },
        "findings": ordered,
        "top_findings": top_findings,
        "recommended_reviews": recommended_reviews,
        "model_contract": model_contract,
        "errors": [],
    }


_LANE_SEVERITY_MAP = {
    "critical": "critical",
    "high": "error",
    "medium": "warning",
    "low": "info",
}


def _map_severity(finding: dict[str, Any]) -> str:
    """Map secdrift finding severity to lane contract level."""
    raw = str(finding.get("severity") or "").lower()
    return _LANE_SEVERITY_MAP.get(raw, "info")


def run_as_lane(project_dir: Path) -> "LaneResult":
    """Run secdrift and return results in the standard lane contract format.

    Wraps ``run_secdrift_scan`` so that secdrift can be invoked through the
    unified ``LaneResult`` interface used by all drift lanes.
    """
    from driftdriver.lane_contract import LaneFinding, LaneResult

    try:
        report = run_secdrift_scan(
            repo_name=project_dir.name,
            repo_path=project_dir,
            policy_cfg={"run_pentest": False, "allow_network_scans": False},
        )
    except Exception as exc:
        return LaneResult(
            lane="secdrift",
            findings=[LaneFinding(message=f"secdrift error: {exc}", severity="error")],
            exit_code=1,
            summary=f"secdrift failed: {exc}",
        )

    findings = []
    for f in report.get("findings", []):
        findings.append(LaneFinding(
            message=str(f.get("title") or f.get("category") or "security finding"),
            severity=_map_severity(f),
            file=str(f.get("file") or ""),
            line=int(f.get("line") or 0),
            tags=[str(f.get("category") or "security")],
        ))

    summary_data = report.get("summary", {})
    summary_text = str(summary_data.get("narrative") or f"{len(findings)} findings")
    exit_code = 1 if findings else 0
    return LaneResult(
        lane="secdrift",
        findings=findings,
        exit_code=exit_code,
        summary=summary_text,
    )


def emit_security_review_tasks(
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
        task_id = f"secdrift-{fingerprint[:14]}"
        title = f"secdrift: {str(row.get('severity') or 'medium')} {str(row.get('category') or 'finding')}"
        prompt = str(row.get("model_prompt") or "")
        desc = (
            "Program-level secdrift review task.\n\n"
            f"Finding: {row.get('title')}\n"
            f"Severity: {row.get('severity')}\n"
            f"Evidence: {row.get('evidence')}\n"
            f"File: {row.get('file')}\n"
            f"Recommendation: {row.get('recommendation')}\n\n"
            f"Suggested agent prompt:\n{prompt}\n"
        )

        out["attempted"] = int(out["attempted"]) + 1
        result = guarded_add_drift_task(
            wg_dir=wg_dir,
            task_id=task_id,
            title=title,
            description=desc,
            lane_tag="secdrift",
            extra_tags=["security", "review"],
            cwd=repo_path,
        )
        if result == "created":
            out["created"] = int(out["created"]) + 1
            out["tasks"].append({"task_id": task_id, "status": "created"})
        elif result == "existing":
            out["existing"] = int(out["existing"]) + 1
            out["tasks"].append({"task_id": task_id, "status": "existing"})
        elif result == "capped":
            out["skipped"] = int(out["skipped"]) + 1
            out["tasks"].append({"task_id": task_id, "status": "capped"})
        else:
            out["errors"].append(f"{repo_path.name}: could not create {task_id}: {result}")

    out["tasks"] = list(out.get("tasks") or [])[:80]
    out["errors"] = list(out.get("errors") or [])[:80]
    return out
