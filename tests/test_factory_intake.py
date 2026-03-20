# ABOUTME: Tests for factory intake parser and complexity scorer.
# ABOUTME: Uses real tmp_path NORTH_STAR.md files; no mocks.
from __future__ import annotations

from pathlib import Path

import pytest

from driftdriver.factory.intake import (
    IntakeProject,
    compute_complexity_score,
    parse_north_star,
    scan_intake_dir,
)


def _write_ns(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_parse_simple_north_star(tmp_path: Path) -> None:
    ns_path = tmp_path / "myproject" / "NORTH_STAR.md"
    _write_ns(ns_path, """\
# North Star — myproject

This is a simple tool.

## Outcome target
Fast, reliable, single-purpose.

## Current phase
`onboarded`
""")
    project = parse_north_star("myproject", ns_path)
    assert project.name == "myproject"
    assert project.outcome_target == "Fast, reliable, single-purpose."
    assert project.current_phase == "onboarded"
    assert project.complexity_hints == {}


def test_parse_north_star_with_hints(tmp_path: Path) -> None:
    ns_path = tmp_path / "bigproject" / "NORTH_STAR.md"
    _write_ns(ns_path, """\
# North Star — bigproject

A multi-domain platform.

## Outcome target
Production-grade API platform.

## Current phase
`onboarded`

## Complexity hints
- domain_count: 4
- has_external_integrations: true
- estimated_loc: 8000
""")
    project = parse_north_star("bigproject", ns_path)
    assert project.complexity_hints["domain_count"] == 4
    assert project.complexity_hints["has_external_integrations"] is True
    assert project.complexity_hints["estimated_loc"] == 8000


def test_complexity_score_simple_project() -> None:
    hints: dict = {}
    score = compute_complexity_score(hints)
    assert score < 0.5


def test_complexity_score_complex_project() -> None:
    hints = {
        "domain_count": 5,
        "has_external_integrations": True,
        "estimated_loc": 10000,
    }
    score = compute_complexity_score(hints)
    assert score >= 0.5


def test_scan_intake_dir_finds_projects(tmp_path: Path) -> None:
    intake_dir = tmp_path / "factory" / "intake"
    for name in ["alpha", "beta"]:
        ns = intake_dir / name / "NORTH_STAR.md"
        _write_ns(ns, f"# North Star — {name}\n\nDoes {name} things.\n\n## Outcome target\nWork well.\n\n## Current phase\n`onboarded`\n")
    projects = scan_intake_dir(intake_dir)
    assert len(projects) == 2
    names = {p.name for p in projects}
    assert names == {"alpha", "beta"}


def test_scan_intake_dir_missing_returns_empty(tmp_path: Path) -> None:
    projects = scan_intake_dir(tmp_path / "nonexistent" / "intake")
    assert projects == []
