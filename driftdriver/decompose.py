# ABOUTME: Goal decomposition — breaks a high-level goal into workgraph tasks via LLM.
# ABOUTME: Emits create_task directives. Replaces project_autopilot's decomposition logic.

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from driftdriver.directives import Action, Directive, DirectiveLog
from driftdriver.drift_task_guard import record_finding_ledger
from driftdriver.executor_shim import ExecutorShim
from driftdriver.signal_gate import is_gate_enabled, record_fire, should_fire

_log = logging.getLogger(__name__)


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
    # Signal gate — skip LLM when goal+context content is unchanged.
    _gate_agent = "decompose"
    _policy_path = wg_dir / "drift-policy.toml"
    _gate_dir = wg_dir / ".signal-gates"
    _gate_input = {"goal": goal, "context": context}
    _gate_active = is_gate_enabled(_gate_agent, _policy_path)

    if _gate_active and not should_fire(_gate_agent, _gate_input, gate_dir=_gate_dir):
        _log.info("[decompose] signal gate suppressed LLM call (content unchanged)")
        record_finding_ledger(
            wg_dir,
            repo=repo,
            lane=_gate_agent,
            finding_type="signal_gate_suppressed",
            task_id=f"gate-{_gate_agent}",
            result="suppressed",
            message="LLM call skipped — goal+context unchanged",
        )
        return {"goal": goal, "task_count": 0}

    tasks = _call_llm(goal, context)

    # Record successful fire so next identical call is gated.
    if _gate_active:
        record_fire(_gate_agent, _gate_input, gate_dir=_gate_dir)

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
