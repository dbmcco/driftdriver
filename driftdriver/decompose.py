# ABOUTME: Goal decomposition — breaks a high-level goal into workgraph tasks via LLM.
# ABOUTME: Emits create_task directives. Replaces project_autopilot's decomposition logic.

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim


def _call_llm(goal: str, context: str) -> list[dict[str, Any]]:
    """Call LLM to decompose goal into task list. Returns list of task dicts."""
    prompt = (
        f"Decompose this goal into 3-8 concrete, dependency-ordered tasks "
        f"for a workgraph. Return JSON array of objects with id, title, "
        f"description, after (list of dependency ids).\n\n"
        f"Goal: {goal}\n\nContext: {context}\n"
    )
    result = subprocess.run(
        ["claude", "--print", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=120,
    )
    text = result.stdout.strip()
    start = text.find("[")
    end = text.rfind("]") + 1
    if start >= 0 and end > start:
        return json.loads(text[start:end])
    return []


def decompose_goal(
    *,
    goal: str,
    wg_dir: Path,
    directive_log: DirectiveLog,
    repo: str = "",
    context: str = "",
) -> dict[str, Any]:
    """Decompose a goal into workgraph tasks via LLM, emitting create_task directives.

    Returns dict with goal, task_count.
    """
    tasks = _call_llm(goal, context)
    shim = ExecutorShim(wg_dir=wg_dir, log=directive_log)

    for task in tasks:
        directive = Directive(
            source="decompose",
            repo=repo,
            action=Action.CREATE_TASK,
            params={
                "task_id": task["id"],
                "title": task["title"],
                "description": task.get("description", ""),
                "after": task.get("after", []),
                "tags": ["decomposed"],
            },
            reason=f"decomposed from goal: {goal[:80]}",
        )
        shim.execute(directive)

    return {"goal": goal, "task_count": len(tasks)}
