# ABOUTME: Tests for Design Panel — 5 specialist LLM calls + quality gate + synthesis.
# ABOUTME: All LLM callers injected; no real API calls.
from __future__ import annotations

from pathlib import Path

import pytest

from driftdriver.factory.design_panel import (
    DesignPanelResult,
    _quality_gate,
    run_design_panel,
)


def _make_long_text(words: int = 120) -> str:
    return " ".join(["word"] * words)


def _fake_specialist_caller(role: str, north_star: str) -> str:
    """Returns a valid (>100 word) specialist perspective."""
    return _make_long_text(120)


def _fake_short_caller(role: str, north_star: str) -> str:
    """Returns a short (< 100 word) response to trigger quality gate."""
    return "Too short."


def _fake_moderator(transcripts: dict[str, str], north_star: str) -> dict:
    return {
        "plan_summary": "Build a clean, focused service.",
        "tasks": [
            "Set up core data model",
            "Implement API endpoints",
            "Add test coverage",
            "Write NORTH_STAR alignment check",
        ],
    }


def test_quality_gate_passes_long_transcript() -> None:
    transcript = _make_long_text(120)
    assert _quality_gate(transcript) is True


def test_quality_gate_fails_short_transcript() -> None:
    assert _quality_gate("Too short.") is False


def test_run_design_panel_success(tmp_path: Path) -> None:
    north_star = "# North Star — myapp\n\nDoes great things.\n\n## Outcome target\nWork well.\n"
    repo_path = tmp_path / "myapp"
    repo_path.mkdir()
    result = run_design_panel(
        north_star=north_star,
        repo_path=repo_path,
        specialist_caller=_fake_specialist_caller,
        moderator_caller=_fake_moderator,
    )
    assert result.success
    assert len(result.tasks) >= 1
    assert (repo_path / "decomposed_plan.md").exists()


def test_run_design_panel_quality_gate_triggers_retry(tmp_path: Path) -> None:
    """If specialist response is too short, it retries (up to 2 times)."""
    call_counts: dict[str, int] = {}

    def _counting_caller(role: str, north_star: str) -> str:
        call_counts[role] = call_counts.get(role, 0) + 1
        if call_counts[role] == 1:
            return "Too short."
        return _make_long_text(120)

    result = run_design_panel(
        north_star="# North Star — test\n\nDoes things.\n\n## Outcome target\nWork.\n",
        repo_path=tmp_path,
        specialist_caller=_counting_caller,
        moderator_caller=_fake_moderator,
    )
    assert result.success
    assert any(count >= 2 for count in call_counts.values())
