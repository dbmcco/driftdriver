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

from driftdriver.llm_meter import extract_usage_from_api_response, record_spend
from driftdriver.signal_gate import should_fire as _sg_should_fire, record_fire as _sg_record_fire

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

    # Record LLM spend
    usage = extract_usage_from_api_response(body)
    if usage:
        record_spend(
            agent="upstream-tracker",
            model=model,
            input_tokens=usage[0],
            output_tokens=usage[1],
        )

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


def _save_gated_result(gate_dir: Path, gate_name: str, result: Any) -> None:
    """Persist a gated LLM result alongside the signal-gate state file."""
    gate_dir.mkdir(parents=True, exist_ok=True)
    path = gate_dir / f"{gate_name}.result.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(result, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load_gated_result(gate_dir: Path, gate_name: str) -> Any | None:
    """Load a persisted gated LLM result.  Returns None on miss or error."""
    path = gate_dir / f"{gate_name}.result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def triage_relevance(
    changed_files: list[str],
    commit_subjects: list[str],
    category: str,
    *,
    llm_caller: Callable[[str, str], dict[str, Any]] | None = None,
    gate_dir: Path | None = None,
) -> float:
    """Return relevance score 0-1. internals-only always returns 0 (no LLM call).

    When *gate_dir* is provided, uses signal-gate disk persistence to skip
    the LLM call when the input (files, subjects, category) hasn't changed.
    """
    if category == "internals-only":
        return 0.0

    # Signal gate check
    gate_input = (sorted(changed_files), sorted(commit_subjects), category)
    gate_name = "upstream-triage"
    effective_gate_dir = gate_dir if gate_dir is not None else Path(".workgraph/.signal-gates")

    if not _sg_should_fire(gate_name, gate_input, gate_dir=effective_gate_dir):
        cached = _load_gated_result(effective_gate_dir, gate_name)
        if cached is not None:
            return float(cached)

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
        score = max(0.0, min(1.0, score))
    except Exception:
        return 0.0

    _sg_record_fire(gate_name, gate_input, gate_dir=effective_gate_dir)
    _save_gated_result(effective_gate_dir, gate_name, score)
    return score


def deep_eval_change(
    changed_files: list[str],
    commit_subjects: list[str],
    category: str,
    context: str,
    *,
    llm_caller: Callable[[str, str], dict[str, Any]] | None = None,
    gate_dir: Path | None = None,
) -> dict[str, Any]:
    """Sonnet deep eval — impact, risk_score (0-1), recommended_action.

    When *gate_dir* is provided, uses signal-gate disk persistence to skip
    the LLM call when the input hasn't changed since the last successful call.
    """
    # Signal gate check
    gate_input = (sorted(changed_files), sorted(commit_subjects), category, context)
    gate_name = "upstream-deepeval"
    effective_gate_dir = gate_dir if gate_dir is not None else Path(".workgraph/.signal-gates")

    if not _sg_should_fire(gate_name, gate_input, gate_dir=effective_gate_dir):
        cached = _load_gated_result(effective_gate_dir, gate_name)
        if cached is not None and isinstance(cached, dict):
            return cached

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
    except Exception:
        return {
            "impact": "unknown",
            "value_gained": "eval failed",
            "risk_introduced": "unknown",
            "risk_score": 0.5,
            "recommended_action": "watch",
        }

    _sg_record_fire(gate_name, gate_input, gate_dir=effective_gate_dir)
    _save_gated_result(effective_gate_dir, gate_name, result)
    return result


# --- Lag window and wg task emission ---

from typing import Callable as _Callable

_WgRunner = _Callable[[list[str]], tuple[int, str, str]]


def lag_window_check(commit_count: int, threshold: int) -> bool:
    """Return True if commit_count meets or exceeds threshold (task should be emitted)."""
    return commit_count >= threshold


