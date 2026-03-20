# ABOUTME: governancedrift — pipe layer for ecosystem conformance checking and Operational Health scoring.
# ABOUTME: Deterministic: no LLM. Classifies findings, computes deltas, scores Op. Health.
from __future__ import annotations

import json
import re
import socket
import subprocess
from enum import Enum
from pathlib import Path
from typing import Any


class FindingCategory(str, Enum):
    LIFECYCLE_VIOLATION = "lifecycle-violation"
    PROCESS_DEBT = "process-debt"
    ARCHITECTURE_GAP = "architecture-gap"
    POSTURE_MISMATCH = "posture-mismatch"
    UNPUSHED_WORK = "unpushed-work"


def classify_finding(
    *,
    repo: str,
    lifecycle: str,
    daemon_posture: str,
    daemon_socket_live: bool,
    live_agent_count: int,
    tasks_ready: int,
    north_star_present: bool,
) -> dict[str, Any] | None:
    """Classify one repo's conformance state. Returns a finding dict or None if clean."""

    # Lifecycle violation: retired/experimental repo with live daemon
    if lifecycle in ("retired", "experimental") and daemon_socket_live:
        return {
            "repo": repo,
            "category": FindingCategory.LIFECYCLE_VIOLATION,
            "severity": "high",
            "declared": f"lifecycle={lifecycle}, daemon_posture={daemon_posture}",
            "observed": f"daemon_socket_live=True, live_agents={live_agent_count}",
        }

    # Process debt: active repo with zombie agents (agents alive, zero ready work)
    if lifecycle == "active" and live_agent_count > 0 and tasks_ready == 0:
        return {
            "repo": repo,
            "category": FindingCategory.PROCESS_DEBT,
            "severity": "high",
            "declared": f"lifecycle={lifecycle}",
            "observed": f"live_agents={live_agent_count}, tasks_ready=0",
        }

    # Posture mismatch: daemon running when declared never
    if daemon_posture == "never" and daemon_socket_live:
        return {
            "repo": repo,
            "category": FindingCategory.POSTURE_MISMATCH,
            "severity": "medium",
            "declared": "daemon_posture=never",
            "observed": "daemon_socket_live=True",
        }

    # Architecture gap: active repo missing north star (skip for experimental/maintenance)
    if lifecycle == "active" and not north_star_present:
        return {
            "repo": repo,
            "category": FindingCategory.ARCHITECTURE_GAP,
            "severity": "medium",
            "declared": "lifecycle=active (north star expected)",
            "observed": "north_star_present=False",
        }

    return None


