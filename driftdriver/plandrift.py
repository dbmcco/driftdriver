# ABOUTME: Plan drift module — reviews workgraph structure for test gates, loopbacks, and continuation edges
# ABOUTME: Produces model-mediated review prompts and emits repo-local corrective planning tasks
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from hashlib import sha1
from pathlib import Path
from typing import Any


_SEVERITY_RANK = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

_TEST_RE = re.compile(r"\b(test|integration|e2e|playwright|regression|verify|validation)\b", re.IGNORECASE)
_INTEGRATION_RE = re.compile(r"\b(integration|contract|api)\b", re.IGNORECASE)
_E2E_RE = re.compile(r"\b(e2e|playwright|ui|journey|smoke)\b", re.IGNORECASE)
_LOOPBACK_RE = re.compile(r"\b(loopback|retry|rollback|recovery|remediate|fixup|fallback)\b", re.IGNORECASE)
_CONTINUATION_RE = re.compile(
    r"\b(double[- ]shot[- ]latte|latte|continuation|handoff|resume|session[- ]driver|tmux|supervisor)\b",
    re.IGNORECASE,
)
_IMPLEMENTATION_RE = re.compile(
    r"\b(implement|build|feature|fix|refactor|ship|deliver|migrate|integrate|wire|create)\b",
    re.IGNORECASE,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(parts: list[str]) -> str:
    key = "|".join(str(part or "").strip().lower() for part in parts)
    return sha1(key.encode("utf-8")).hexdigest()  # noqa: S324 - non-crypto identity hash


def _normalize_cfg(policy_cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(policy_cfg) if isinstance(policy_cfg, dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "interval_seconds": max(0, int(raw.get("interval_seconds", 14400))),
        "max_findings_per_repo": max(1, int(raw.get("max_findings_per_repo", 40))),
        "emit_review_tasks": bool(raw.get("emit_review_tasks", True)),
        "max_review_tasks_per_repo": max(1, int(raw.get("max_review_tasks_per_repo", 3))),
        "require_integration_tests": bool(raw.get("require_integration_tests", True)),
        "require_e2e_tests": bool(raw.get("require_e2e_tests", True)),
        "require_failure_loopbacks": bool(raw.get("require_failure_loopbacks", True)),
        "require_continuation_edges": bool(raw.get("require_continuation_edges", True)),
        "continuation_runtime": str(raw.get("continuation_runtime", "double-shot-latte") or "double-shot-latte"),
        "orchestration_runtime": str(raw.get("orchestration_runtime", "claude-session-driver") or "claude-session-driver"),
        "review_loop_mode": str(raw.get("review_loop_mode", "trycycle-inspired") or "trycycle-inspired"),
        "fresh_reviewer_required": bool(raw.get("fresh_reviewer_required", True)),
        "review_rounds": max(1, int(raw.get("review_rounds", 2))),
        "allow_tmux_fallback": bool(raw.get("allow_tmux_fallback", True)),
        "hard_stop_on_critical": bool(raw.get("hard_stop_on_critical", False)),
    }


def _normalize_dependencies(raw_after: Any) -> list[str]:
    if not isinstance(raw_after, list):
        return []
    out: list[str] = []
    for row in raw_after:
        if isinstance(row, dict):
            dep = str(row.get("id") or "").strip()
            if dep:
                out.append(dep)
            continue
        dep = str(row).strip()
        if dep:
            out.append(dep)
    return out


def _normalize_tags(raw_tags: Any) -> list[str]:
    if not isinstance(raw_tags, list):
        return []
    out: list[str] = []
    for row in raw_tags:
        tag = str(row or "").strip().lower()
        if tag:
            out.append(tag)
    return out


def _task_text(task: dict[str, Any]) -> str:
    bits: list[str] = [
        str(task.get("id") or ""),
        str(task.get("title") or ""),
        str(task.get("description") or ""),
        " ".join(str(row or "") for row in (task.get("tags") or []) if str(row or "").strip()),
    ]
    return " ".join(part for part in bits if part).strip()


def _is_test_task(task: dict[str, Any]) -> bool:
    text = _task_text(task)
    return bool(_TEST_RE.search(text))


def _is_integration_task(task: dict[str, Any]) -> bool:
    text = _task_text(task)
    return bool(_INTEGRATION_RE.search(text))


def _is_e2e_task(task: dict[str, Any]) -> bool:
    text = _task_text(task)
    return bool(_E2E_RE.search(text))


def _is_loopback_task(task: dict[str, Any]) -> bool:
    text = _task_text(task)
    return bool(_LOOPBACK_RE.search(text))


def _is_continuation_task(task: dict[str, Any]) -> bool:
    text = _task_text(task)
    return bool(_CONTINUATION_RE.search(text))


def _is_implementation_task(task: dict[str, Any]) -> bool:
    if _is_test_task(task):
        return False
    text = _task_text(task)
    return bool(_IMPLEMENTATION_RE.search(text))


def _looks_like_web_repo(repo_path: Path) -> bool:
    if not (repo_path / "package.json").exists():
        return False
    markers = ("next.config.js", "next.config.mjs", "vite.config.ts", "playwright.config.ts", "playwright.config.js")
    if any((repo_path / marker).exists() for marker in markers):
        return True
    src = repo_path / "src"
    if not src.exists():
        return False
    for suffix in (".tsx", ".jsx", ".vue", ".svelte"):
        if any(src.rglob(f"*{suffix}")):
            return True
    return False


def _read_workgraph_tasks(repo_path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
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
            "after": _normalize_dependencies(row.get("after") or row.get("dependencies") or row.get("depends_on")),
            "tags": _normalize_tags(row.get("tags")),
        }

    return tasks, errors[:12]


def _build_dependents(tasks: dict[str, dict[str, Any]]) -> tuple[dict[str, list[str]], int]:
    dependents: dict[str, list[str]] = {}
    missing = 0
    for task in tasks.values():
        task_id = str(task.get("id") or "")
        for dep in _normalize_dependencies(task.get("after")):
            if dep not in tasks:
                missing += 1
            dependents.setdefault(dep, []).append(task_id)
    return dependents, missing


def _downstream_has(
    *,
    start_task_id: str,
    tasks: dict[str, dict[str, Any]],
    dependents: dict[str, list[str]],
    predicate: Any,
    max_depth: int = 3,
) -> bool:
    seen = {start_task_id}
    frontier: list[tuple[str, int]] = [(start_task_id, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            continue
        for child in dependents.get(current, []):
            if child in seen:
                continue
            seen.add(child)
            task = tasks.get(child)
            if not isinstance(task, dict):
                continue
            if predicate(task):
                return True
            frontier.append((child, depth + 1))
    return False


def _planning_prompt(repo_name: str, finding: dict[str, Any], cfg: dict[str, Any]) -> str:
    fingerprint = str(finding.get("fingerprint") or "")
    category = str(finding.get("category") or "planning-gap")
    severity = str(finding.get("severity") or "medium")
    evidence = str(finding.get("evidence") or "")
    recommendation = str(finding.get("recommendation") or "")
    return (
        f"In `{repo_name}`, triage plandrift finding `{fingerprint}` ({severity}/{category}). "
        f"Evidence: {evidence}. Add exact Workgraph dependency updates for integration/e2e gates, failure loopbacks, "
        f"and continuation routing via {cfg['continuation_runtime']} with {cfg['orchestration_runtime']} "
        f"(tmux fallback allowed={cfg['allow_tmux_fallback']}). Recommendation seed: {recommendation}"
    )


def run_workgraph_plan_review(
    *,
    repo_name: str,
    repo_path: Path,
    repo_snapshot: dict[str, Any] | None = None,
    policy_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = _normalize_cfg(policy_cfg)
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
                "narrative": "plandrift disabled by policy",
            },
            "findings": [],
            "top_findings": [],
            "recommended_reviews": [],
            "model_contract": {},
            "errors": [],
        }

    snapshot = dict(repo_snapshot) if isinstance(repo_snapshot, dict) else {}
    tasks, errors = _read_workgraph_tasks(repo_path)
    dependents, missing_refs = _build_dependents(tasks)

    findings: list[dict[str, Any]] = []
    web_repo = _looks_like_web_repo(repo_path)
    active_states = {"open", "ready", "in-progress"}
    implementation_tasks = [
        row
        for row in tasks.values()
        if str(row.get("status") or "").strip().lower() in active_states and _is_implementation_task(row)
    ]
    test_tasks = [
        row
        for row in tasks.values()
        if str(row.get("status") or "").strip().lower() in active_states and _is_test_task(row)
    ]
    in_progress_count = sum(1 for row in tasks.values() if str(row.get("status") or "").strip().lower() == "in-progress")
    ready_count = sum(1 for row in tasks.values() if str(row.get("status") or "").strip().lower() in ("open", "ready"))
    blocked_open = max(0, int(snapshot.get("blocked_open") or 0))
    missing_dependencies = max(missing_refs, max(0, int(snapshot.get("missing_dependencies") or 0)))

    for task in implementation_tasks:
        task_id = str(task.get("id") or "")
        task_title = str(task.get("title") or "")
        missing_checks: list[str] = []
        if cfg["require_integration_tests"] and not _downstream_has(
            start_task_id=task_id,
            tasks=tasks,
            dependents=dependents,
            predicate=_is_integration_task,
        ):
            missing_checks.append("integration")
        if web_repo and cfg["require_e2e_tests"] and not _downstream_has(
            start_task_id=task_id,
            tasks=tasks,
            dependents=dependents,
            predicate=_is_e2e_task,
        ):
            missing_checks.append("e2e")
        if not missing_checks:
            continue
        severity = "high" if str(task.get("status") or "") == "in-progress" else "medium"
        missing_label = "+".join(missing_checks)
        findings.append(
            {
                "fingerprint": _fingerprint([repo_name, task_id, "missing-intervening-tests", missing_label]),
                "category": "missing-intervening-tests",
                "severity": severity,
                "title": "Implementation task lacks intervening test gates",
                "evidence": f"task={task_id} ({task_title}); missing={missing_label}",
                "recommendation": (
                    f"Add explicit {missing_label} task(s) blocked-by `{task_id}` and keep them on the critical path before completion."
                ),
            }
        )

    if cfg["require_failure_loopbacks"]:
        for task in test_tasks:
            task_id = str(task.get("id") or "")
            has_loopback = _downstream_has(
                start_task_id=task_id,
                tasks=tasks,
                dependents=dependents,
                predicate=_is_loopback_task,
            )
            if has_loopback:
                continue
            findings.append(
                {
                    "fingerprint": _fingerprint([repo_name, task_id, "missing-failure-loopback"]),
                    "category": "missing-failure-loopback",
                    "severity": "medium",
                    "title": "Test task has no explicit failure loopback",
                    "evidence": f"task={task_id} ({task.get('title')}) has no downstream recovery/remediation branch",
                    "recommendation": (
                        f"Create a loopback task dependent on `{task_id}` that routes failures back to the owning implementation task."
                    ),
                }
            )

    if cfg["require_continuation_edges"] and in_progress_count > 0:
        has_continuation = any(_is_continuation_task(row) for row in tasks.values())
        if not has_continuation:
            severity = "high" if ready_count <= 0 else "medium"
            findings.append(
                {
                    "fingerprint": _fingerprint([repo_name, "continuation-bridge-gap", str(in_progress_count), str(ready_count)]),
                    "category": "continuation-bridge-gap",
                    "severity": severity,
                    "title": "In-progress work has no continuation bridge task",
                    "evidence": f"in_progress={in_progress_count}; ready={ready_count}; continuation_tasks=0",
                    "recommendation": (
                        "Add a continuation/handoff task to keep progress alive at context limits via "
                        f"{cfg['continuation_runtime']} with {cfg['orchestration_runtime']}."
                    ),
                }
            )

        session_hook = repo_path / ".workgraph" / "handlers" / "session-start.sh"
        if not session_hook.exists():
            findings.append(
                {
                    "fingerprint": _fingerprint([repo_name, "session-driver-hook-missing"]),
                    "category": "session-driver-hook-missing",
                    "severity": "low",
                    "title": "Session orchestration hook missing",
                    "evidence": ".workgraph/handlers/session-start.sh not found",
                    "recommendation": (
                        "Install session-start/task lifecycle hooks so orchestration state is logged and recoverable."
                    ),
                }
            )

    if missing_dependencies > 0 or blocked_open > 0:
        severity = "high" if missing_dependencies > 0 else "medium"
        findings.append(
            {
                "fingerprint": _fingerprint([repo_name, "dependency-integrity-gap", str(missing_dependencies), str(blocked_open)]),
                "category": "dependency-integrity-gap",
                "severity": severity,
                "title": "Dependency integrity gaps detected in workgraph",
                "evidence": f"missing_dependencies={missing_dependencies}; blocked_open={blocked_open}",
                "recommendation": "Repair dependency references and unblock open tasks before adding new scope.",
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
            -_SEVERITY_RANK.get(str(row.get("severity") or "").lower(), 0),
            str(row.get("category") or ""),
            str(row.get("evidence") or ""),
        ),
    )
    top_findings = ordered[: int(cfg["max_findings_per_repo"])]
    for row in top_findings:
        row["model_prompt"] = _planning_prompt(repo_name, row, cfg)

    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for row in top_findings:
        sev = str(row.get("severity") or "").lower()
        if sev in counts:
            counts[sev] += 1
    total = len(top_findings)
    raw_total = len(ordered)
    at_risk = counts["critical"] > 0 or counts["high"] > 0 or missing_dependencies > 0
    narrative = (
        f"plandrift reviewed `{repo_name}`: {total} prioritized planning findings "
        f"(critical={counts['critical']}, high={counts['high']}, medium={counts['medium']}, low={counts['low']}). "
        f"Raw findings={raw_total}; implementation_tasks={len(implementation_tasks)}; test_tasks={len(test_tasks)}."
    )
    recommended = [row for row in top_findings if _SEVERITY_RANK.get(str(row.get("severity") or "").lower(), 0) >= 2][:10]
    model_contract = {
        "decision_owner": "model",
        "triage_objective": "Ensure workgraph plans enforce test gates, loopbacks, and forward continuation.",
        "review_loop_mode": str(cfg.get("review_loop_mode") or "default"),
        "fresh_reviewer_required": bool(cfg.get("fresh_reviewer_required", False)),
        "review_rounds": int(cfg.get("review_rounds") or 1),
        "required_outputs": [
            "dependency_updates",
            "intervening_tests",
            "failure_loopbacks",
            "continuation_strategy",
            "orchestration_runtime_choice",
        ],
        "prompt_seed": (
            "Review plandrift findings and output exact task/dependency updates that preserve active work while "
            "adding integration/e2e validation and explicit failure recovery paths. Use a trycycle-inspired loop: "
            "plan/review first, then build/review with a fresh reviewer perspective each round."
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
            "implementation_tasks": len(implementation_tasks),
            "test_tasks": len(test_tasks),
            "missing_dependencies": missing_dependencies,
            "blocked_open": blocked_open,
            "narrative": narrative,
        },
        "findings": ordered,
        "top_findings": top_findings,
        "recommended_reviews": recommended,
        "model_contract": model_contract,
        "errors": errors[:20],
    }


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


def emit_plan_review_tasks(
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
        fingerprint = str(row.get("fingerprint") or "").strip()
        if not fingerprint:
            out["skipped"] = int(out["skipped"]) + 1
            continue
        task_id = f"plandrift-{fingerprint[:14]}"
        title = f"plandrift: {str(row.get('severity') or 'medium')} {str(row.get('category') or 'finding')}"
        prompt = str(row.get("model_prompt") or "")
        desc = (
            "Workgraph planning integrity review task.\n\n"
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
                "plandrift",
                "-t",
                "planning",
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
