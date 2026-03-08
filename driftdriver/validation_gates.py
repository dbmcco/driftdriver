# ABOUTME: Validation gates — judgment layer for task completion verification.
# ABOUTME: Migrated from wg feature/project-protocol branch. Emits directives, doesn't modify wg.

from __future__ import annotations

from pathlib import Path
from typing import Any

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.executor_shim import ExecutorShim


def check_validation_gates(
    *,
    task: dict[str, Any],
    wg_dir: Path,
    directive_log: DirectiveLog,
) -> dict[str, Any]:
    """Check if a task requires validation before completion.

    If the task has a 'verify' field, emit a create_validation directive
    to create a validation subtask blocked by the original.

    Returns dict with validation_required (bool), task_id, and criteria.
    """
    task_id = task.get("id", "")
    verify = task.get("verify", "")

    if not verify:
        return {"validation_required": False, "task_id": task_id}

    directive = Directive(
        source="validation_gates",
        repo="",
        action=Action.CREATE_VALIDATION,
        params={
            "parent_task_id": task_id,
            "criteria": verify,
        },
        reason=f"task {task_id} has verify criteria",
    )

    shim = ExecutorShim(wg_dir=wg_dir, log=directive_log)
    shim.execute(directive)

    return {"validation_required": True, "task_id": task_id, "criteria": verify}
