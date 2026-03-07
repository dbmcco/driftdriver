# ABOUTME: Planner service — decomposes goals into task subgraphs
# ABOUTME: Writes tasks to the workgraph and stops; no dispatch or quality checking

from __future__ import annotations

from pathlib import Path

DECOMPOSE_PROMPT_TEMPLATE = """\
You are a project planner. Given a high-level goal, decompose it into
concrete workgraph tasks with dependencies.

## Goal
{goal}

## Project Directory
{project_dir}

## Instructions
1. Research the goal by reading relevant files in the project.
2. Create workgraph tasks using `wg add`. Each task must have:
   - A short `--id` (kebab-case, e.g., `feat-auth-login`)
   - A clear title
   - A `-d` description covering: what to do, which files to touch, acceptance criteria
   - `--after` dependencies where appropriate
   - `--immediate` to bypass draft mode
3. Keep tasks small — each should be completable in one focused session.
4. After creating all tasks, run:
   ./.workgraph/coredrift ensure-contracts --apply
5. Print a summary of the tasks you created (id + title + deps).
6. Do NOT implement anything. Planning only.
"""


def build_decompose_prompt(goal: str, project_dir: Path) -> str:
    """Build the prompt that instructs the planner to decompose a goal into tasks."""
    return DECOMPOSE_PROMPT_TEMPLATE.format(
        goal=goal,
        project_dir=str(project_dir),
    )
