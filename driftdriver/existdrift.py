# ABOUTME: existdrift — pre-build grounding lane.
# ABOUTME: Verifies plan↔reality (touch paths, repo boundary, symbols) and
# ABOUTME: builds evidence bundles that ground planner prompts.
from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any

from driftdriver.drift_task_guard import guarded_add_drift_task

_SEVERITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}

_ACTIVE_STATES = {"open", "ready", "in-progress"}

# Exclude patterns for directory traversal and symbol search.
_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".workgraph", "dist", "build", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "target", "Cargo",
}

# Regex to extract a wg-contract fenced block from a description.
_WG_CONTRACT_RE = re.compile(
    r"```wg-contract\s*\n(.*?)```",
    re.DOTALL,
)

# Regex to extract backticked identifiers from description text.
_SYMBOL_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]{3,})`")

# Documents we look for in evidence bundles.
_DOC_FILES = ("NORTH_STAR.md", "AGENTS.md", "CLAUDE.md", "README.md")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(parts: list[str]) -> str:
    key = "|".join(str(part or "").strip().lower() for part in parts)
    return sha1(key.encode("utf-8")).hexdigest()  # noqa: S324


def _default_existdrift_cfg() -> dict[str, Any]:
    return {
        "enabled": True,
        "interval_seconds": 14400,
        "severity_missing_path": "warning",
        "severity_outside_repo": "high",
        "severity_unknown_symbol": "info",
        "max_findings": 40,
        "symbol_check": True,
        "min_symbol_len": 4,
        "emit_followups": False,
    }


def _normalize_cfg(raw: dict[str, Any] | None) -> dict[str, Any]:
    r = dict(raw) if isinstance(raw, dict) else {}
    return {
        "enabled": bool(r.get("enabled", True)),
        "interval_seconds": max(0, int(r.get("interval_seconds", 14400))),
        "severity_missing_path": str(r.get("severity_missing_path", "warning") or "warning"),
        "severity_outside_repo": str(r.get("severity_outside_repo", "high") or "high"),
        "severity_unknown_symbol": str(r.get("severity_unknown_symbol", "info") or "info"),
        "max_findings": max(1, int(r.get("max_findings", 40))),
        "symbol_check": bool(r.get("symbol_check", True)),
        "min_symbol_len": max(2, int(r.get("min_symbol_len", 4))),
        "emit_followups": bool(r.get("emit_followups", False)),
    }


# ---------------------------------------------------------------------------
# Workgraph task reader (mirrors plandrift's approach)
# ---------------------------------------------------------------------------


def _read_workgraph_tasks(
    repo_path: Path,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """Read workgraph tasks from graph.jsonl. Reuses plandrift's reader if available."""
    # Try importing plandrift's reader first to avoid duplication.
    try:
        from driftdriver.plandrift import _read_workgraph_tasks as _pdr

        return _pdr(repo_path)
    except Exception:
        pass

    # Fallback: mirror the pattern directly.
    graph = repo_path / ".workgraph" / "graph.jsonl"
    if not graph.exists():
        return {}, [".workgraph/graph.jsonl missing"]

    tasks: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    try:
        lines = graph.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return {}, [f"could not read graph.jsonl: {exc}"]

    for idx, line in enumerate(lines, start=1):
        raw = line.strip()
        if not raw:
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            if len(errors) < 12:
                errors.append(f"invalid json at line {idx}")
            continue
        if not isinstance(row, dict):
            continue
        row_type = str(row.get("type") or "").strip().lower()
        if row_type and row_type != "task":
            continue
        task_id = str(row.get("id") or "").strip()
        if not task_id:
            continue
        tasks[task_id] = {
            "id": task_id,
            "title": str(row.get("title") or ""),
            "description": str(row.get("description") or row.get("desc") or ""),
            "status": str(row.get("status") or "open").strip().lower(),
        }

    return tasks, errors[:12]


# ---------------------------------------------------------------------------
# Contract and symbol extraction
# ---------------------------------------------------------------------------


def _extract_contract_touch(description: str) -> list[str]:
    """Extract the ``touch`` list from a wg-contract fenced block.

    Tolerates parse failure by returning an empty list.  Falls back to
    scanning the raw description for ``touch = [...]`` TOML-like syntax if
    the fenced block is malformed.
    """
    # Try the fenced block first.
    match = _WG_CONTRACT_RE.search(description)
    body = match.group(1) if match else description

    # Quick TOML parse for the touch key.
    try:
        import tomllib

        # tomllib requires a top-level table; wrap if needed.
        parsed = tomllib.loads(body)
        raw_touch = parsed.get("touch")
        if isinstance(raw_touch, list):
            return [str(p).strip() for p in raw_touch if str(p).strip()]
    except Exception:
        pass

    # Regex fallback: find touch = ["a", "b"]
    touch_match = re.search(
        r'touch\s*=\s*\[([^\]]*)\]',
        body,
    )
    if touch_match:
        inner = touch_match.group(1)
        paths = re.findall(r'"([^"]+)"', inner)
        return paths
    return []


def _extract_symbols(
    description: str,
    *,
    min_len: int = 4,
    cap: int = 20,
) -> list[str]:
    """Extract backticked identifiers from a description, deduped and capped."""
    seen: set[str] = set()
    result: list[str] = []
    for m in _SYMBOL_RE.finditer(description):
        sym = m.group(1)
        if len(sym) >= min_len and sym not in seen:
            seen.add(sym)
            result.append(sym)
            if len(result) >= cap:
                break
    return result


def _check_symbol_exists(symbol: str, repo_path: Path) -> bool:
    """Check whether a symbol appears in any source file under the repo."""
    rg = shutil.which("rg")
    if rg:
        try:
            result = subprocess.run(
                [rg, "--fixed-strings", "--files-with-matches",
                 "--no-hidden", "-g", "!.git", "-g", "!node_modules",
                 "-g", "!__pycache__", "-g", "!.venv", "-g", "!*.pyc",
                 symbol],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Fallback: grep
    grep = shutil.which("grep")
    if grep:
        try:
            result = subprocess.run(
                [grep, "-rl", "--include=*",
                 "-r", symbol, str(repo_path)],
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=15,
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass

    # Last resort: Python file scan of top-level source dirs.
    try:
        for src_dir in [repo_path / "src", repo_path, repo_path / "lib"]:
            if not src_dir.exists():
                continue
            for f in src_dir.rglob("*.py"):
                if any(part in _EXCLUDE_DIRS for part in f.parts):
                    continue
                try:
                    if symbol in f.read_text(encoding="utf-8", errors="replace"):
                        return True
                except OSError:
                    continue
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Path checking helpers
# ---------------------------------------------------------------------------


def _resolve_path(repo_path: Path, touch_path: str) -> Path:
    """Resolve a touch path relative to repo_path, normalising ``../`` escapes."""
    return (repo_path / touch_path).resolve()


def _is_outside_repo(repo_path: Path, touch_path: str) -> bool:
    """Return True if a touch path resolves outside the repo root."""
    resolved = _resolve_path(repo_path, touch_path)
    try:
        resolved.relative_to(repo_path.resolve())
        return False
    except ValueError:
        return True


def _suggest_nearest(repo_path: Path, touch_path: str) -> str:
    """Suggest a nearest sibling if the parent directory exists."""
    full = repo_path / touch_path
    parent = full.parent
    if parent.exists() and parent != repo_path:
        siblings = sorted(
            f.name for f in parent.iterdir() if f.is_file()
        )[:5]
        if siblings:
            rel_parent = parent.relative_to(repo_path)
            return (
                f"Parent `{rel_parent}/` exists with: {', '.join(siblings)}. "
                f"Confirm the path or declare the file as new."
            )
    return "Confirm the path or declare the file as new."


# ---------------------------------------------------------------------------
# Main scan
# ---------------------------------------------------------------------------


def scan_grounding(
    repo_path: Path,
    *,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scan workgraph tasks for plan↔reality mismatches.

    Checks three things for each active task:
    a. Missing touch paths (with all-new heuristic).
    b. Touch paths outside the repo boundary.
    c. Unknown backticked symbols.

    Returns a report dict mirroring plandrift's shape.
    """
    c = _normalize_cfg(cfg)
    if not c["enabled"]:
        return {
            "repo": repo_path.name,
            "path": str(repo_path),
            "generated_at": _iso_now(),
            "enabled": False,
            "summary": {
                "findings_total": 0,
                "critical": 0,
                "high": 0,
                "medium": 0,
                "low": 0,
                "info": 0,
                "at_risk": False,
                "narrative": "existdrift disabled by policy",
            },
            "findings": [],
            "top_findings": [],
            "errors": [],
        }

    repo_name = repo_path.name
    tasks, errors = _read_workgraph_tasks(repo_path)

    findings: list[dict[str, Any]] = []

    for task in tasks.values():
        task_id = str(task.get("id") or "")
        task_title = str(task.get("title") or "")
        status = str(task.get("status") or "open").strip().lower()
        if status not in _ACTIVE_STATES:
            continue

        description = str(task.get("description") or "")
        touch_paths = _extract_contract_touch(description)

        # --- Check b: outside-repo paths (highest severity, check first) ---
        outside_paths = [
            p for p in touch_paths if _is_outside_repo(repo_path, p)
        ]
        if outside_paths:
            findings.append({
                "fingerprint": _fingerprint(
                    [repo_name, task_id, "outside-repo-path", ",".join(sorted(outside_paths))]
                ),
                "category": "outside-repo-path",
                "severity": c["severity_outside_repo"],
                "title": "Task touches paths outside repo boundary",
                "evidence": (
                    f"task={task_id} ({task_title}); "
                    f"outside={outside_paths}"
                ),
                "recommendation": (
                    f"Move `{outside_paths[0]}` into the repo or use "
                    f"`wg add --repo <peer>` for cross-repo work."
                ),
            })

        # --- Check a: missing touch paths ---
        # Skip outside-repo paths for the missing check (already flagged).
        inside_paths = [
            p for p in touch_paths if not _is_outside_repo(repo_path, p)
        ]
        missing = [
            p for p in inside_paths
            if not (repo_path / p).exists()
        ]
        existing = [p for p in inside_paths if (repo_path / p).exists()]

        if missing:
            all_new = len(existing) == 0 and len(missing) > 0
            if all_new:
                findings.append({
                    "fingerprint": _fingerprint(
                        [repo_name, task_id, "missing-touch-path", "all-new"]
                    ),
                    "category": "missing-touch-path",
                    "severity": "info",
                    "title": "Task appears to create entirely new files",
                    "evidence": (
                        f"task={task_id} ({task_title}); "
                        f"all-new paths (none exist yet): {missing}"
                    ),
                    "recommendation": (
                        f"Confirm these are intentional new files: "
                        f"{', '.join(missing)}"
                    ),
                })
            else:
                for mp in missing:
                    suggestion = _suggest_nearest(repo_path, mp)
                    findings.append({
                        "fingerprint": _fingerprint(
                            [repo_name, task_id, "missing-touch-path", mp]
                        ),
                        "category": "missing-touch-path",
                        "severity": c["severity_missing_path"],
                        "title": "Task references non-existent path",
                        "evidence": f"task={task_id} ({task_title}); path={mp}",
                        "recommendation": suggestion,
                    })

        # --- Check c: unknown symbols ---
        if c["symbol_check"]:
            symbols = _extract_symbols(
                description,
                min_len=c["min_symbol_len"],
            )
            unknown: list[str] = []
            for sym in symbols:
                if not _check_symbol_exists(sym, repo_path):
                    unknown.append(sym)
            if unknown:
                findings.append({
                    "fingerprint": _fingerprint(
                        [repo_name, task_id, "unknown-symbol", ",".join(sorted(unknown))]
                    ),
                    "category": "unknown-symbol",
                    "severity": c["severity_unknown_symbol"],
                    "title": "Description references symbols not found in repo",
                    "evidence": f"task={task_id} ({task_title}); unknown symbols: {unknown}",
                    "recommendation": (
                        f"Verify these identifiers exist or will be created: "
                        f"{', '.join(unknown)}"
                    ),
                })

    # Deduplicate by fingerprint.
    deduped: dict[str, dict[str, Any]] = {}
    for row in findings:
        fp = str(row.get("fingerprint") or "").strip()
        if fp and fp not in deduped:
            deduped[fp] = row

    ordered = sorted(
        deduped.values(),
        key=lambda row: (
            -_SEVERITY_RANK.get(str(row.get("severity") or "").lower(), 0),
            str(row.get("category") or ""),
            str(row.get("evidence") or ""),
        ),
    )

    max_f = int(c["max_findings"])
    top_findings = ordered[:max_f]

    counts: dict[str, int] = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for row in ordered:
        sev = str(row.get("severity") or "").lower()
        if sev in counts:
            counts[sev] += 1

    total = len(ordered)
    at_risk = counts["critical"] > 0 or counts["high"] > 0
    narrative = (
        f"existdrift reviewed `{repo_name}`: {total} grounding findings "
        f"(critical={counts['critical']}, high={counts['high']}, "
        f"medium={counts['medium']}, low={counts['low']}, "
        f"info={counts['info']}). "
        f"Checked {len(tasks)} tasks."
    )

    return {
        "repo": repo_name,
        "path": str(repo_path),
        "generated_at": _iso_now(),
        "enabled": True,
        "summary": {
            "findings_total": total,
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "info": counts["info"],
            "at_risk": at_risk,
            "narrative": narrative,
        },
        "findings": ordered,
        "top_findings": top_findings,
        "errors": errors[:20],
    }


# ---------------------------------------------------------------------------
# Evidence bundle (for planner prompt grounding)
# ---------------------------------------------------------------------------


def build_evidence_bundle(
    repo_path: Path,
    *,
    hint_text: str = "",
    max_paths: int = 30,
) -> str:
    """Build a markdown grounding brief from the repo filesystem.

    Pure filesystem: directory tree, test setup detection, doc-file
    presence, and hint-noun path matching. No LLM, no network.
    """
    sections: list[str] = ["## Repository Evidence\n"]

    # --- Directory tree (2 levels deep) ---
    sections.append("### Directory Layout\n")
    for entry in sorted(repo_path.iterdir()):
        if entry.name in _EXCLUDE_DIRS or entry.name.startswith("."):
            continue
        if entry.is_dir():
            sections.append(f"- **{entry.name}/**")
            try:
                children = sorted(entry.iterdir())[:8]
                for child in children:
                    if child.name in _EXCLUDE_DIRS or child.name.startswith("."):
                        continue
                    name = child.name + "/" if child.is_dir() else child.name
                    sections.append(f"  - {name}")
            except PermissionError:
                pass
        elif entry.is_file():
            sections.append(f"- {entry.name}")
    sections.append("")

    # --- Test setup detection ---
    sections.append("### Test Setup\n")
    test_findings: list[str] = []
    if (repo_path / "pytest.ini").exists():
        test_findings.append("pytest.ini found")
    if (repo_path / "pyproject.toml").exists():
        try:
            import tomllib

            data = tomllib.loads(
                (repo_path / "pyproject.toml").read_text(encoding="utf-8")
            )
            if "tool" in data and "pytest" in data.get("tool", {}):
                test_findings.append("[tool.pytest] in pyproject.toml")
        except Exception:
            pass
    if (repo_path / "package.json").exists():
        try:
            pkg = json.loads(
                (repo_path / "package.json").read_text(encoding="utf-8")
            )
            if isinstance(pkg.get("scripts"), dict) and "test" in pkg["scripts"]:
                test_findings.append(
                    f'package.json scripts.test = "{pkg["scripts"]["test"]}"'
                )
        except Exception:
            pass
    if test_findings:
        for f in test_findings:
            sections.append(f"- {f}")
    else:
        sections.append("- No recognized test setup found")
    sections.append("")

    # --- Doc files ---
    sections.append("### Project Docs\n")
    for doc in _DOC_FILES:
        if (repo_path / doc).exists():
            sections.append(f"- `{doc}` present")
    sections.append("")

    # --- Hint-noun path matching ---
    if hint_text:
        sections.append("### Paths Matching Your Goal\n")
        words = set()
        for token in re.findall(r"[a-z]{4,}", hint_text.lower()):
            words.add(token)
        if words:
            matches: list[str] = []
            try:
                for f in repo_path.rglob("*"):
                    if any(part in _EXCLUDE_DIRS for part in f.parts):
                        continue
                    if f.is_dir():
                        continue
                    rel = str(f.relative_to(repo_path))
                    for word in words:
                        if word in rel.lower():
                            matches.append(rel)
                            break
                    if len(matches) >= max_paths:
                        break
            except Exception:
                pass
            if matches:
                for m in sorted(matches):
                    sections.append(f"- `{m}`")
            else:
                sections.append("- No matching paths found")
        sections.append("")

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# High-level entry point (mirrors plandrift.run_workgraph_plan_review)
# ---------------------------------------------------------------------------


def run_existdrift_check(
    *,
    repo_name: str,
    repo_path: Path,
    repo_snapshot: dict[str, Any] | None = None,
    policy_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run existdrift grounding check, mirroring plandrift's calling convention."""
    report = scan_grounding(repo_path, cfg=policy_cfg)
    # Override repo name to match caller convention.
    report["repo"] = repo_name
    return report


# ---------------------------------------------------------------------------
# Lane wrapper (run_as_lane for check.py integration)
# ---------------------------------------------------------------------------

_LANE_SEVERITY_MAP = {
    "critical": "critical",
    "high": "error",
    "medium": "warning",
    "low": "info",
    "info": "info",
}


def _map_severity(finding: dict[str, Any]) -> str:
    raw = str(finding.get("severity") or "").lower()
    return _LANE_SEVERITY_MAP.get(raw, "info")


def run_as_lane(project_dir: Path) -> Any:
    """Run existdrift and return results in the standard lane contract format."""
    from driftdriver.lane_contract import LaneFinding, LaneResult

    try:
        report = run_existdrift_check(
            repo_name=project_dir.name,
            repo_path=project_dir,
        )
    except Exception as exc:
        return LaneResult(
            lane="existdrift",
            findings=[LaneFinding(message=f"existdrift error: {exc}", severity="error")],
            exit_code=1,
            summary=f"existdrift failed: {exc}",
        )

    findings = []
    for f in report.get("findings", []):
        findings.append(LaneFinding(
            message=str(f.get("title") or f.get("category") or "grounding finding"),
            severity=_map_severity(f),
            file="",
            line=0,
            tags=[str(f.get("category") or "grounding")],
        ))

    summary_data = report.get("summary", {})
    summary_text = str(summary_data.get("narrative") or f"{len(findings)} findings")
    exit_code = 1 if findings else 0
    return LaneResult(
        lane="existdrift",
        findings=findings,
        exit_code=exit_code,
        summary=summary_text,
    )


# ---------------------------------------------------------------------------
# Followup task emission (mirrors plandrift.emit_plan_review_tasks)
# ---------------------------------------------------------------------------


def emit_grounding_followups(
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

    for row in (report.get("top_findings") or [])[:max(1, int(max_tasks))]:
        fingerprint = str(row.get("fingerprint") or "").strip()
        if not fingerprint:
            out["skipped"] = int(out["skipped"]) + 1
            continue
        task_id = f"existdrift-{fingerprint[:14]}"
        title = f"existdrift: {str(row.get('severity') or 'info')} {str(row.get('category') or 'finding')}"

        out["attempted"] = int(out["attempted"]) + 1
        result = guarded_add_drift_task(
            wg_dir=wg_dir,
            task_id=task_id,
            title=title,
            description=(
                f"Grounding finding: {row.get('title')}\n"
                f"Severity: {row.get('severity')}\n"
                f"Evidence: {row.get('evidence')}\n"
                f"Recommendation: {row.get('recommendation')}\n"
            ),
            lane_tag="existdrift",
            extra_tags=["grounding"],
            cwd=repo_path,
        )
        if result == "created":
            out["created"] = int(out["created"]) + 1
            out["tasks"].append({"task_id": task_id, "status": "created"})
        elif result == "existing":
            out["existing"] = int(out["existing"]) + 1
        else:
            out["skipped"] = int(out["skipped"]) + 1

    out["tasks"] = list(out.get("tasks") or [])[:80]
    out["errors"] = list(out.get("errors") or [])[:80]
    return out
