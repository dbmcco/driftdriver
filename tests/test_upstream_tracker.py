# ABOUTME: Tests for upstream_tracker — git diff, LLM eval, risk routing.
# ABOUTME: LLM caller is injected; git operations use real tmp_path repos.
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

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


# --- LLM evaluation tests ---

from driftdriver.upstream_tracker import deep_eval_change, triage_relevance


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


def test_triage_relevance_returns_score() -> None:
    score = triage_relevance(
        changed_files=["src/coordinator.rs"],
        commit_subjects=["fix: liveness detection"],
        category="behavior",
        llm_caller=_fake_haiku_caller,
    )
    assert 0.0 <= score <= 1.0
    assert score == pytest.approx(0.7)


def test_triage_internals_only_skips_llm() -> None:
    """internals-only changes get relevance 0.0 without calling the LLM."""
    called = []

    def _spy_caller(model: str, prompt: str) -> dict[str, Any]:
        called.append(model)
        return {"relevance_score": 0.9, "rationale": "test"}

    score = triage_relevance(
        changed_files=["src/tui/views.rs"],
        commit_subjects=["chore: TUI polish"],
        category="internals-only",
        llm_caller=_spy_caller,
    )
    assert score == 0.0
    assert called == []


def test_deep_eval_returns_risk_score() -> None:
    result = deep_eval_change(
        changed_files=["src/coordinator.rs"],
        commit_subjects=["fix: liveness detection"],
        category="behavior",
        context="driftdriver uses wg coordinator for factory task dispatch",
        llm_caller=_fake_sonnet_caller,
    )
    assert "risk_score" in result
    assert result["recommended_action"] in ("adopt", "watch", "ignore")


# --- Pass 1 tests ---

from driftdriver.upstream_tracker import _git_current_sha, run_pass1


def _make_git_repo(path: Path) -> str:
    """Init a real git repo with one commit; return current SHA."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, capture_output=True)
    (path / "README.md").write_text("hello")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
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
    pins = set_sha(pins, "graphwork/workgraph", "main", sha)
    save_pins(pins_path, pins)

    config = {
        "external_repos": [{
            "name": "graphwork/workgraph",
            "local_path": str(repo),
            "branches": ["main"],
        }]
    }
    results = run_pass1(config, pins_path, llm_caller=_fake_haiku_caller)
    assert results == []


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
    assert "action" in result


# --- Pass 2 tests ---

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


# --- Snapshot entry tests ---

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
