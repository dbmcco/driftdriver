# ABOUTME: Speedrift Quality Planner — structures workgraphs with quality intelligence.
# ABOUTME: Reads specs, applies quality patterns from repertoire, produces task graphs.
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from driftdriver.planner_core import (
    BUILTIN_PATTERNS,
    PlannedNode,
    call_llm as _core_call_llm,
    materialize_plan,
    parse_plan_output,
)
from driftdriver.signal_gate import is_gate_enabled, should_fire, record_fire
from driftdriver.drift_task_guard import record_finding_ledger

_log = logging.getLogger(__name__)

# Canonical node type — aliased for backward compatibility.
PlannedTask = PlannedNode


@dataclass
class PlannerOutput:
    tasks: list[PlannedTask] = field(default_factory=list)
    added_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"tasks": [t.to_dict() for t in self.tasks]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)


def load_repertoire() -> dict[str, dict[str, str]]:
    """Return a copy of the built-in quality pattern repertoire."""
    return dict(BUILTIN_PATTERNS)


def build_planner_prompt(
    *,
    spec_content: str,
    north_star: str,
    repertoire: dict[str, dict[str, str]],
    drift_policy_summary: str = "",
) -> str:
    """Build the LLM prompt that instructs the planner to produce a quality-aware task graph."""
    repertoire_text = ""
    for name, pattern in repertoire.items():
        repertoire_text += f"\n### {name}\n"
        repertoire_text += f"- **Description:** {pattern['description']}\n"
        repertoire_text += f"- **When to use:** {pattern['when']}\n"
        repertoire_text += f"- **Structure:** {pattern['structure']}\n"

    return f"""You are the Speedrift Quality Planner. Your job is to take a specification and produce a workgraph task list with quality intelligence baked in.

## North Star
{north_star}

## Specification
{spec_content}

{f"## Drift Policy Summary{chr(10)}{drift_policy_summary}" if drift_policy_summary else ""}

## Quality Pattern Repertoire
These are the quality patterns available. Use your judgment about which to apply and where.
{repertoire_text}

## Your Task
Analyze the specification and produce a structured task graph as JSON. For each implementation task, decide:
1. What type of work is it? (code, UI, data, API, infrastructure, config)
2. What is the risk profile? (low, medium, high)
3. Which quality patterns should follow it, if any?
4. Where should NorthStar checkpoints go? (phase boundaries, after significant decisions)

Use break/fix loops where appropriate. Don't over-test trivial changes. Think about risk.

## Output Format
Respond with ONLY a JSON object:
```json
{{
  "tasks": [
    {{
      "id": "task-slug",
      "title": "Human-readable title",
      "after": ["dependency-task-id"],
      "type": "code|quality-gate|northstar-checkpoint",
      "risk": "low|medium|high",
      "description": "What the agent should do",
      "pattern": "e2e-breakfix|ux-eval|data-eval|contract-test|northstar-checkpoint (if quality-gate)",
      "max_iterations": 3,
      "touch": ["src/path/to/file.ts"],
      "acceptance": ["Build passes", "Tests pass"],
      "verify": "npm run typecheck"
    }}
  ]
}}
```

## CRITICAL: wg-contract blocks
Every code-type task description MUST begin with a wg-contract fenced block so coredrift can check it:
````
```wg-contract
schema = 1
mode = "core"
objective = "The task title"
non_goals = ["Things explicitly out of scope"]
touch = ["src/file1.ts", "src/file2.ts"]
acceptance = ["Acceptance criterion 1", "Acceptance criterion 2"]
max_files = 15
max_loc = 500
```
````
Include the wg-contract block as the FIRST thing in the description field. Put the human-readable instructions after it.
"""


def _read_north_star(repo_path: Path) -> str:
    """Read the North Star alignment statement from drift-policy.toml."""
    policy_path = repo_path / ".workgraph" / "drift-policy.toml"
    if not policy_path.exists():
        policy_path = repo_path / "drift-policy.toml"
    if not policy_path.exists():
        return ""
    try:
        import tomllib

        data = tomllib.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    alignment = (data.get("northstardrift") or {}).get("alignment") or {}
    return str(alignment.get("statement", ""))


def _call_llm(prompt: str, model: str = "sonnet") -> str:
    """Call claude CLI in non-interactive mode and return the response text.

    Thin wrapper delegating to planner_core.call_llm.
    """
    return _core_call_llm(prompt, model=model)


