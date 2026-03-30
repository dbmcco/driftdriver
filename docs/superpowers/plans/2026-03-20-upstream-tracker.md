# Upstream Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automated tracking of external upstream repos (graphwork/workgraph, agentbureau/agency) and internal repos with unpushed work, with LLM-mediated risk routing and hub visibility.

**Architecture:** A single `upstream_tracker.py` module orchestrates two passes: (1) fetch + diff + LLM-eval external repos, route by risk score; (2) scan enrolled internal repos for unpushed commits / dirty trees, emit governancedrift findings. Results land in the hub snapshot as an `upstream_tracker` dict. No daemon, no new lane — triggered as a wg cycle task.

**Tech Stack:** Python 3.11+, tomllib/tomli_w, subprocess (git), urllib (Anthropic API), pytest with real git fixtures

---

## File Structure

- **Create:** `driftdriver/upstream_pins.py` — read/write `.driftdriver/upstream-pins.toml`; tracks pinned SHAs + snooze list
- **Create:** `driftdriver/upstream_tracker.py` — orchestrates both passes; injectable LLM caller for testability
- **Modify:** `driftdriver/governancedrift.py:14-19` — add `UNPUSHED_WORK` to `FindingCategory` enum and `classify_unpushed` function
- **Modify:** `driftdriver/ecosystem_hub/snapshot.py` — call `run_upstream_tracker`, add `upstream_tracker` key to snapshot dict
- **Create:** `tests/test_upstream_pins.py` — unit tests for TOML read/write/snooze logic
- **Create:** `tests/test_upstream_tracker.py` — unit tests using real git repos in tmp_path; LLM caller injected as a simple callable

---

## Task 1: Upstream Pins Store

**Files:**
- Create: `driftdriver/upstream_pins.py`
- Create: `tests/test_upstream_pins.py`

**Context:** `.driftdriver/upstream-pins.toml` tracks last-known SHAs per external repo+branch and a snooze list. This module is pure I/O — no git, no LLM. The TOML format:

```toml
[shas]
"graphwork/workgraph:main" = "abc123"
"graphwork/workgraph:fix-toctou-race" = "def456"
"agentbureau/agency:main" = "ghi789"

[snoozed]
"graphwork/workgraph:fix-before-edges" = { until = "2026-04-01", reason = "TUI-only, no impact" }
```

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_upstream_pins.py
# ABOUTME: Tests for upstream_pins — TOML read/write/snooze logic.
# ABOUTME: No mocks; uses real tmp_path files.
from __future__ import annotations

from datetime import date, timezone
from pathlib import Path
import pytest

from driftdriver.upstream_pins import (
    get_sha,
    is_snoozed,
    load_pins,
    save_pins,
    set_sha,
    snooze_branch,
)


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    pins = load_pins(tmp_path / "upstream-pins.toml")
    assert pins == {"shas": {}, "snoozed": {}}


def test_set_and_get_sha(tmp_path: Path) -> None:
    path = tmp_path / "upstream-pins.toml"
    pins = load_pins(path)
    pins = set_sha(pins, "graphwork/workgraph", "main", "abc123")
    save_pins(path, pins)
    pins2 = load_pins(path)
    assert get_sha(pins2, "graphwork/workgraph", "main") == "abc123"


def test_get_sha_missing_returns_none(tmp_path: Path) -> None:
    pins = load_pins(tmp_path / "nope.toml")
    assert get_sha(pins, "graphwork/workgraph", "nonexistent") is None


def test_snooze_and_is_snoozed(tmp_path: Path) -> None:
    path = tmp_path / "pins.toml"
    pins = load_pins(path)
    pins = snooze_branch(pins, "graphwork/workgraph", "fix-before-edges", "2099-01-01", "TUI-only")
    save_pins(path, pins)
    pins2 = load_pins(path)
    assert is_snoozed(pins2, "graphwork/workgraph", "fix-before-edges") is True


def test_expired_snooze_not_snoozed(tmp_path: Path) -> None:
    path = tmp_path / "pins.toml"
    pins = load_pins(path)
    pins = snooze_branch(pins, "graphwork/workgraph", "old-branch", "2020-01-01", "old")
    save_pins(path, pins)
    pins2 = load_pins(path)
    assert is_snoozed(pins2, "graphwork/workgraph", "old-branch") is False