def compute_conformance_delta(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run classify_finding across all repos, return list of findings."""
    findings = []
    for repo in repos:
        finding = classify_finding(
            repo=repo["name"],
            lifecycle=repo.get("lifecycle", "active"),
            daemon_posture=repo.get("daemon_posture", "always-on"),
            daemon_socket_live=repo.get("daemon_socket_live", False),
            live_agent_count=repo.get("live_agent_count", 0),
            tasks_ready=repo.get("tasks_ready", 0),
            north_star_present=repo.get("north_star_present", False),
        )
        if finding:
            findings.append(finding)
    return findings


def score_operational_health(
    *,
    zombie_ratio: float,
    failed_abandoned_ratio: float,
    posture_alignment_ratio: float,
    abandoned_age_pressure: float,
) -> float:
    """Compute Operational Health score 0-100 from four normalized inputs (all 0-1)."""
    process_cleanliness = max(0.0, 1.0 - zombie_ratio) * 100
    task_debt_inverse = max(0.0, 1.0 - failed_abandoned_ratio) * 100
    daemon_alignment = posture_alignment_ratio * 100
    abandoned_inverse = max(0.0, 1.0 - abandoned_age_pressure) * 100

    score = (
        0.30 * process_cleanliness
        + 0.25 * task_debt_inverse
        + 0.25 * daemon_alignment
        + 0.20 * abandoned_inverse
    )
    return round(min(100.0, max(0.0, score)), 1)


def route_remediation(*, confidence: float, finding_category: FindingCategory) -> str:
    """Route finding to workgraph_task (automatic) or inbox_signal (human judgment)."""
    if confidence >= 0.85:
        return "workgraph_task"
    return "inbox_signal"


def build_model_prompt(finding: dict[str, Any]) -> str:
    """Build the deterministic prompt sent to the model for finding interpretation."""
    category = finding["category"]
    if hasattr(category, "value"):
        category = category.value
    return (
        f"Repo: {finding['repo']}\n"
        f"Category: {category}\n"
        f"Severity: {finding['severity']}\n"
        f"Declared: {finding['declared']}\n"
        f"Observed: {finding['observed']}\n\n"
        "Interpret this conformance finding. Provide:\n"
        "- confidence: float 0.0–1.0 (your confidence in this finding)\n"
        "- remediation: workgraph_task or inbox_signal\n"
        "- narrative: one sentence explaining why this matters\n"
        "- claude_prompt: actionable instruction for a Claude worker\n"
    )


def parse_model_response(output: str) -> dict[str, Any]:
    """Parse model output into structured fields. Safe defaults on missing fields."""
    result: dict[str, Any] = {
        "confidence": 0.5,
        "remediation_path": "inbox_signal",
        "narrative": "",
        "claude_prompt": None,
    }

    if m := re.search(r"confidence[:\s]+([0-9.]+)", output, re.IGNORECASE):
        try:
            result["confidence"] = float(m.group(1))
        except ValueError:
            pass

    if m := re.search(r"remediation[:\s]+(workgraph_task|inbox_signal)", output, re.IGNORECASE):
        result["remediation_path"] = m.group(1)

    if m := re.search(r"narrative[:\s]+(.+?)(?:\n|claude_prompt|$)", output, re.IGNORECASE | re.DOTALL):
        result["narrative"] = m.group(1).strip()

    if m := re.search(r"claude_prompt[:\s]+(.+?)(?:\n\n|$)", output, re.IGNORECASE | re.DOTALL):
        result["claude_prompt"] = m.group(1).strip()

    return result


_ENFORCEMENT_COOLDOWN: dict[str, float] = {}
_ENFORCEMENT_DEFAULT_COOLDOWN_SECONDS = 300


def enforce_daemon_posture(
    *,
    repos_payload: list[dict[str, Any]],
    dry_run: bool = False,
    max_stops: int = 5,
    cooldown_seconds: int = _ENFORCEMENT_DEFAULT_COOLDOWN_SECONDS,
    directive_log: Any | None = None,
) -> dict[str, Any]:
    """Enforce daemon posture: stop services on repos that should not have daemons running.

    Targets:
    - lifecycle=retired or experimental with service_running=True
    - daemon_posture=never with service_running=True

    Returns summary dict with checked/candidates/actions.
    """
    import time as _time

    now = _time.time()
    checked = 0
    candidates = 0
    actions: list[dict[str, Any]] = []

    for row in repos_payload:
        if not isinstance(row, dict):
            continue
        checked += 1

        repo_name = str(row.get("name") or "")
        repo_path_raw = str(row.get("path") or "")
        if not repo_name:
            continue
        if not bool(row.get("workgraph_exists")):
            continue
        if not bool(row.get("service_running")):
            continue

        lifecycle = str(row.get("lifecycle") or "active")
        daemon_posture = str(row.get("daemon_posture") or "always-on")

        # Determine if this repo needs enforcement
        reason_category = None
        if lifecycle in ("retired", "experimental"):
            reason_category = "lifecycle-violation"
        elif daemon_posture == "never":
            reason_category = "posture-mismatch"

        if reason_category is None:
            continue

        candidates += 1

        if len(actions) >= max(1, max_stops):
            continue

        # Cooldown check
        key = repo_name
        last = _ENFORCEMENT_COOLDOWN.get(key, 0.0)
        if not dry_run and now - last < max(1, cooldown_seconds):
            continue

        action_entry: dict[str, Any] = {
            "repo": repo_name,
            "path": repo_path_raw,
            "action": "stop_service",
            "reason_category": reason_category,
            "lifecycle": lifecycle,
            "daemon_posture": daemon_posture,
            "dry_run": dry_run,
            "ok": True,
            "error": "",
        }

        if not dry_run:
            _ENFORCEMENT_COOLDOWN[key] = now
            repo_path = Path(repo_path_raw).expanduser()
            wg_dir = repo_path / ".workgraph"

            if directive_log is not None:
                try:
                    from driftdriver.directives import Action, Directive
                    from driftdriver.executor_shim import ExecutorShim

                    directive = Directive(
                        source="governance_enforcement",
                        repo=repo_name,
                        action=Action.STOP_SERVICE,
                        params={"repo": str(wg_dir)},
                        reason=f"daemon posture enforcement: {reason_category} (lifecycle={lifecycle}, posture={daemon_posture})",
                    )
                    shim = ExecutorShim(wg_dir=wg_dir, log=directive_log, timeout=15.0)
                    shim_result = shim.execute(directive)
                    action_entry["ok"] = shim_result == "completed"
                    if not action_entry["ok"]:
                        action_entry["error"] = str(shim_result)
                except Exception as exc:
                    action_entry["ok"] = False
                    action_entry["error"] = str(exc)[:200]
            else:
                try:
                    result = subprocess.run(
                        ["wg", "--dir", str(wg_dir), "service", "stop"],
                        cwd=str(repo_path),
                        capture_output=True,
                        text=True,
                        timeout=15.0,
                    )
                    action_entry["ok"] = result.returncode == 0 or "not running" in result.stdout.lower()
                    if not action_entry["ok"]:
                        action_entry["error"] = (result.stderr or result.stdout)[:200]
                except Exception as exc:
                    action_entry["ok"] = False
                    action_entry["error"] = str(exc)[:200]

        actions.append(action_entry)

    return {
        "enabled": True,
        "checked": checked,
        "enforcement_candidates": candidates,
        "actions": actions,
        "dry_run": dry_run,
        "max_stops": max(1, max_stops),
    }


def check_daemon_socket_live(repo_path: Path) -> bool:
    """Check if the workgraph daemon socket is listening for a repo."""
    sock_path = repo_path / ".workgraph" / "service" / "daemon.sock"
    if not sock_path.exists():
        return False
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            s.connect(str(sock_path))
            return True
    except (OSError, ConnectionRefusedError):
        return False


def observe_repo(repo_path: Path) -> dict[str, Any]:
    """Collect observed reality for one repo. Returns dict with conformance-relevant fields."""
    daemon_live = check_daemon_socket_live(repo_path)

    # Count live Claude agent processes for this repo
    live_agent_count = 0
    if repo_path.exists():
        try:
            result = subprocess.run(
                ["pgrep", "-f", f"claude.*{repo_path.name}"],
                capture_output=True, text=True, timeout=5,
            )
            live_agent_count = len(result.stdout.strip().splitlines()) if result.returncode == 0 else 0
        except Exception:
            live_agent_count = 0

    # Count task statuses from graph.jsonl
    graph = repo_path / ".workgraph" / "graph.jsonl"
    tasks_ready = 0
    tasks_failed = 0
    tasks_abandoned = 0
    tasks_total = 0
    if graph.exists():
        for line in graph.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
                tasks_total += 1
                status = t.get("status", "")
                if status == "open":
                    tasks_ready += 1
                elif status == "failed":
                    tasks_failed += 1
                elif status == "abandoned":
                    tasks_abandoned += 1
            except Exception:
                pass

    north_star = (
        (repo_path / "NORTH_STAR.md").exists()
        or (repo_path / "docs" / "NORTH_STAR.md").exists()
    )

    return {
        "daemon_socket_live": daemon_live,
        "live_agent_count": live_agent_count,
        "tasks_ready": tasks_ready,
        "tasks_failed": tasks_failed,
        "tasks_abandoned": tasks_abandoned,
        "tasks_total": tasks_total,
        "north_star_present": north_star,
    }


def collect_ecosystem_governance(
    repos: list[dict[str, Any]],
    workspace_root: Path,
) -> dict[str, Any]:
    """
    Run a full governance collection pass over all repos.
    Returns {"conformance_findings": [...], "op_health_inputs": {...}}.
    """
    observed_repos: list[dict[str, Any]] = []
    total_live_agents = 0
    total_failed = 0
    total_abandoned = 0
    total_tasks = 0
    posture_aligned = 0

    for repo_meta in repos:
        repo_path = workspace_root / repo_meta["name"]
        if not repo_path.exists():
            continue
        observed = observe_repo(repo_path)
        merged = {**repo_meta, **observed}
        observed_repos.append(merged)

        total_live_agents += observed["live_agent_count"]
        total_failed += observed["tasks_failed"]
        total_abandoned += observed["tasks_abandoned"]
        total_tasks += observed["tasks_total"]

        declared = repo_meta.get("daemon_posture", "always-on")
        live = observed["daemon_socket_live"]
        if declared == "always-on" and live:
            posture_aligned += 1
        elif declared == "never" and not live:
            posture_aligned += 1
        elif declared == "on-demand":
            posture_aligned += 1

    findings = compute_conformance_delta(observed_repos)

    total_processes = max(1, total_live_agents)
    zombie_agents = sum(
        r["live_agent_count"] for r in observed_repos
        if r["live_agent_count"] > 0 and r["tasks_ready"] == 0
    )

    op_health_inputs = {
        "zombie_ratio": min(1.0, zombie_agents / total_processes),
        "failed_abandoned_ratio": min(1.0, (total_failed + total_abandoned) / max(1, total_tasks)),
        "posture_alignment_ratio": posture_aligned / max(1, len(observed_repos)),
        "abandoned_age_pressure": min(1.0, total_abandoned / max(1, total_tasks)),
    }

    return {
        "conformance_findings": [
            {**f, "category": f["category"].value if hasattr(f["category"], "value") else f["category"]}
            for f in findings
        ],
        "op_health_inputs": op_health_inputs,
    }