def _parse_plan_output(raw: str) -> PlannerOutput:
    """Extract and parse JSON task list from LLM response, handling markdown code blocks.

    Thin wrapper delegating to planner_core.parse_plan_output.
    """
    nodes = parse_plan_output(raw)
    return PlannerOutput(tasks=list(nodes))


# ---------------------------------------------------------------------------
# Callbacks for materialize_plan — recreate the original wg-add behaviour
# ---------------------------------------------------------------------------


def _quality_desc_builder(node: PlannedNode) -> str:
    """Build task description with quality-pattern section appended."""
    parts: list[str] = []
    if node.description:
        parts.append(node.description)
    if node.pattern and node.pattern in BUILTIN_PATTERNS:
        pattern = BUILTIN_PATTERNS[node.pattern]
        parts.append(f"\n## Quality Pattern: {node.pattern}")
        parts.append(f"{pattern['description']}")
        parts.append(f"Structure: {pattern['structure']}")
        if node.max_iterations:
            parts.append(f"Max iterations: {node.max_iterations}")
    return "\n".join(parts)


def _quality_verify_fallback(node: PlannedNode) -> str:
    """Default verification command for quality-gate / northstar-checkpoint tasks."""
    if node.task_type == "quality-gate" and node.pattern:
        if node.pattern == "e2e-breakfix":
            return "run tests and confirm all pass"
        elif node.pattern == "ux-eval":
            return "evaluate UX criteria and confirm acceptable"
        elif node.pattern == "data-eval":
            return "validate schema and run migration dry-run"
        elif node.pattern == "contract-test":
            return "run contract tests and confirm API matches spec"
    elif node.task_type == "northstar-checkpoint":
        return "assess North Star alignment and confirm score > 0.7"
    return ""


def _quality_tag_builder(node: PlannedNode) -> list[str]:
    """Tag quality gates and checkpoints."""
    if node.task_type in ("quality-gate", "northstar-checkpoint"):
        return ["quality", node.pattern or node.task_type]
    return []


def plan_from_spec(
    *,
    spec_path: Path,
    repo_path: Path,
    dry_run: bool = False,
    model: str = "sonnet",
) -> PlannerOutput:
    """Read a spec file and produce a quality-aware workgraph task plan.

    In dry_run mode, prints a summary and returns an empty PlannerOutput
    without making any LLM calls.
    """
    spec_content = spec_path.read_text(encoding="utf-8")
    north_star = _read_north_star(repo_path)
    repertoire = load_repertoire()

    prompt = build_planner_prompt(
        spec_content=spec_content,
        north_star=north_star,
        repertoire=repertoire,
    )

    if dry_run:
        print(f"[planner dry-run] Would call {model} with {len(prompt)} char prompt")
        print(f"[planner dry-run] North Star: {north_star or '(not configured)'}")
        print(f"[planner dry-run] Patterns available: {', '.join(repertoire.keys())}")
        return PlannerOutput()

    # Signal gate — skip LLM when prompt content is unchanged.
    _gate_agent = "quality_planner"
    _policy_path = repo_path / ".workgraph" / "drift-policy.toml"
    _gate_dir = repo_path / ".workgraph" / ".signal-gates"
    _gate_active = is_gate_enabled(_gate_agent, _policy_path)

    if _gate_active and not should_fire(_gate_agent, prompt, gate_dir=_gate_dir):
        _log.info("[quality_planner] signal gate suppressed LLM call (content unchanged)")
        wg_dir = repo_path / ".workgraph"
        record_finding_ledger(
            wg_dir,
            repo=str(repo_path),
            lane=_gate_agent,
            finding_type="signal_gate_suppressed",
            task_id=f"gate-{_gate_agent}",
            result="suppressed",
            message="LLM call skipped — prompt content unchanged",
        )
        return PlannerOutput()

    raw = _call_llm(prompt, model=model)
    output = _parse_plan_output(raw)

    # Record successful fire so next identical call is gated.
    if _gate_active:
        record_fire(_gate_agent, prompt, gate_dir=_gate_dir)

    # Write tasks via wg add with quality-gate structuring.
    output.added_count = materialize_plan(
        output.tasks,
        repo_path,
        desc_builder=_quality_desc_builder,
        verify_fallback=_quality_verify_fallback,
        tag_builder=_quality_tag_builder,
    )
    return output