def test_set_sha_overwrites_previous(tmp_path: Path) -> None:
    path = tmp_path / "pins.toml"
    pins = load_pins(path)
    pins = set_sha(pins, "graphwork/workgraph", "main", "aaa")
    pins = set_sha(pins, "graphwork/workgraph", "main", "bbb")
    save_pins(path, pins)
    assert get_sha(load_pins(path), "graphwork/workgraph", "main") == "bbb"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/test_upstream_pins.py -v 2>&1 | head -20
```

Expected: ImportError — `upstream_pins` does not exist yet.

- [ ] **Step 3: Implement `upstream_pins.py`**

```python
# driftdriver/upstream_pins.py
# ABOUTME: Read/write .driftdriver/upstream-pins.toml for upstream branch tracking.
# ABOUTME: Tracks pinned SHAs per repo+branch and a snooze list with expiry dates.
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore[no-redef]

try:
    import tomli_w
    _HAS_TOMLI_W = True
except ImportError:
    _HAS_TOMLI_W = False


def load_pins(pins_path: Path) -> dict[str, Any]:
    """Load upstream-pins.toml. Returns {shas: {}, snoozed: {}} if absent."""
    if not pins_path.exists():
        return {"shas": {}, "snoozed": {}}
    try:
        data = tomllib.loads(pins_path.read_text(encoding="utf-8"))
    except Exception:
        return {"shas": {}, "snoozed": {}}
    return {
        "shas": dict(data.get("shas") or {}),
        "snoozed": dict(data.get("snoozed") or {}),
    }


