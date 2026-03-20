# ABOUTME: Tests for desired_outcome stamping on drift task descriptions.
# ABOUTME: Verifies NORTH_STAR.md is read and appended correctly.
from __future__ import annotations

from pathlib import Path

import pytest

from driftdriver.drift_task_guard import extract_desired_outcome


def test_no_north_star_returns_none(tmp_path: Path) -> None:
    assert extract_desired_outcome(tmp_path) is None


def test_north_star_with_outcome_section(tmp_path: Path) -> None:
    (tmp_path / "NORTH_STAR.md").write_text(
        "# North Star\n\n## Outcome target\n\nLean, fast, maintainable.\n\n## Other\nstuff\n"
    )
    result = extract_desired_outcome(tmp_path)
    assert result == "Lean, fast, maintainable."


def test_north_star_without_outcome_section_uses_first_paragraph(tmp_path: Path) -> None:
    (tmp_path / "NORTH_STAR.md").write_text(
        "# North Star\n\nThis repo does one thing well.\n\n## Other section\nstuff\n"
    )
    result = extract_desired_outcome(tmp_path)
    assert result == "This repo does one thing well."


def test_empty_north_star_returns_none(tmp_path: Path) -> None:
    (tmp_path / "NORTH_STAR.md").write_text("")
    assert extract_desired_outcome(tmp_path) is None


def test_desired_outcome_appended_to_description(tmp_path: Path) -> None:
    (tmp_path / "NORTH_STAR.md").write_text(
        "# North Star\n\n## Outcome target\n\nClean API surface.\n"
    )
    from driftdriver.drift_task_guard import stamp_desired_outcome

    desc = "Fix the bug in parser"
    result = stamp_desired_outcome(desc, tmp_path)
    assert "Fix the bug in parser" in result
    assert "Clean API surface." in result
    assert "desired_outcome" in result.lower()


def test_no_north_star_description_unchanged(tmp_path: Path) -> None:
    from driftdriver.drift_task_guard import stamp_desired_outcome

    desc = "Fix the bug"
    assert stamp_desired_outcome(desc, tmp_path) == desc
