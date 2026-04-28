# ABOUTME: Scaffolds new repos from factory intake declarations.
# ABOUTME: git init + NORTH_STAR.md + drift-policy.toml + workgraph attractor structure.
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from driftdriver.factory.intake import IntakeProject


@dataclass
class ScaffoldResult:
    success: bool = False
    skipped: bool = False
    error: str = ""
    repo_path: Path | None = None


_MINIMAL_DRIFT_POLICY = """\
[policy]
version = "1.0"
lifecycle = "active"
daemon_posture = "supervised"

[budgets.lane]
max_open = 3
max_hourly = 10

[attractor]
target = "production-ready"
"""

_ATTRACTOR_TOML = """\
[attractor]
from_state = "onboarded"
to_state = "production-ready"
description = "Drive new project from scaffold to production-ready state"

[circuit_breakers]
max_passes = 3
task_budget = 30
"""


def _git_init(repo_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "factory@driftdriver"],
        cwd=str(repo_path), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Dark Factory"],
        cwd=str(repo_path), check=True, capture_output=True,
    )


def scaffold_project(
    project: IntakeProject,
    *,
    workspace_root: Path,
    wg_init: Callable[[Path], None] | None = None,
) -> ScaffoldResult:
    """Scaffold a new repo for the given project.

    Returns ScaffoldResult with success=True or skipped=True if already exists.
    wg_init is injectable (default initializes `.workgraph` via subprocess).
    """
    repo_path = workspace_root / project.name

    if (repo_path / ".git").exists():
        return ScaffoldResult(skipped=True, repo_path=repo_path)

    try:
        repo_path.mkdir(parents=True, exist_ok=True)

        # 1. git init
        _git_init(repo_path)

        # 2. Copy NORTH_STAR.md from intake
        ns_content = project.north_star_path.read_text(encoding="utf-8")
        (repo_path / "NORTH_STAR.md").write_text(ns_content, encoding="utf-8")

        # 3. Minimal drift-policy.toml
        (repo_path / "drift-policy.toml").write_text(_MINIMAL_DRIFT_POLICY, encoding="utf-8")

        # 4. Workgraph structure
        wg_dir = repo_path / ".workgraph"
        wg_dir.mkdir(exist_ok=True)
        (wg_dir / "graph.jsonl").touch()

        # 5. Attractor declaration
        attractors_dir = wg_dir / "attractors"
        attractors_dir.mkdir(exist_ok=True)
        (attractors_dir / "onboarded-to-production-ready.toml").write_text(
            _ATTRACTOR_TOML, encoding="utf-8"
        )

        # 6. Run wg init (injectable for testing)
        if wg_init is not None:
            wg_init(repo_path)
        else:
            _default_wg_init(repo_path)

        # 7. Initial git commit
        subprocess.run(["git", "add", "."], cwd=str(repo_path), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: scaffold {project.name} from dark factory intake"],
            cwd=str(repo_path), check=True, capture_output=True,
        )

        return ScaffoldResult(success=True, repo_path=repo_path)

    except Exception as exc:
        return ScaffoldResult(error=str(exc))


def _default_wg_init(repo_path: Path) -> None:
    """Run `wg init` in the new repo. Best-effort — fails silently."""
    try:
        subprocess.run(
            ["wg", "--dir", str(repo_path / ".workgraph"), "init", "--model", "claude:opus"],
            cwd=str(repo_path),
            capture_output=True,
            timeout=15.0,
        )
    except Exception:
        pass  # wg not installed — workgraph structure already created manually