def save_pins(pins_path: Path, pins: dict[str, Any]) -> None:
    """Write pins back to disk atomically."""
    pins_path.parent.mkdir(parents=True, exist_ok=True)
    if _HAS_TOMLI_W:
        content = tomli_w.dumps(pins)
    else:
        # Fallback: hand-write minimal TOML
        lines = ["[shas]\n"]
        for key, val in (pins.get("shas") or {}).items():
            lines.append(f'"{key}" = "{val}"\n')
        lines.append("\n[snoozed]\n")
        for key, val in (pins.get("snoozed") or {}).items():
            until = val.get("until", "")
            reason = val.get("reason", "")
            lines.append(f'"{key}" = {{ until = "{until}", reason = "{reason}" }}\n')
        content = "".join(lines)
    tmp = pins_path.with_suffix(".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.replace(pins_path)


def _branch_key(repo: str, branch: str) -> str:
    return f"{repo}:{branch}"


def get_sha(pins: dict[str, Any], repo: str, branch: str) -> str | None:
    """Return pinned SHA or None if not tracked yet."""
    return (pins.get("shas") or {}).get(_branch_key(repo, branch))


def set_sha(pins: dict[str, Any], repo: str, branch: str, sha: str) -> dict[str, Any]:
    """Return updated pins dict with new SHA (immutable-style)."""
    updated = {
        "shas": dict(pins.get("shas") or {}),
        "snoozed": dict(pins.get("snoozed") or {}),
    }
    updated["shas"][_branch_key(repo, branch)] = sha
    return updated


def is_snoozed(pins: dict[str, Any], repo: str, branch: str) -> bool:
    """Return True if this branch is snoozed and the snooze hasn't expired."""
    entry = (pins.get("snoozed") or {}).get(_branch_key(repo, branch))
    if not isinstance(entry, dict):
        return False
    until_str = entry.get("until", "")
    try:
        until = date.fromisoformat(str(until_str))
        return until >= date.today()
    except (ValueError, TypeError):
        return False


def snooze_branch(
    pins: dict[str, Any],
    repo: str,
    branch: str,
    until: str,
    reason: str,
) -> dict[str, Any]:
    """Return updated pins dict with snooze entry added."""
    updated = {
        "shas": dict(pins.get("shas") or {}),
        "snoozed": dict(pins.get("snoozed") or {}),
    }
    updated["snoozed"][_branch_key(repo, branch)] = {"until": until, "reason": reason}
    return updated
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python3 -m pytest tests/test_upstream_pins.py -v
```

Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/upstream_pins.py tests/test_upstream_pins.py
git commit -m "feat: add upstream_pins store for tracking external branch SHAs"
```

---

## Task 2: Change Classifier

**Files:**
- Modify: `driftdriver/upstream_tracker.py` (create, add classifier)
- Modify: `tests/test_upstream_tracker.py` (create, add classifier tests)

**Context:** Classify a set of changed files + commit subjects into one of four categories. Deterministic — no LLM. Used to decide whether Haiku triage is worth running (internals-only → skip). Categories match the spec: `schema`, `api-surface`, `behavior`, `internals-only`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_upstream_tracker.py
# ABOUTME: Tests for upstream_tracker — git diff, LLM eval, risk routing.
# ABOUTME: LLM caller is injected; git operations use real tmp_path repos.
from __future__ import annotations

import pytest
from driftdriver.upstream_tracker import classify_changes


def test_schema_change_detected() -> None:
    files = ["graph.jsonl", "schema/task.json", "src/main.rs"]
    assert classify_changes(files, []) == "schema"


def test_api_surface_change_detected() -> None:
    files = ["src/cli/commands.rs", "src/main.rs"]
    subjects = ["feat: add wg retract command"]
    assert classify_changes(files, subjects) == "api-surface"


def test_behavior_change() -> None:
    files = ["src/coordinator.rs", "src/scheduler.rs"]
    subjects = ["fix: liveness detection for stuck agents"]
    assert classify_changes(files, subjects) == "behavior"


def test_internals_only() -> None:
    files = ["src/tui/views.rs", "README.md", "CHANGELOG.md"]
    subjects = ["chore: TUI polish"]
    assert classify_changes(files, subjects) == "internals-only"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_upstream_tracker.py::test_schema_change_detected -v 2>&1 | head -15
```

Expected: ImportError — `upstream_tracker` does not exist.

- [ ] **Step 3: Implement `upstream_tracker.py` with classifier**

```python
# driftdriver/upstream_tracker.py
# ABOUTME: Tracks upstream external repos (graphwork/workgraph, agentbureau/agency).
# ABOUTME: Pass 1: fetch, classify changes, LLM eval, risk route. Pass 2: internal unpushed work.
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

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
    "tui", "views", "README", "CHANGELOG", "CONTRIBUTING",
    "LICENSE", ".md", "docs/", "tests/", "fixtures/",
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
```

- [ ] **Step 4: Run classifier tests**

```bash
python3 -m pytest tests/test_upstream_tracker.py -k "classify" -v
```

Expected: All 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/upstream_tracker.py tests/test_upstream_tracker.py
git commit -m "feat: add upstream change classifier (schema/api-surface/behavior/internals-only)"
```

---

## Task 3: LLM Evaluation (Haiku Triage + Sonnet Deep Eval)

**Files:**
- Modify: `driftdriver/upstream_tracker.py` — add `triage_relevance` and `deep_eval_change`
- Modify: `tests/test_upstream_tracker.py` — add LLM tests with injected fake caller

**Context:** LLM calls follow the `intelligence/evaluator.py` pattern: POST to Anthropic API with tool_use for structured output. Tests inject a fake `llm_caller: Callable` to avoid real API calls. The default caller uses `ANTHROPIC_API_KEY`. Haiku for triage (cheap, fast), Sonnet for deep eval (only when relevance > 0.3).

- [ ] **Step 1: Write the failing tests**

```python
# Add to tests/test_upstream_tracker.py

from driftdriver.upstream_tracker import triage_relevance, deep_eval_change


def _fake_haiku_caller(model: str, prompt: str) -> dict[str, Any]:
    """Returns a fixed relevance score for testing."""
    return {"relevance_score": 0.7, "rationale": "test"}


def _fake_sonnet_caller(model: str, prompt: str) -> dict[str, Any]:
    """Returns a fixed deep eval for testing."""
    return {
        "impact": "moderate",
        "value_gained": "cleaner API",
        "risk_introduced": "low",
        "risk_score": 0.2,
        "recommended_action": "adopt",
    }


def test_triage_relevance_returns_score(tmp_path: Path) -> None:
    score = triage_relevance(
        changed_files=["src/coordinator.rs"],
        commit_subjects=["fix: liveness detection"],
        category="behavior",
        llm_caller=_fake_haiku_caller,
    )
    assert 0.0 <= score <= 1.0
    assert score == pytest.approx(0.7)


def test_triage_internals_only_skips_llm(tmp_path: Path) -> None:
    """internals-only changes get relevance 0.0 without calling the LLM."""
    called = []
    def _spy_caller(model: str, prompt: str) -> dict:
        called.append(model)
        return {"relevance_score": 0.9, "rationale": "test"}

    score = triage_relevance(
        changed_files=["src/tui/views.rs"],
        commit_subjects=["chore: TUI polish"],
        category="internals-only",
        llm_caller=_spy_caller,
    )
    assert score == 0.0
    assert called == []  # LLM never called for internals-only


def test_deep_eval_returns_risk_score(tmp_path: Path) -> None:
    result = deep_eval_change(
        changed_files=["src/coordinator.rs"],
        commit_subjects=["fix: liveness detection"],
        category="behavior",
        context="driftdriver uses wg coordinator for factory task dispatch",
        llm_caller=_fake_sonnet_caller,
    )
    assert "risk_score" in result
    assert result["recommended_action"] in ("adopt", "watch", "ignore")
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest tests/test_upstream_tracker.py -k "triage or deep_eval" -v 2>&1 | head -20
```

Expected: ImportError — `triage_relevance` not defined yet.

- [ ] **Step 3: Implement LLM evaluation functions**

Add to `driftdriver/upstream_tracker.py`:

```python
import os
from urllib.error import HTTPError
from urllib.request import Request, urlopen

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
    # Extract text from response and parse JSON
    content = body.get("content", [])
    for block in content:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("text", "").strip()
            # Extract JSON from response
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
        # Normalize risk_score to float
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
```

- [ ] **Step 4: Run LLM tests**

```bash
python3 -m pytest tests/test_upstream_tracker.py -k "triage or deep_eval" -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/upstream_tracker.py tests/test_upstream_tracker.py
git commit -m "feat: add LLM-mediated upstream triage (Haiku) and deep eval (Sonnet)"
```

---

## Task 4: Risk Router + Full Pass 1

**Files:**
- Modify: `driftdriver/upstream_tracker.py` — add `run_pass1`, `_git_current_sha`, `_route_change`
- Modify: `tests/test_upstream_tracker.py` — add pass1 tests with real git repo

**Context:** Pass 1 orchestrates the full external repo evaluation cycle. It needs a local clone of the external repo to fetch from (path configured in the tracker config dict). Tests use a real `git init` + `git commit` in `tmp_path` to simulate a changed upstream. The risk threshold is 0.4 — below auto-adopts, above alerts.

**Tracker config format** (passed as a dict, typically loaded from `.driftdriver/upstream-pins.toml` extra section or a separate config file):
```python
{
    "external_repos": [
        {
            "name": "graphwork/workgraph",
            "local_path": "/path/to/local/workgraph",  # required for git ops
            "branches": ["main", "fix-toctou-race", "infra-fix-toctou", "fix-auto-task-edges"],
        },
        {
            "name": "agentbureau/agency",
            "local_path": "/path/to/local/agency",
            "branches": ["main"],
        },
    ]
}
```

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/test_upstream_tracker.py
import subprocess
from driftdriver.upstream_tracker import run_pass1, _git_current_sha


def _make_git_repo(path: Path) -> str:
    """Init a real git repo with one commit; return current SHA."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    rc, sha, _ = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True
    ).returncode, "", ""
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True)
    return result.stdout.strip()


def test_git_current_sha(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    expected_sha = _make_git_repo(repo)
    sha = _git_current_sha(repo, "HEAD")
    assert sha == expected_sha


def test_run_pass1_no_change_returns_empty(tmp_path: Path) -> None:
    repo = tmp_path / "wg"
    sha = _make_git_repo(repo)
    pins_path = tmp_path / ".driftdriver" / "upstream-pins.toml"

    from driftdriver.upstream_pins import load_pins, save_pins, set_sha
    pins = load_pins(pins_path)
    pins = set_sha(pins, "graphwork/workgraph", "main", sha)  # pin to current
    save_pins(pins_path, pins)

    config = {
        "external_repos": [{
            "name": "graphwork/workgraph",
            "local_path": str(repo),
            "branches": ["main"],
        }]
    }
    results = run_pass1(config, pins_path, llm_caller=_fake_haiku_caller)
    assert results == []  # no change → no results


def test_run_pass1_new_sha_triggers_eval(tmp_path: Path) -> None:
    repo = tmp_path / "wg"
    _make_git_repo(repo)
    pins_path = tmp_path / ".driftdriver" / "upstream-pins.toml"
    # No pin set → treat as new, triggers eval
    config = {
        "external_repos": [{
            "name": "graphwork/workgraph",
            "local_path": str(repo),
            "branches": ["main"],
        }]
    }
    results = run_pass1(config, pins_path, llm_caller=_fake_haiku_caller, deep_eval_caller=_fake_sonnet_caller)
    assert len(results) == 1
    result = results[0]
    assert result["repo"] == "graphwork/workgraph"
    assert result["branch"] == "main"
    assert "action" in result  # "auto_adopt" or "alert"
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest tests/test_upstream_tracker.py -k "pass1 or git_current" -v 2>&1 | head -20
```

Expected: ImportError or AttributeError — functions not defined yet.

- [ ] **Step 3: Implement `run_pass1` and helpers**

Add to `driftdriver/upstream_tracker.py`:

```python
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


def run_pass1(
    config: dict[str, Any],
    pins_path: Path,
    *,
    llm_caller: Callable[[str, str], dict[str, Any]] | None = None,
    deep_eval_caller: Callable[[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Evaluate all tracked external repos. Returns list of evaluation results."""
    from driftdriver.upstream_pins import get_sha, is_snoozed, load_pins, set_sha, save_pins

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

            # Determine changed files and subjects
            if pinned_sha:
                changed_files = _git_changed_files(local_path, pinned_sha, current_sha)
                subjects = _git_commit_subjects(local_path, pinned_sha, current_sha)
            else:
                # First time seeing this branch — treat everything as new
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
                risk_score = 0.1  # low relevance → low risk
                eval_result["recommended_action"] = "ignore"

            eval_result["action"] = (
                "alert" if risk_score >= _RISK_ALERT_THRESHOLD else "auto_adopt"
            )

            # Update pin to current SHA
            pins = set_sha(pins, repo_name, branch, current_sha)
            pins_updated = True
            results.append(eval_result)

    if pins_updated:
        save_pins(pins_path, pins)

    return results
```

- [ ] **Step 4: Run pass1 tests**

```bash
python3 -m pytest tests/test_upstream_tracker.py -k "pass1 or git_current" -v
```

Expected: All 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add driftdriver/upstream_tracker.py tests/test_upstream_tracker.py
git commit -m "feat: implement upstream tracker Pass 1 (fetch, classify, LLM eval, risk route)"
```

---

## Task 5: Internal Repos Pass (Pass 2) + Governancedrift Finding

**Files:**
- Modify: `driftdriver/governancedrift.py:14-19` — add `UNPUSHED_WORK` to `FindingCategory`
- Modify: `driftdriver/governancedrift.py` — add `classify_unpushed_work` function
- Modify: `driftdriver/upstream_tracker.py` — add `run_pass2`
- Modify: `tests/test_upstream_tracker.py` — add pass2 tests

**Context:** Pass 2 uses data already in the hub snapshot's `repos` list — each repo has `ahead` (int) and `working_tree_dirty` (bool). No additional git calls needed. Findings are added to the `conformance_findings` list with a new `UNPUSHED_WORK` category. Threshold: `ahead >= 3` OR `working_tree_dirty` with no open tasks (zombie dirty tree).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_upstream_tracker.py — add:
from driftdriver.upstream_tracker import run_pass2


def test_pass2_clean_repos_no_findings() -> None:
    repos = [
        {"name": "paia-shell", "ahead": 0, "working_tree_dirty": False, "exists": True},
        {"name": "derek", "ahead": 1, "working_tree_dirty": False, "exists": True},
    ]
    findings = run_pass2(repos)
    assert findings == []


def test_pass2_ahead_repo_emits_finding() -> None:
    repos = [
        {"name": "paia-shell", "ahead": 5, "working_tree_dirty": False, "exists": True},
    ]
    findings = run_pass2(repos)
    assert len(findings) == 1
    assert findings[0]["repo"] == "paia-shell"
    assert findings[0]["category"] == "unpushed-work"


def test_pass2_dirty_tree_emits_finding() -> None:
    repos = [
        {"name": "lfw", "ahead": 0, "working_tree_dirty": True, "exists": True},
    ]
    findings = run_pass2(repos)
    assert len(findings) == 1
    assert findings[0]["category"] == "unpushed-work"
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest tests/test_upstream_tracker.py -k "pass2" -v 2>&1 | head -20
```

Expected: ImportError — `run_pass2` not defined.

- [ ] **Step 3: Add `UNPUSHED_WORK` to governancedrift**

In `driftdriver/governancedrift.py`, find the `FindingCategory` enum (lines 14-19) and add:

```python
class FindingCategory(str, Enum):
    LIFECYCLE_VIOLATION = "lifecycle-violation"
    PROCESS_DEBT = "process-debt"
    ARCHITECTURE_GAP = "architecture-gap"
    POSTURE_MISMATCH = "posture-mismatch"
    UNPUSHED_WORK = "unpushed-work"  # add this line
```

- [ ] **Step 4: Implement `run_pass2`**

Add to `driftdriver/upstream_tracker.py`:

```python
_AHEAD_THRESHOLD = 3  # commits ahead before flagging


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
```

- [ ] **Step 5: Run pass2 tests**

```bash
python3 -m pytest tests/test_upstream_tracker.py -k "pass2" -v
```

Expected: All 3 PASS.

- [ ] **Step 6: Run existing governancedrift tests to check for regressions**

```bash
python3 -m pytest tests/ -k "governance or conformance" -v --tb=short 2>&1 | tail -15
```

Expected: All pass.

- [ ] **Step 7: Commit**

```bash
git add driftdriver/governancedrift.py driftdriver/upstream_tracker.py tests/test_upstream_tracker.py
git commit -m "feat: add Pass 2 (unpushed-work findings) and UNPUSHED_WORK governance category"
```

---

## Task 6: Hub Snapshot Wiring

**Files:**
- Modify: `driftdriver/ecosystem_hub/snapshot.py` — import `run_pass2`, add `upstream_tracker` key to snapshot dict
- Modify: `tests/test_upstream_tracker.py` — add snapshot wiring test

**Context:** The snapshot dict already has `upstream_candidates` and `conformance_findings`. We add an `upstream_tracker` dict and merge pass2 findings into `conformance_findings`. Pass 1 is not called from snapshot collection (it's a wg cycle task triggered separately) — only its last result is read from a state file. Pass 2 IS called inline since it only reads from already-collected repo data.

The last Pass 1 result is stored at `.driftdriver/upstream-tracker-last.json` (written by `run_pass1`).

- [ ] **Step 1: Write failing test**

```python
# tests/test_upstream_tracker.py — add:
from driftdriver.upstream_tracker import build_snapshot_entry


def test_build_snapshot_entry_no_state(tmp_path: Path) -> None:
    repos = [{"name": "paia", "ahead": 0, "working_tree_dirty": False, "exists": True}]
    entry = build_snapshot_entry(repos, state_dir=tmp_path)
    assert "pass1_last_run" in entry
    assert "pass2_findings" in entry
    assert entry["pass2_findings"] == []


def test_build_snapshot_entry_with_pass2_finding(tmp_path: Path) -> None:
    repos = [{"name": "paia", "ahead": 5, "working_tree_dirty": False, "exists": True}]
    entry = build_snapshot_entry(repos, state_dir=tmp_path)
    assert len(entry["pass2_findings"]) == 1
    assert entry["pass2_findings"][0]["category"] == "unpushed-work"
```

- [ ] **Step 2: Run to verify they fail**

```bash
python3 -m pytest tests/test_upstream_tracker.py -k "snapshot_entry" -v 2>&1 | head -15
```

Expected: ImportError — `build_snapshot_entry` not defined.

- [ ] **Step 3: Implement `build_snapshot_entry` and update `run_pass1` to persist state**

Add to `driftdriver/upstream_tracker.py`:

```python
_STATE_FILENAME = "upstream-tracker-last.json"


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

    # Read last pass1 result (if any)
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
```

Also update `run_pass1` to write its results to state:

```python
# At the end of run_pass1, before returning results, add:
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
```

- [ ] **Step 4: Wire into snapshot.py**

In `driftdriver/ecosystem_hub/snapshot.py`, add after the `agency_eval_inputs` block:

```python
from driftdriver.upstream_tracker import build_snapshot_entry as _build_upstream_entry

# Upstream tracker entry — pass2 inline, pass1 from last saved state
_state_dir = project_dir / ".driftdriver"
_upstream_tracker = _build_upstream_entry(
    [asdict(r) for r in repos],
    state_dir=_state_dir,
)
```

And in the snapshot dict, add:
```python
"upstream_tracker": _upstream_tracker,
```

Also merge pass2 findings into conformance_findings (they use the same format):
```python
"conformance_findings": governance.get("conformance_findings", []) + _upstream_tracker["pass2_findings"],
```

- [ ] **Step 5: Run all tests**

```bash
python3 -m pytest tests/ -k "upstream or tracker" -q --tb=short 2>&1 | tail -10
```

Expected: All pass.

- [ ] **Step 6: Run full test suite for regressions**

```bash
python3 -m pytest tests/ -q --tb=short 2>&1 | tail -10
```

Expected: No new failures.

- [ ] **Step 7: Commit**

```bash
git add driftdriver/upstream_tracker.py driftdriver/ecosystem_hub/snapshot.py tests/test_upstream_tracker.py
git commit -m "feat: wire upstream_tracker into hub snapshot (pass2 inline, pass1 from state)"
```

---
