# ABOUTME: Tracks upstream external repos (graphwork/workgraph, agentbureau/agency).
# ABOUTME: Pass 1: fetch, classify changes, LLM eval, risk route. Pass 2: internal unpushed work.
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# --- Change classifier ---

_SCHEMA_PATTERNS = {
    "graph.jsonl", "schema", ".json", ".proto", "migrations",
    "models.rs", "models.py", "schema.rs",
}
_API_PATTERNS = {
    "cli", "commands", "cmd", "main.rs", "main.py", "__main__",
    "api.rs", "api.py", "interface",
}
_INTERNALS_PATTERNS = {
    "tui", "views", "readme", "changelog", "contributing",
    "license", ".md", "docs/", "tests/", "fixtures/",
}
_API_KEYWORDS = {
    "add command", "new command", "new flag", "new option",
    "wg retract", "wg cascade", "wg decompose", "wg compact",
    "breaking", "rename", "remove command",
}


def classify_changes(changed_files: list[str], commit_subjects: list[str]) -> str:
    """Classify a change set into schema/api-surface/behavior/internals-only.

    Priority: schema > api-surface > behavior > internals-only.
    Deterministic — no LLM call needed.
    """
    files_lower = {f.lower() for f in changed_files}
    subjects_lower = " ".join(commit_subjects).lower()

    # Schema: data structure files
    if any(
        pat in f or f.endswith(pat)
        for f in files_lower
        for pat in _SCHEMA_PATTERNS
    ):
        return "schema"

    # API surface: CLI/command changes or API keywords in subjects
    if any(pat in f for f in files_lower for pat in _API_PATTERNS):
        return "api-surface"
    if any(kw in subjects_lower for kw in _API_KEYWORDS):
        return "api-surface"

    # Internals-only: TUI, docs, tests
    non_internal = [
        f for f in files_lower
        if not any(pat in f for pat in _INTERNALS_PATTERNS)
    ]
    if not non_internal:
        return "internals-only"

    return "behavior"


# --- LLM evaluation ---

_ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
_ANTHROPIC_API_VERSION = "2023-06-01"
_HAIKU_MODEL = "claude-haiku-4-5-20251001"
_SONNET_MODEL = "claude-sonnet-4-6"

_DRIFTDRIVER_CONTEXT = (
    "driftdriver orchestrates drift checks and factory tasks using workgraph (wg CLI). "
    "Key integration points: drift_task_guard.py, agency_adapter.py, ecosystem_hub/snapshot.py. "
    "Changes to wg CLI commands, graph.jsonl schema, or coordinator behavior directly affect us."
)


def _default_llm_caller(model: str, prompt: str) -> dict[str, Any]:
    """Call Anthropic API with a simple text prompt, return parsed JSON from the response."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    payload = {
        "model": model,
        "max_tokens": 512,
        "messages": [{"role": "user", "content": prompt}],
    }
    request = Request(
        _ANTHROPIC_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": _ANTHROPIC_API_VERSION,
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(f"Anthropic API error {exc.code}") from exc
    content = body.get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "").strip()
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end])
                except json.JSONDecodeError:
                    pass
    return {}


def triage_relevance(
    changed_files: list[str],
    commit_subjects: list[str],
    category: str,
    *,
    llm_caller: Callable[[str, str], dict[str, Any]] | None = None,
) -> float:
    """Return relevance score 0-1. internals-only always returns 0 (no LLM call)."""
    if category == "internals-only":
        return 0.0
    caller = llm_caller or _default_llm_caller
    files_summary = ", ".join(changed_files[:10])
    subjects_summary = "; ".join(commit_subjects[:5])
    prompt = (
        f"You are evaluating whether a change to an upstream dependency is relevant to driftdriver.\n\n"
        f"Context: {_DRIFTDRIVER_CONTEXT}\n\n"
        f"Changed files: {files_summary}\n"
        f"Commit subjects: {subjects_summary}\n"
        f"Category: {category}\n\n"
        f'Respond with ONLY a JSON object: {{"relevance_score": <0.0-1.0>, "rationale": "<one sentence>"}}'
    )
    try:
        result = caller(_HAIKU_MODEL, prompt)
        score = float(result.get("relevance_score", 0.0))
        return max(0.0, min(1.0, score))
    except Exception:
        return 0.0


def deep_eval_change(
    changed_files: list[str],
    commit_subjects: list[str],
    category: str,
    context: str,
    *,
    llm_caller: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Sonnet deep eval — impact, risk_score (0-1), recommended_action."""
    caller = llm_caller or _default_llm_caller
    files_summary = ", ".join(changed_files[:20])
    subjects_summary = "; ".join(commit_subjects[:10])
    prompt = (
        f"Evaluate this upstream change for driftdriver adoption.\n\n"
        f"Context: {context}\n"
        f"Category: {category}\n"
        f"Changed files: {files_summary}\n"
        f"Commit subjects: {subjects_summary}\n\n"
        f"Respond with ONLY JSON:\n"
        f'{{"impact": "<low|moderate|high>", "value_gained": "<one sentence>", '
        f'"risk_introduced": "<one sentence>", "risk_score": <0.0-1.0>, '
        f'"recommended_action": "<adopt|watch|ignore>"}}'
    )
    try:
        result = caller(_SONNET_MODEL, prompt)
        risk = float(result.get("risk_score", 0.5))
        result["risk_score"] = max(0.0, min(1.0, risk))
        if result.get("recommended_action") not in ("adopt", "watch", "ignore"):
            result["recommended_action"] = "watch"
        return result
    except Exception:
        return {
            "impact": "unknown",
            "value_gained": "eval failed",
            "risk_introduced": "unknown",
            "risk_score": 0.5,
            "recommended_action": "watch",
        }