def emit_wg_task(
    repo_name: str,
    commit_count: int,
    eval_result: dict[str, Any],
    project_dir: Path,
    *,
    wg_runner: _WgRunner | None = None,
) -> str | None:
    """Emit a wg task for an upstream sync opportunity.

    Always passes --dir explicitly to avoid CWD resolution failures.
    Returns the created task ID, or None on failure.
    """
    action = eval_result.get("recommended_action", "watch")
    risk_score = eval_result.get("risk_score", 0.5)
    category = eval_result.get("category", "unknown")

    noun = "commit" if commit_count == 1 else "commits"
    title = f"sync upstream: {repo_name} ({commit_count} {noun})"

    desc_parts = [f"Upstream {repo_name} has {commit_count} new {noun}."]
    desc_parts.append(f"Category: {category} | Risk: {risk_score:.2f} | Action: {action}")
    if eval_result.get("value_gained"):
        desc_parts.append(f"Value: {eval_result['value_gained']}")
    if eval_result.get("risk_introduced"):
        desc_parts.append(f"Risk detail: {eval_result['risk_introduced']}")
    description = " ".join(desc_parts)

    wg_dir = str(project_dir / ".workgraph")
    cmd = ["wg", "--dir", wg_dir, "add", title, "--description", description]

    def _default_runner(c: list[str]) -> tuple[int, str, str]:
        try:
            result = subprocess.run(c, capture_output=True, text=True, timeout=15.0)
            return result.returncode, result.stdout, result.stderr
        except Exception as exc:
            return 1, "", str(exc)

    runner = wg_runner or _default_runner
    rc, stdout, _stderr = runner(cmd)

    if rc != 0:
        return None

    # Parse "Added task: TITLE (ID)" from stdout
    for line in stdout.splitlines():
        if line.startswith("Added task:") and "(" in line:
            task_id = line.rsplit("(", 1)[-1].rstrip(")")
            if task_id:
                return task_id

    return None


# --- Pass 1: External repos ---

_RELEVANCE_THRESHOLD = 0.3
_RISK_ALERT_THRESHOLD = 0.4


