# ABOUTME: Tests for factory scaffold — git init, drift-policy, workgraph structure.
# ABOUTME: Uses real git and real tmp_path; wg init is stubbed via a callable.
from __future__ import annotations

from pathlib import Path

import pytest

from driftdriver.factory.intake import IntakeProject, parse_north_star
from driftdriver.factory.scaffold import ScaffoldResult, scaffold_project


def _make_project(name: str, tmp_path: Path) -> IntakeProject:
    ns_path = tmp_path / "intake" / name / "NORTH_STAR.md"
    ns_path.parent.mkdir(parents=True)
    ns_path.write_text(
        f"# North Star — {name}\n\nDoes {name}.\n\n## Outcome target\nWork well.\n\n## Current phase\n`onboarded`\n"
    )
    return parse_north_star(name, ns_path)


def test_scaffold_creates_git_repo(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    result = scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    assert result.success
    assert (workspace / "myapp" / ".git").exists()


def test_scaffold_writes_north_star(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    ns = workspace / "myapp" / "NORTH_STAR.md"
    assert ns.exists()
    assert "North Star" in ns.read_text()


def test_scaffold_writes_drift_policy(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    policy = workspace / "myapp" / "drift-policy.toml"
    assert policy.exists()


def test_scaffold_idempotent(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    r1 = scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    r2 = scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    assert r1.success
    assert r2.skipped


def test_scaffold_creates_attractor_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = _make_project("myapp", tmp_path)
    scaffold_project(project, workspace_root=workspace, wg_init=lambda p: None)
    attractor = workspace / "myapp" / ".workgraph" / "attractors" / "onboarded-to-production-ready.toml"
    assert attractor.exists()