# --- Pass 1: External repos ---

_RELEVANCE_THRESHOLD = 0.3
_RISK_ALERT_THRESHOLD = 0.4


def _git_current_sha(repo_path: Path, branch: str) -> str | None:
    """Return current HEAD SHA of a local git repo for the given ref."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", branch],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except Exception:
        pass
    return None


def _git_changed_files(repo_path: Path, old_sha: str, new_sha: str) -> list[str]:
    """Return list of files changed between two SHAs."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", old_sha, new_sha],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.splitlines() if f.strip()]
    except Exception:
        pass
    return []


def _git_commit_subjects(repo_path: Path, old_sha: str, new_sha: str) -> list[str]:
    """Return commit subjects between two SHAs."""
    try:
        result = subprocess.run(
            ["git", "log", "--format=%s", f"{old_sha}..{new_sha}", "-n", "50"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        if result.returncode == 0:
            return [s for s in result.stdout.splitlines() if s.strip()]
    except Exception:
        pass
    return []


_STATE_FILENAME = "upstream-tracker-last.json"


def run_pass1(
    config: dict[str, Any],
    pins_path: Path,
    *,
    llm_caller: Callable[[str, str], dict[str, Any]] | None = None,
    deep_eval_caller: Callable[[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate all tracked external repos. Returns list of evaluation results."""
    from driftdriver.upstream_pins import get_sha, is_snoozed, load_pins, save_pins, set_sha

    pins = load_pins(pins_path)
    results: list[dict[str, Any]] = []
    pins_updated = False

    for repo_cfg in config.get("external_repos") or []:
        repo_name = str(repo_cfg.get("name") or "")
        local_path_str = str(repo_cfg.get("local_path") or "")
        branches = list(repo_cfg.get("branches") or [])
        if not repo_name or not local_path_str or not branches:
            continue
        local_path = Path(local_path_str)
        if not local_path.exists():
            continue

        for branch in branches:
            if is_snoozed(pins, repo_name, branch):
                continue

            current_sha = _git_current_sha(local_path, branch)
            if not current_sha:
                continue

            pinned_sha = get_sha(pins, repo_name, branch)
            if pinned_sha == current_sha:
                continue  # No change

            if pinned_sha:
                changed_files = _git_changed_files(local_path, pinned_sha, current_sha)
                subjects = _git_commit_subjects(local_path, pinned_sha, current_sha)
            else:
                changed_files = []
                subjects = []

            category = classify_changes(changed_files, subjects)
            relevance = triage_relevance(
                changed_files, subjects, category, llm_caller=llm_caller
            )

            eval_result: dict[str, Any] = {
                "repo": repo_name,
                "branch": branch,
                "old_sha": pinned_sha,
                "new_sha": current_sha,
                "category": category,
                "relevance_score": relevance,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if relevance > _RELEVANCE_THRESHOLD:
                deep = deep_eval_change(
                    changed_files,
                    subjects,
                    category,
                    context=_DRIFTDRIVER_CONTEXT,
                    llm_caller=deep_eval_caller,
                )
                eval_result.update(deep)
                risk_score = float(deep.get("risk_score", 0.5))
            else:
                risk_score = 0.1
                eval_result["recommended_action"] = "ignore"

            eval_result["action"] = (
                "alert" if risk_score >= _RISK_ALERT_THRESHOLD else "auto_adopt"
            )

            pins = set_sha(pins, repo_name, branch, current_sha)
            pins_updated = True
            results.append(eval_result)

    if pins_updated:
        save_pins(pins_path, pins)

    # Persist state for snapshot reads
    state_path = pins_path.parent / _STATE_FILENAME
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "results": results,
            }, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

    return results


# --- Pass 2: Internal repos ---

_AHEAD_THRESHOLD = 3


def run_pass2(repos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Scan enrolled repos for unpushed commits or dirty trees.

    Returns a list of conformance-style findings with category='unpushed-work'.
    Consumes data already in the hub snapshot — no additional git calls.
    """
    findings = []
    for repo in repos:
        if not isinstance(repo, dict) or not repo.get("exists"):
            continue
        name = str(repo.get("name") or "")
        if not name:
            continue
        ahead = int(repo.get("ahead") or 0)
        dirty = bool(repo.get("working_tree_dirty"))

        if ahead >= _AHEAD_THRESHOLD:
            findings.append({
                "repo": name,
                "category": "unpushed-work",
                "severity": "medium",
                "declared": f"ahead_threshold={_AHEAD_THRESHOLD}",
                "observed": f"ahead={ahead} commits not pushed",
            })
        elif dirty:
            findings.append({
                "repo": name,
                "category": "unpushed-work",
                "severity": "low",
                "declared": "working_tree=clean",
                "observed": "working_tree_dirty=True",
            })
    return findings


# --- Snapshot entry ---

def build_snapshot_entry(
    repos: list[dict[str, Any]],
    *,
    state_dir: Path,
) -> dict[str, Any]:
    """Build the upstream_tracker dict for inclusion in the hub snapshot.

    Calls run_pass2 inline (data already available). Reads last pass1 result
    from state_dir/upstream-tracker-last.json (written by run_pass1 cycle task).
    """
    pass2_findings = run_pass2(repos)

    state_file = state_dir / _STATE_FILENAME
    pass1_last: dict[str, Any] = {}
    if state_file.exists():
        try:
            pass1_last = json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass1_last = {}

    return {
        "pass1_last_run": pass1_last.get("timestamp"),
        "pass1_results": pass1_last.get("results", []),
        "pass2_findings": pass2_findings,
    }
