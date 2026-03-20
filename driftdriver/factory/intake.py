# ABOUTME: Scans /factory/intake/ for new project declarations.
# ABOUTME: Parses NORTH_STAR.md, computes complexity score, routes to scaffold or Design Panel.
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

_COMPLEXITY_THRESHOLD = 0.5
_STATE_FILENAME = ".factory-state.json"


@dataclass
class IntakeProject:
    name: str
    north_star_path: Path
    summary: str
    outcome_target: str
    current_phase: str
    complexity_hints: dict[str, Any] = field(default_factory=dict)


def parse_north_star(project_name: str, ns_path: Path) -> IntakeProject:
    """Parse a NORTH_STAR.md and return an IntakeProject."""
    text = ns_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    summary = ""
    outcome_target = ""
    current_phase = "onboarded"
    hints: dict[str, Any] = {}

    section = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            section = stripped[3:].lower()
            continue
        if stripped.startswith("# "):
            continue  # title line

        if section is None and stripped and not summary:
            summary = stripped
            continue

        if section and "outcome target" in section:
            if stripped and not outcome_target:
                outcome_target = stripped

        if section and "current phase" in section:
            if stripped:
                current_phase = stripped.strip("`")

        if section and "complexity hints" in section:
            m = re.match(r"-\s+(\w+):\s+(.+)", stripped)
            if m:
                key = m.group(1).strip()
                val_str = m.group(2).strip().lower()
                if val_str.isdigit():
                    hints[key] = int(val_str)
                elif val_str in ("true", "yes"):
                    hints[key] = True
                elif val_str in ("false", "no"):
                    hints[key] = False
                else:
                    try:
                        hints[key] = int(val_str)
                    except ValueError:
                        hints[key] = val_str

    return IntakeProject(
        name=project_name,
        north_star_path=ns_path,
        summary=summary,
        outcome_target=outcome_target,
        current_phase=current_phase,
        complexity_hints=hints,
    )


def compute_complexity_score(hints: dict[str, Any]) -> float:
    """Compute complexity_score from NORTH_STAR.md complexity hints.

    Returns float in [0.0, 1.0]. Threshold: < 0.5 → simple path, >= 0.5 → Design Panel.
    """
    domain_count = int(hints.get("domain_count") or 0)
    has_external = 1.0 if hints.get("has_external_integrations") else 0.0
    estimated_loc = int(hints.get("estimated_loc") or 0)

    domain_normalized = min(domain_count / 5.0, 1.0)
    loc_normalized = min(estimated_loc / 10_000.0, 1.0)
    dep_normalized = 0.0  # not yet surfaced in NORTH_STAR format

    score = (
        0.4 * domain_normalized
        + 0.3 * has_external
        + 0.2 * loc_normalized
        + 0.1 * dep_normalized
    )
    return round(score, 3)


def scan_intake_dir(intake_dir: Path) -> list[IntakeProject]:
    """Scan /factory/intake/ for NORTH_STAR.md files. Returns all valid projects."""
    if not intake_dir.is_dir():
        return []
    projects = []
    for ns_path in sorted(intake_dir.glob("*/NORTH_STAR.md")):
        project_name = ns_path.parent.name
        try:
            project = parse_north_star(project_name, ns_path)
            projects.append(project)
        except Exception:
            continue
    return projects


def _read_state(intake_project_dir: Path) -> dict[str, Any]:
    state_file = intake_project_dir / _STATE_FILENAME
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _write_state(intake_project_dir: Path, state: dict[str, Any]) -> None:
    state_file = intake_project_dir / _STATE_FILENAME
    state_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(state_file)


def route_project(
    project: IntakeProject,
    *,
    workspace_root: Path,
    scaffold_fn: Callable | None = None,
    design_panel_fn: Callable | None = None,
) -> dict[str, Any]:
    """Route a project through simple or complex creation path.

    Returns status dict. Records progress in .factory-state.json in the intake dir.
    """
    from driftdriver.factory.scaffold import scaffold_project

    state = _read_state(project.north_star_path.parent)
    if state.get("status") == "scaffolded" and state.get("design_panel_done"):
        return {"status": "already_complete", "project": project.name}

    # Step 1: Scaffold (idempotent)
    _scaffold = scaffold_fn or (lambda p, ws: scaffold_project(p, workspace_root=ws))
    scaffold_result = _scaffold(project, workspace_root)

    if not (scaffold_result.success or scaffold_result.skipped):
        return {"status": "scaffold_failed", "error": scaffold_result.error, "project": project.name}

    repo_path = scaffold_result.repo_path or (workspace_root / project.name)
    state["status"] = "scaffolded"
    state["repo_path"] = str(repo_path)
    _write_state(project.north_star_path.parent, state)

    # Step 2: Route by complexity
    complexity = compute_complexity_score(project.complexity_hints)
    if complexity >= _COMPLEXITY_THRESHOLD and not state.get("design_panel_done"):
        if design_panel_fn is not None:
            design_panel_fn(project, repo_path)
        else:
            from driftdriver.factory.design_panel import run_design_panel
            run_design_panel(
                north_star=project.north_star_path.read_text(encoding="utf-8"),
                repo_path=repo_path,
            )
        state["design_panel_done"] = True
        _write_state(project.north_star_path.parent, state)

    return {"status": "routed", "project": project.name, "complexity": complexity}
