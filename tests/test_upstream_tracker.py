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
