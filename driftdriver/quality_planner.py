# ABOUTME: Speedrift Quality Planner — structures workgraphs with quality intelligence.
# ABOUTME: Reads specs, applies quality patterns from repertoire, produces task graphs.
from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


BUILTIN_PATTERNS: dict[str, dict[str, str]] = {
    "e2e-breakfix": {
        "description": "Run end-to-end tests, diagnose failures, fix, retest. Max N iterations.",
        "when": "Any code that has testable behavior.",
        "structure": "implement -> test -> [fail? -> fix -> retest, max N] -> proceed",
    },
    "ux-eval": {
        "description": "Evaluate UI against UX criteria (accessibility, responsiveness, interaction patterns).",
        "when": "User-facing changes.",
        "structure": "implement -> UX eval -> [issues? -> fix -> re-eval, max N] -> proceed",
    },
    "data-eval": {
        "description": "Validate data model changes against integrity constraints, migration safety, rollback.",
        "when": "Schema changes, migrations, data pipeline changes.",
        "structure": "implement -> validate schema + dry-run -> [issues? -> fix -> re-validate] -> proceed",
    },
    "contract-test": {
        "description": "Verify API contracts match spec.",
        "when": "API endpoints, inter-service communication.",
        "structure": "implement -> contract test -> [drift? -> fix -> retest] -> proceed",
    },
    "northstar-checkpoint": {
        "description": "Invoke NorthStarDrift v2 alignment check scoped to this graph's completed work.",
        "when": "Phase boundaries, after significant directional decisions.",
        "structure": "assess alignment -> [aligned? proceed | drifting? warn | lost? pause + escalate]",
    },
}


@dataclass
class PlannedTask:
    id: str
    title: str
    after: list[str] = field(default_factory=list)
    task_type: str = "code"
    risk: str = "medium"
    description: str = ""
    pattern: str | None = None
    max_iterations: int | None = None
    verify: str = ""
    touch: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "after": self.after,
            "type": self.task_type,
            "risk": self.risk,
        }
        if self.description:
            d["description"] = self.description
        if self.pattern:
            d["pattern"] = self.pattern
        if self.max_iterations is not None:
            d["max_iterations"] = self.max_iterations
        if self.verify:
            d["verify"] = self.verify
        if self.touch:
            d["touch"] = self.touch
        if self.acceptance:
            d["acceptance"] = self.acceptance
        return d


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
    """Call an LLM via `claude --print` and return the response text."""
    try:
        result = subprocess.run(
            ["claude", "--print", "--model", model, "-p", "-"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"warning: LLM call failed: {e}", file=sys.stderr)
        return ""


def _parse_plan_output(raw: str) -> PlannerOutput:
    """Extract and parse JSON task list from LLM response, handling markdown code blocks."""
    text = raw.strip()
    if not text:
        return PlannerOutput()

    # Strategy: try direct JSON parse first, then extract from code blocks.
    # The direct parse handles cases where the JSON contains backticks in string values.
    data = None

    # 1. Try parsing the raw text directly (may work if no wrapping code block)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Try extracting from ```json ... ``` by finding the outermost block
    if data is None and "```json" in text:
        start = text.index("```json") + len("```json")
        # Find the LAST ``` (the closing one for the outermost block)
        last_fence = text.rfind("```")
        if last_fence > start:
            candidate = text[start:last_fence].strip()
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # 3. Try finding { ... } spanning the largest balanced region
    if data is None:
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            candidate = text[first_brace : last_brace + 1]
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                pass

    if data is None:
        return PlannerOutput()

    tasks: list[PlannedTask] = []
    for t in data.get("tasks", []):
        tasks.append(
            PlannedTask(
                id=t.get("id", ""),
                title=t.get("title", ""),
                after=t.get("after", []),
                task_type=t.get("type", "code"),
                risk=t.get("risk", "medium"),
                description=t.get("description", ""),
                pattern=t.get("pattern"),
                max_iterations=t.get("max_iterations"),
                verify=t.get("verify", ""),
                touch=t.get("touch", []),
                acceptance=t.get("acceptance", []),
            )
        )
    return PlannerOutput(tasks=tasks)


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

    raw = _call_llm(prompt, model=model)
    output = _parse_plan_output(raw)

    # Write tasks via wg add with quality-gate structuring
    added_count = 0
    for task in output.tasks:
        cmd = ["wg", "add", task.title, "--immediate"]
        if task.after:
            for dep in task.after:
                cmd.extend(["--after", dep])

        # Build description with quality gate context
        desc_parts = []
        if task.description:
            desc_parts.append(task.description)

        if task.pattern and task.pattern in BUILTIN_PATTERNS:
            pattern = BUILTIN_PATTERNS[task.pattern]
            desc_parts.append(f"\n## Quality Pattern: {task.pattern}")
            desc_parts.append(f"{pattern['description']}")
            desc_parts.append(f"Structure: {pattern['structure']}")
            if task.max_iterations:
                desc_parts.append(f"Max iterations: {task.max_iterations}")

        if desc_parts:
            cmd.extend(["-d", "\n".join(desc_parts)])

        # Add verification command — prefer explicit verify from LLM, fall back to pattern defaults
        if task.verify:
            cmd.extend(["--verify", task.verify])
        elif task.task_type == "quality-gate" and task.pattern:
            if task.pattern == "e2e-breakfix":
                cmd.extend(["--verify", "run tests and confirm all pass"])
            elif task.pattern == "ux-eval":
                cmd.extend(["--verify", "evaluate UX criteria and confirm acceptable"])
            elif task.pattern == "data-eval":
                cmd.extend(["--verify", "validate schema and run migration dry-run"])
            elif task.pattern == "contract-test":
                cmd.extend(["--verify", "run contract tests and confirm API matches spec"])
        elif task.task_type == "northstar-checkpoint":
            cmd.extend(["--verify", "assess North Star alignment and confirm score > 0.7"])

        # Tag quality gates and checkpoints
        if task.task_type in ("quality-gate", "northstar-checkpoint"):
            cmd.extend(["--tag", f"quality,{task.pattern or task.task_type}"])

        try:
            result = subprocess.run(
                cmd, cwd=str(repo_path), capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                added_count += 1
            else:
                stderr = result.stderr.strip()
                if stderr:
                    print(f"warning: wg add for '{task.id}': {stderr}", file=sys.stderr)
        except subprocess.TimeoutExpired:
            print(f"warning: wg add timed out for '{task.id}' (workgraph daemon may be unresponsive)", file=sys.stderr)
        except Exception as e:
            print(f"warning: wg add failed for {task.id}: {e}", file=sys.stderr)

    output.added_count = added_count
    return output