def _git_commit_count(repo_path: Path, old_sha: str, new_sha: str) -> int:
    """Return number of commits between two SHAs (exclusive old_sha, inclusive new_sha)."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", f"{old_sha}..{new_sha}"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=30.0,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
    except Exception:
        pass
    return 0


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


def _resolve_upstream_ref(repo_cfg: dict[str, Any], branch: str, repo_path: Path) -> str:
    """Return the ref that represents the true external upstream line."""
    explicit = str(repo_cfg.get("upstream_ref") or "").strip()
    if explicit:
        return explicit
    candidate = f"origin/{branch}"
    if _git_current_sha(repo_path, candidate):
        return candidate
    return branch


def _resolve_adopted_ref(repo_cfg: dict[str, Any], branch: str) -> str:
    """Return the ref that represents the currently adopted local/fork line."""
    explicit = str(repo_cfg.get("adopted_ref") or "").strip()
    if explicit:
        return explicit
    return branch


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
    project_dir: Path | None = None,
    wg_runner: _WgRunner | None = None,
) -> list[dict[str, Any]]:
    """Evaluate all tracked external repos. Returns list of evaluation results.

    When *project_dir* is provided, emits wg tasks for repos that warrant action
    (recommended_action != 'ignore') or exceed their configured lag_window_commits threshold.
    """
    from driftdriver.upstream_pins import (
        get_adopted_sha,
        get_sha,
        is_snoozed,
        load_pins,
        save_pins,
        set_adopted_sha,
        set_sha,
    )

    pins = load_pins(pins_path)
    results: list[dict[str, Any]] = []
    pins_updated = False

    for repo_cfg in config.get("external_repos") or []:
        repo_name = str(repo_cfg.get("name") or "")
        local_path_str = str(repo_cfg.get("local_path") or "")
        branches = list(repo_cfg.get("branches") or [])
        lag_threshold = int(repo_cfg.get("lag_window_commits") or 0)
        if not repo_name or not local_path_str or not branches:
            continue
        local_path = Path(local_path_str)
        if not local_path.exists():
            continue

        for branch in branches:
            if is_snoozed(pins, repo_name, branch):
                continue

            upstream_ref = _resolve_upstream_ref(repo_cfg, branch, local_path)
            adopted_ref = _resolve_adopted_ref(repo_cfg, branch)

            upstream_sha = _git_current_sha(local_path, upstream_ref)
            if not upstream_sha:
                continue
            adopted_sha = _git_current_sha(local_path, adopted_ref) or upstream_sha

            pinned_sha = get_sha(pins, repo_name, branch)
            pinned_adopted_sha = get_adopted_sha(pins, repo_name, branch)
            upstream_changed = pinned_sha != upstream_sha
            adopted_changed = pinned_adopted_sha != adopted_sha
            adopted_diverged = adopted_sha != upstream_sha
            should_report = upstream_changed or adopted_diverged

            if not should_report and not adopted_changed:
                continue  # No upstream or adoption-state change

            if not should_report:
                pins = set_adopted_sha(pins, repo_name, branch, adopted_sha)
                pins_updated = True
                continue

            if upstream_changed and pinned_sha:
                changed_files = _git_changed_files(local_path, pinned_sha, upstream_sha)
                subjects = _git_commit_subjects(local_path, pinned_sha, upstream_sha)
                commit_count = _git_commit_count(local_path, pinned_sha, upstream_sha)
            else:
                changed_files = []
                subjects = []
                commit_count = 0

            if upstream_changed:
                category = classify_changes(changed_files, subjects)
                relevance = triage_relevance(
                    changed_files, subjects, category, llm_caller=llm_caller
                )
            else:
                category = "tracking-state"
                relevance = 0.0

            eval_result: dict[str, Any] = {
                "repo": repo_name,
                "branch": branch,
                "old_sha": pinned_sha,
                "new_sha": upstream_sha,
                "category": category,
                "relevance_score": relevance,
                "commit_count": commit_count,
                "upstream_ref": upstream_ref,
                "adopted_ref": adopted_ref,
                "adopted_old_sha": pinned_adopted_sha,
                "adopted_sha": adopted_sha,
                "adopted_diverged": adopted_diverged,
                "tracking_status": "tracking-adopted-line" if adopted_diverged else "tracking-upstream",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            if upstream_changed and relevance > _RELEVANCE_THRESHOLD:
                deep = deep_eval_change(
                    changed_files,
                    subjects,
                    category,
                    context=_DRIFTDRIVER_CONTEXT,
                    llm_caller=deep_eval_caller,
                )
                eval_result.update(deep)
                eval_result["llm_eval"] = deep
                risk_score = float(deep.get("risk_score", 0.5))
            else:
                risk_score = 0.1
                eval_result["recommended_action"] = "ignore"
                eval_result["llm_eval"] = None

            eval_result["action"] = (
                "tracked"
                if not upstream_changed
                else ("alert" if risk_score >= _RISK_ALERT_THRESHOLD else "auto_adopt")
            )

            # Emit a wg task when action warrants it or lag window exceeded
            if project_dir is not None and upstream_changed:
                action = eval_result.get("recommended_action", "watch")
                lag_exceeded = lag_threshold > 0 and lag_window_check(commit_count, lag_threshold)
                if action != "ignore" or lag_exceeded:
                    task_id = emit_wg_task(
                        repo_name,
                        commit_count,
                        eval_result,
                        project_dir,
                        wg_runner=wg_runner,
                    )
                    eval_result["wg_task_id"] = task_id

            if upstream_changed:
                pins = set_sha(pins, repo_name, branch, upstream_sha)
            if adopted_changed:
                pins = set_adopted_sha(pins, repo_name, branch, adopted_sha)
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
