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
    # llm_eval key must always be present (None when relevance too low for deep eval)
    assert "llm_eval" in result


def test_run_pass1_high_relevance_populates_llm_eval(tmp_path: Path) -> None:
    """When relevance is high, deep eval runs and llm_eval dict is set in the result."""
    from driftdriver.upstream_pins import load_pins, save_pins, set_sha

    repo = tmp_path / "wg"
    old_sha = _make_git_repo(repo)

    # Add a second commit with an API-surface file so classify_changes returns 'api-surface'
    (repo / "src").mkdir()
    (repo / "src" / "commands.rs").write_text("pub fn new_cmd() {}")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "feat: add new-cmd"], cwd=repo, check=True, capture_output=True)

    pins_path = tmp_path / ".driftdriver" / "upstream-pins.toml"
    pins = load_pins(pins_path)
    pins = set_sha(pins, "graphwork/workgraph", "main", old_sha)
    save_pins(pins_path, pins)

    config = {
        "external_repos": [{
            "name": "graphwork/workgraph",
            "local_path": str(repo),
            "branches": ["main"],
        }]
    }
    results = run_pass1(
        config, pins_path,
        llm_caller=_fake_haiku_caller,
        deep_eval_caller=_fake_sonnet_caller,
    )
    assert len(results) == 1
    result = results[0]
    assert result["llm_eval"] is not None
    assert result["llm_eval"]["impact"] == "moderate"
    assert result["llm_eval"]["risk_score"] == pytest.approx(0.2)


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


# --- Server integration: _run_upstream_pass1 ---

def test_run_upstream_pass1_skips_missing_config(tmp_path: Path) -> None:
    """_run_upstream_pass1 does nothing if upstream-config.toml is absent."""
    from driftdriver.ecosystem_hub.server import _run_upstream_pass1
    # Should complete silently with no state file written
    _run_upstream_pass1(tmp_path)
    assert not (tmp_path / ".driftdriver" / "upstream-tracker-last.json").exists()


def test_run_upstream_pass1_skips_empty_config(tmp_path: Path) -> None:
    """_run_upstream_pass1 does nothing if external_repos list is empty."""
    from driftdriver.ecosystem_hub.server import _run_upstream_pass1
    dd_dir = tmp_path / ".driftdriver"
    dd_dir.mkdir()
    (dd_dir / "upstream-config.toml").write_text("[global]\n", encoding="utf-8")
    _run_upstream_pass1(tmp_path)
    assert not (dd_dir / "upstream-tracker-last.json").exists()


# --- lag_window_check tests ---

from driftdriver.upstream_tracker import lag_window_check


def test_lag_window_check_at_threshold() -> None:
    assert lag_window_check(20, 20) is True


def test_lag_window_check_above_threshold() -> None:
    assert lag_window_check(51, 20) is True


def test_lag_window_check_below_threshold() -> None:
    assert lag_window_check(4, 5) is False


def test_lag_window_check_zero_threshold_always_true() -> None:
    assert lag_window_check(0, 0) is True


# --- emit_wg_task tests ---

from driftdriver.upstream_tracker import emit_wg_task


def _make_fake_wg_runner(returncode: int = 0, stdout: str = "") -> object:
    """Return a fake wg_runner callable that captures calls."""
    calls: list[list[str]] = []

    def runner(cmd: list[str]) -> tuple[int, str, str]:
        calls.append(cmd)
        return returncode, stdout, ""

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_emit_wg_task_passes_dir_explicitly(tmp_path: Path) -> None:
    """Critical: --dir must be in the wg command to avoid CWD resolution bug."""
    runner = _make_fake_wg_runner(
        stdout="Added task: sync upstream: graphwork/workgraph (52 commits) (sync-upstream-graphwork-workgraph)\n"
    )
    eval_result = {"recommended_action": "adopt", "risk_score": 0.3, "category": "behavior"}
    emit_wg_task("graphwork/workgraph", 52, eval_result, tmp_path, wg_runner=runner)

    assert len(runner.calls) == 1
    cmd = runner.calls[0]
    assert "--dir" in cmd
    dir_idx = cmd.index("--dir")
    assert ".workgraph" in cmd[dir_idx + 1]


def test_emit_wg_task_does_not_set_cwd(tmp_path: Path) -> None:
    """Verify emit_wg_task does NOT pass CWD — relies on --dir instead."""
    captured: list[dict] = []

    def runner(cmd: list[str], **kwargs: object) -> tuple[int, str, str]:
        captured.append({"cmd": cmd, "kwargs": kwargs})
        return 0, "Added task: x (y)\n", ""

    eval_result = {"recommended_action": "watch", "risk_score": 0.4, "category": "api-surface"}
    emit_wg_task("agentbureau/agency", 7, eval_result, tmp_path, wg_runner=runner)
    for item in captured:
        assert "cwd" not in item["kwargs"]


def test_emit_wg_task_returns_task_id(tmp_path: Path) -> None:
    runner = _make_fake_wg_runner(
        stdout="Added task: sync upstream: graphwork/workgraph (52 commits) (upstream-workgraph-sync)\n"
    )
    eval_result = {"recommended_action": "adopt", "risk_score": 0.2, "category": "behavior"}
    task_id = emit_wg_task("graphwork/workgraph", 52, eval_result, tmp_path, wg_runner=runner)
    assert task_id == "upstream-workgraph-sync"


def test_emit_wg_task_returns_none_on_failure(tmp_path: Path) -> None:
    runner = _make_fake_wg_runner(returncode=1, stdout="error: something failed\n")
    eval_result = {"recommended_action": "adopt", "risk_score": 0.3, "category": "behavior"}
    task_id = emit_wg_task("graphwork/workgraph", 5, eval_result, tmp_path, wg_runner=runner)
    assert task_id is None


def test_emit_wg_task_include_repo_and_count_in_title(tmp_path: Path) -> None:
    """Task title must include repo name and commit count."""
    runner = _make_fake_wg_runner(
        stdout="Added task: sync upstream: danshapiro/freshell (22 commits) (upstream-freshell-sync)\n"
    )
    eval_result = {"recommended_action": "watch", "risk_score": 0.15, "category": "internals-only"}
    emit_wg_task("danshapiro/freshell", 22, eval_result, tmp_path, wg_runner=runner)
    cmd = runner.calls[0]
    title = next((arg for i, arg in enumerate(cmd) if arg == "add" and i + 1 < len(cmd)), None)
    # Find the title arg (first positional after 'add')
    add_idx = cmd.index("add")
    title_arg = cmd[add_idx + 1]
    assert "danshapiro/freshell" in title_arg
    assert "22" in title_arg


# --- Snapshot: upstream_eval field ---

def test_build_snapshot_entry_upstream_eval_key_from_pass1_results(tmp_path: Path) -> None:
    """upstream_eval dict is derived from pass1_results."""
    import json
    from driftdriver.upstream_tracker import build_snapshot_entry

    state_file = tmp_path / "upstream-tracker-last.json"
    state_file.write_text(json.dumps({
        "timestamp": "2026-01-01T00:00:00+00:00",
        "results": [
            {"repo": "danshapiro/freshell", "llm_eval": "minor UI changes, low risk"},
            {"repo": "graphwork/workgraph", "llm_eval": None},
        ],
    }), encoding="utf-8")

    entry = build_snapshot_entry([], state_dir=tmp_path)
    assert entry["pass1_results"][0]["repo"] == "danshapiro/freshell"
    assert entry["pass1_results"][0]["llm_eval"] == "minor UI changes, low risk"
