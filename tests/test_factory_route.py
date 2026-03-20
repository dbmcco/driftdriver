# ABOUTME: Tests for intake routing — simple vs complex path selection.
# ABOUTME: Uses injected scaffold and design_panel callables.
from __future__ import annotations

from pathlib import Path

import pytest

from driftdriver.factory.intake import (
    IntakeProject,
    compute_complexity_score,
    parse_north_star,
    route_project,
)


def _make_project(name: str, hints: dict, tmp_path: Path) -> IntakeProject:
    ns_path = tmp_path / "intake" / name / "NORTH_STAR.md"
    ns_path.parent.mkdir(parents=True)
    ns_path.write_text(
        f"# North Star — {name}\n\nDoes {name}.\n\n## Outcome target\nWork.\n\n## Current phase\n`onboarded`\n"
    )
    p = parse_north_star(name, ns_path)
    p.complexity_hints.update(hints)
    return p


def _fake_scaffold_result(name: str, workspace: Path):
    """Create a minimal fake ScaffoldResult-like object."""
    class _R:
        success = True
        skipped = False
        error = ""
        repo_path = workspace / name
    return _R()


def test_simple_project_routes_to_scaffold(tmp_path: Path) -> None:
    project = _make_project("simple", {}, tmp_path)
    assert compute_complexity_score(project.complexity_hints) < 0.5

    scaffolded = []
    panel_run = []

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    route_project(
        project,
        workspace_root=workspace,
        scaffold_fn=lambda p, ws: (scaffolded.append(p.name), _fake_scaffold_result(p.name, ws))[1],
        design_panel_fn=lambda p, rp: panel_run.append(p.name),
    )
    assert "simple" in scaffolded
    assert "simple" not in panel_run


def test_complex_project_routes_to_design_panel(tmp_path: Path) -> None:
    project = _make_project("complex", {
        "domain_count": 5,
        "has_external_integrations": True,
        "estimated_loc": 10000,
    }, tmp_path)
    assert compute_complexity_score(project.complexity_hints) >= 0.5

    scaffolded = []
    panel_run = []

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    route_project(
        project,
        workspace_root=workspace,
        scaffold_fn=lambda p, ws: (scaffolded.append(p.name), _fake_scaffold_result(p.name, ws))[1],
        design_panel_fn=lambda p, rp: panel_run.append(p.name),
    )
    assert "complex" in scaffolded
    assert "complex" in panel_run
