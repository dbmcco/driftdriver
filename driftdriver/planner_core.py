# ABOUTME: Consolidated decomposition core — canonical node schema, model-route
# ABOUTME: policy, shared parse/materialize for all driftdriver planner surfaces.
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Quality pattern repertoire
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Canonical node schema
# ---------------------------------------------------------------------------


@dataclass
class PlannedNode:
    """A single task node in a decomposed workgraph plan."""

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
    model: str = ""
    route_tier: str = ""
    escalation_reason: str = ""

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
        if self.model:
            d["model"] = self.model
        if self.route_tier:
            d["route_tier"] = self.route_tier
        if self.escalation_reason:
            d["escalation_reason"] = self.escalation_reason
        return d


# ---------------------------------------------------------------------------
# Model route policy
# ---------------------------------------------------------------------------


@dataclass
class ModelRoutePolicy:
    """Policy governing which model providers may be assigned to workgraph tasks."""

    prohibited_prefixes: tuple[str, ...] = ("anthropic",)
    conditional_providers: tuple[str, ...] = ("lunaroute",)
    tier_prefixes: dict[str, tuple[str, ...]] = field(default_factory=lambda: {
        "fast": ("ollama:",),
        "standard": ("zai:", "kimi-coding:kimi-for-coding"),
        "premium": ("kimi-coding:k3", "openai-codex"),
    })
    default_tier: str = "fast"
    require_escalation_reason_for: tuple[str, ...] = ("premium",)

    def tier_of(self, model: str) -> str:
        """Return the tier name for *model* based on prefix matching.

        First matching prefix wins.  No match returns ``"unknown"``.
        """
        for tier, prefixes in self.tier_prefixes.items():
            for prefix in prefixes:
                if model.startswith(prefix):
                    return tier
        return "unknown"


DEFAULT_MODEL_ROUTE_POLICY = ModelRoutePolicy()


@dataclass
class RouteViolation:
    """A model-route assignment that violates the active policy."""

    node_id: str
    model: str
    reason: str
    kind: str = "prohibited"


def _provider_prefix(model: str) -> str:
    """Extract the provider prefix — the portion before the first ':' or '/'."""
    for i, ch in enumerate(model):
        if ch == ":" or ch == "/":
            return model[:i]
    return model


def validate_model_routes(
    route_models: dict[str, str],
    *,
    policy: ModelRoutePolicy = DEFAULT_MODEL_ROUTE_POLICY,
    allow_conditional: bool = False,
    escalation_reasons: dict[str, str] | None = None,
) -> list[RouteViolation]:
    """Check each node→model assignment against the route policy.

    Prohibited prefixes are always flagged (kind=``"prohibited"``).
    Conditional providers are flagged unless *allow_conditional* is True
    (kind=``"conditional"``).
    Models in a tier listed in *require_escalation_reason_for* without a
    non-empty entry in *escalation_reasons* are flagged
    (kind=``"missing-escalation-reason"``).
    """
    violations: list[RouteViolation] = []
    for node_id, model in route_models.items():
        prefix = _provider_prefix(model)
        prefix_lower = prefix.lower()
        flagged = False
        if prefix_lower in policy.prohibited_prefixes:
            violations.append(RouteViolation(
                node_id=node_id,
                model=model,
                kind="prohibited",
                reason=(
                    f"Model '{model}' uses provider '{prefix}' which is "
                    f"prohibited for workgraph-dispatched tasks."
                ),
            ))
            flagged = True
        elif prefix_lower in policy.conditional_providers and not allow_conditional:
            violations.append(RouteViolation(
                node_id=node_id,
                model=model,
                kind="conditional",
                reason=(
                    f"Model '{model}' uses conditional provider '{prefix}' "
                    f"which requires explicit opt-in (allow_conditional=True)."
                ),
            ))
            flagged = True

        if not flagged:
            tier = policy.tier_of(model)
            if tier in policy.require_escalation_reason_for:
                reason = (escalation_reasons or {}).get(node_id, "")
                if not reason:
                    violations.append(RouteViolation(
                        node_id=node_id,
                        model=model,
                        kind="missing-escalation-reason",
                        reason=(
                            f"Model '{model}' is tier '{tier}' which requires "
                            f"an explicit escalation reason, but none was provided."
                        ),
                    ))
    return violations


# ---------------------------------------------------------------------------
# Policy bundles
# ---------------------------------------------------------------------------


@dataclass
class PolicyBundle:
    """Configuration that shapes how build_decompose_prompt assembles the prompt."""

    name: str
    mode: str  # "emit-json" or "agent-executes"
    task_count_hint: str | None = None
    patterns: dict[str, dict[str, str]] = field(default_factory=dict)
    extra_instructions: str = ""
    granularity_bar: bool = False
    request_routes: bool = False


BUNDLE_DECOMPOSE_CLI = PolicyBundle(
    name="decompose-cli", mode="emit-json", task_count_hint="3-8",
)
BUNDLE_QUALITY_SPEC = PolicyBundle(
    name="quality-spec", mode="emit-json", patterns=BUILTIN_PATTERNS,
    granularity_bar=True, request_routes=True,
)


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------


def build_decompose_prompt(
    goal: str,
    *,
    project_dir: Path | None = None,
    context: str = "",
    bundle: PolicyBundle,
    spec_content: str | None = None,
    north_star: str | None = None,
) -> str:
    """Build a decomposition prompt according to the policy bundle's mode.

    *agent-executes* mode reproduces planner.py's instruction to create tasks
    directly via ``wg add``.  *emit-json* mode asks for a structured JSON array
    of PlannedNode objects, optionally enriched with quality-pattern guidance.
    """
    sections: list[str] = []

    if bundle.mode == "agent-executes":
        sections.append(
            "You are a project planner. Given a high-level goal, decompose it into\n"
            "concrete workgraph tasks with dependencies.\n"
        )
        sections.append(f"## Goal\n{goal}\n")
        if project_dir is not None:
            sections.append(f"## Project Directory\n{project_dir}\n")
        if context:
            sections.append(f"## Context\n{context}\n")
        sections.append(
            "## Instructions\n"
            "1. Research the goal by reading relevant files in the project.\n"
            "2. Create workgraph tasks using `wg add`. Each task must have:\n"
            "   - A short `--id` (kebab-case, e.g., `feat-auth-login`)\n"
            "   - A clear title\n"
            "   - A `-d` description covering: what to do, which files to touch, "
            "acceptance criteria\n"
            "   - `--after` dependencies where appropriate\n"
            "   - `--no-place` when the task should be immediately dispatchable\n"
            "3. Keep tasks small — each should be completable in one focused session.\n"
            "4. After creating all tasks, run:\n"
            "   ./.workgraph/coredrift ensure-contracts --apply\n"
            "5. Print a summary of the tasks you created (id + title + deps).\n"
            "6. Do NOT implement anything. Planning only.\n"
        )
    elif bundle.mode == "emit-json":
        sections.append(
            "You are a decomposition planner. Given a high-level goal, produce a\n"
            "dependency-ordered task graph as structured JSON.\n"
        )
        # Goal section: omit entirely when goal is empty and a spec is provided.
        if goal or not spec_content:
            sections.append(f"## Goal\n{goal}\n")
        if north_star:
            sections.append(f"## North Star\n{north_star}\n")
        if spec_content:
            sections.append(f"## Specification\n{spec_content}\n")
        if project_dir is not None:
            sections.append(f"## Project Directory\n{project_dir}\n")
        if context:
            sections.append(f"## Context\n{context}\n")
        if bundle.task_count_hint:
            sections.append(f"## Task Count\nProduce {bundle.task_count_hint} tasks.\n")
        if bundle.granularity_bar:
            sections.append(
                "## Task Granularity\n"
                "Keep tasks small — each completable in one focused session, "
                "with one clear responsibility per task. Add dependency edges "
                "where sequencing genuinely matters; do not over-wire. "
                "A task that needs another's output depends on it; "
                "everything else stays parallel.\n"
            )
        if bundle.patterns:
            repertoire_text = "\n"
            for pname, pattern in bundle.patterns.items():
                repertoire_text += f"### {pname}\n"
                repertoire_text += f"- **Description:** {pattern['description']}\n"
                repertoire_text += f"- **When to use:** {pattern['when']}\n"
                repertoire_text += f"- **Structure:** {pattern['structure']}\n\n"
            sections.append(
                "## Quality Pattern Repertoire\n"
                "These are the quality patterns available. Use your judgment about "
                "which to apply and where.\n"
                + repertoire_text
            )
            sections.append(
                "## Planning Decisions\n"
                "For each task, decide explicitly: what type of work is it "
                "(code, UI, data, API, infrastructure, config)? What is its risk "
                "profile (low, medium, high)? Which quality patterns should follow "
                "it, if any? Where should NorthStar checkpoints go (phase "
                "boundaries, after significant decisions)? Use break/fix loops "
                "where appropriate. Don't over-test trivial changes — think about "
                "risk.\n"
            )
        if bundle.request_routes:
            sections.append(
                "## Model Routing\n"
                "Every node MUST include route assignments:\n"
                "- route_tier: one of `fast`, `standard`, `premium`\n"
                "- model: a concrete provider:model pattern\n\n"
                "| Tier | Cost | Eligible models |\n"
                "|------|------|-----------------|\n"
                "| **Fast** (simple leaf tasks) | Free, local | `ollama:` prefixed models |\n"
                "| **Standard** (normal work) | Lower cost | `zai:glm-5.2`, `kimi-coding:kimi-for-coding` |\n"
                "| **Premium** (complex / critical) | Higher cost | `kimi-coding:k3`, `openai-codex:gpt-5.5` |\n\n"
                "Assign the cheapest tier that can do the job well. Small "
                "strongly-verified leaf tasks default to fast. Premium REQUIRES a "
                "non-empty escalation_reason explaining why cheaper tiers are "
                "insufficient. Never assign `anthropic/*` models (prohibited for "
                "workgraph-dispatched tasks). Lunaroute models only with explicit "
                "operator opt-in.\n"
            )
        # Build the output format field list
        output_fields = (
            "id, title, after, type, risk, description, pattern, "
            "max_iterations, touch, acceptance, verify"
        )
        field_docs = (
            "- id: kebab-case slug\n"
            "- title: human-readable title\n"
            "- after: list of dependency task ids\n"
            "- type: code | quality-gate | northstar-checkpoint\n"
            "- risk: low | medium | high\n"
            "- pattern: quality pattern name (if quality-gate)\n"
            "- max_iterations: integer (for fix loops)\n"
            "- touch: list of file paths\n"
            "- acceptance: list of acceptance criteria\n"
            "- verify: command to verify\n"
        )
        if bundle.request_routes:
            output_fields += ", model, route_tier, escalation_reason"
            field_docs += (
                "- model: concrete provider:model pattern (e.g. `zai:glm-5.2`)\n"
                "- route_tier: fast | standard | premium\n"
                "- escalation_reason: required when route_tier is premium\n"
            )
        sections.append(
            "## Output Format\n"
            "Respond with ONLY a JSON array of node objects using these field names:\n"
            f"{output_fields}.\n"
            + field_docs
        )
        sections.append(
            "## CRITICAL: wg-contract blocks\n"
            "Every code-type task description MUST begin with a ```wg-contract fenced "
            "block so coredrift can check it:\n"
            "````\n"
            "```wg-contract\n"
            "schema = 1\n"
            'mode = "core"\n'
            'objective = "The task title"\n'
            'non_goals = ["Things explicitly out of scope"]\n'
            'touch = ["src/file1.ts", "src/file2.ts"]\n'
            'creates = ["src/new_file.py"]\n'
            'acceptance = ["Acceptance criterion 1", "Acceptance criterion 2"]\n'
            "max_files = 15\n"
            "max_loc = 500\n"
            "```\n"
            "````\n"
            "Include the wg-contract block as the FIRST thing in the description "
            "field. Put the human-readable instructions after it.\n"
            "List files the task will CREATE under `creates` and files it will "
            "MODIFY under `touch` — grounding checks treat undeclared missing "
            "touch paths as potential errors.\n"
        )
    else:
        raise ValueError(f"Unknown bundle mode: {bundle.mode!r}")

    if bundle.extra_instructions:
        sections.append(bundle.extra_instructions)

    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Plan output parser
# ---------------------------------------------------------------------------


def parse_plan_output(raw: str) -> list[PlannedNode]:
    """Extract and parse a JSON task list from an LLM response.

    Three extraction strategies (ported from quality_planner._parse_plan_output):
    1. Direct json.loads on the whole text.
    2. Outermost ```json fence via rfind.
    3. Largest {…} brace span (and parallel [ … ] bracket span for arrays).

    Handles both ``{"tasks": [...]}`` wrappers and bare ``[...]`` arrays.
    Returns an empty list on any failure.
    """
    text = raw.strip()
    if not text:
        return []

    data: Any = None

    # 1. Direct parse
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        pass

    # 2. Outermost ```json fence
    if data is None and "```json" in text:
        start = text.index("```json") + len("```json")
        last_fence = text.rfind("```")
        if last_fence > start:
            candidate = text[start:last_fence].strip()
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # 3a. Largest { ... } brace span
    if data is None:
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace >= 0 and last_brace > first_brace:
            candidate = text[first_brace : last_brace + 1]
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # 3b. Largest [ ... ] bracket span (for bare arrays)
    if data is None:
        first_bracket = text.find("[")
        last_bracket = text.rfind("]")
        if first_bracket >= 0 and last_bracket > first_bracket:
            candidate = text[first_bracket : last_bracket + 1]
            try:
                data = json.loads(candidate)
            except json.JSONDecodeError:
                pass

    if data is None:
        return []

    # Normalize to a list of task dicts
    if isinstance(data, list):
        task_list = data
    elif isinstance(data, dict):
        task_list = data.get("tasks", [])
    else:
        return []

    nodes: list[PlannedNode] = []
    for t in task_list:
        if not isinstance(t, dict):
            continue
        nodes.append(
            PlannedNode(
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
                model=t.get("model", ""),
                route_tier=t.get("route_tier", ""),
                escalation_reason=t.get("escalation_reason", ""),
            )
        )
    return nodes


# ---------------------------------------------------------------------------
# LLM caller
# ---------------------------------------------------------------------------


def call_llm(prompt: str, model: str = "sonnet") -> str:
    """Call claude CLI in non-interactive mode and return the response text.

    This is a planner-side direct CLI invocation, NOT a workgraph-dispatched
    task. The anthropic dispatch prohibition in ModelRoutePolicy governs models
    assigned TO tasks, not the planner's own runtime.
    """
    cmd = [
        "claude",
        "-p",
        "--model", model,
        "--output-format", "json",
        "--no-session-persistence",
        "--dangerously-skip-permissions",
    ]
    # Strip env vars that trigger interactive session hooks
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CLAUDE_SESSION_ID", "CLAUDE_CONVERSATION_ID")
    }
    try:
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
            env=env,
        )
        if result.returncode != 0:
            print(
                f"warning: claude CLI exit {result.returncode}: {result.stderr[:200]}",
                file=sys.stderr,
            )
            return ""
        # --output-format json wraps response in {"result": "...", ...}
        try:
            cli_output = json.loads(result.stdout)
            if isinstance(cli_output, dict):
                return cli_output.get("result", result.stdout).strip()
        except json.JSONDecodeError:
            pass
        return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        print(f"warning: LLM call failed: {e}", file=sys.stderr)
        return ""


# ---------------------------------------------------------------------------
# Materializer
# ---------------------------------------------------------------------------


def materialize_plan(
    nodes: list[PlannedNode],
    repo_path: Path,
    *,
    desc_builder: Callable[[PlannedNode], str] | None = None,
    verify_fallback: Callable[[PlannedNode], str] | None = None,
    tag_builder: Callable[[PlannedNode], list[str]] | None = None,
    route_models: dict[str, str] | None = None,
    escalation_reasons: dict[str, str] | None = None,
    policy: ModelRoutePolicy = DEFAULT_MODEL_ROUTE_POLICY,
    runner: Callable[..., Any] | None = None,
    post_commands: list[list[str]] | None = None,
) -> int:
    """Write PlannedNode objects to the workgraph via ``wg add``.

    Generalizes the wg-add loop from quality_planner.plan_from_spec.

    If *route_models* is None, it is derived from nodes that carry a non-empty
    ``.model`` field.

    Route validation asymmetry:
      - **Policy breaches** (prohibited / conditional providers) **fail closed**:
        the node is skipped entirely and never materialized.
      - **Missing escalation paperwork** (premium tier without a recorded
        reason) **degrades to default routing**: the node is still materialized
        but its ``--model`` pin is stripped so it inherits the project default
        profile.  A loud stderr warning is printed.

    - Returns the count of successfully added tasks.
    """
    if runner is None:
        runner = subprocess.run

    # Derive route_models from nodes if not provided
    if route_models is None:
        route_models = {n.id: n.model for n in nodes if n.model}

    # Validate routes and classify violations
    skip_ids: set[str] = set()
    strip_pin_ids: set[str] = set()
    if route_models:
        for v in validate_model_routes(
            route_models,
            policy=policy,
            escalation_reasons=escalation_reasons,
        ):
            if v.kind in ("prohibited", "conditional"):
                print(
                    f"warning: skipping node '{v.node_id}' — route violation: {v.reason}",
                    file=sys.stderr,
                )
                skip_ids.add(v.node_id)
            elif v.kind == "missing-escalation-reason":
                print(
                    f"warning: route pin stripped for node '{v.node_id}' — {v.reason}",
                    file=sys.stderr,
                )
                strip_pin_ids.add(v.node_id)

    added_count = 0
    for node in nodes:
        if node.id in skip_ids:
            continue

        cmd: list[str] = ["wg", "add", node.title, "--id", node.id]

        for dep in node.after:
            cmd.extend(["--blocked-by", dep])

        # Model routing: add --model unless pin was stripped
        if node.id not in strip_pin_ids and route_models:
            model = route_models.get(node.id, "")
            if model:
                cmd.extend(["--model", model])

        desc = desc_builder(node) if desc_builder else (node.description or None)
        if desc:
            cmd.extend(["-d", desc])

        verify = node.verify or (verify_fallback(node) if verify_fallback else "")
        if verify:
            cmd.extend(["--verify", verify])

        # Structural fix loops: wire max_iterations to --max-iterations so
        # cycles are real graph cycles, not prose.
        if node.max_iterations is not None:
            cmd.extend(["--max-iterations", str(node.max_iterations)])

        if tag_builder:
            for tag in tag_builder(node):
                cmd.extend(["--tag", tag])

        try:
            result = runner(
                cmd,
                cwd=str(repo_path),
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                added_count += 1
            else:
                stderr = (result.stderr or "").strip()
                if stderr:
                    print(
                        f"warning: wg add for '{node.id}': {stderr}",
                        file=sys.stderr,
                    )
        except subprocess.TimeoutExpired:
            print(
                f"warning: wg add timed out for '{node.id}' "
                f"(workgraph daemon may be unresponsive)",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"warning: wg add failed for {node.id}: {e}", file=sys.stderr)

    # Post-materialize commands (e.g. coredrift ensure-contracts).
    # Failures are logged but do not affect the added count.
    if post_commands:
        for cmd in post_commands:
            try:
                result = runner(
                    cmd,
                    cwd=str(repo_path),
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    stderr = (result.stderr or "").strip()
                    print(
                        f"warning: post-command failed ({' '.join(cmd)}): {stderr}",
                        file=sys.stderr,
                    )
            except Exception as e:
                print(f"warning: post-command error ({' '.join(cmd)}): {e}", file=sys.stderr)

    return added_count


# ---------------------------------------------------------------------------
# Review gate insertion
# ---------------------------------------------------------------------------


def insert_review_gates(
    nodes: list[PlannedNode],
    *,
    gate_prefix: str = "review",
    gate_model: str = "",
) -> list[PlannedNode]:
    """Insert a roborev review gate after every code-type node.

    For each node with ``task_type == "code"`` a gate node is created:
      - id: ``f"{gate_prefix}-{node.id}"``
      - title: ``f"Review: {node.title}"``
      - task_type: ``"review"``
      - after: ``[node.id]``

    Every *other* node whose ``after`` list references the source node's id
    has that entry replaced with the gate's id, so dependents now wait for
    the review.  The gate node itself is excluded from rewiring (its own
    ``after`` still points at the source).

    Returns the full list: original nodes (mutated where rewired) plus gate
    nodes appended.
    """
    gate_map: dict[str, str] = {}
    gates: list[PlannedNode] = []

    for node in nodes:
        if node.task_type == "code":
            gate_id = f"{gate_prefix}-{node.id}"
            gate_map[node.id] = gate_id
            gate = PlannedNode(
                id=gate_id,
                title=f"Review: {node.title}",
                task_type="review",
                after=[node.id],
                model=gate_model,
                description=(
                    f"Run a roborev review of the implementation task "
                    f"'{node.id}' changes against its acceptance criteria. "
                    f"Address or respond to every finding. Close the roborev "
                    f"job. The gate fails open when no findings exist."
                ),
            )
            gates.append(gate)

    # Rewire: replace dep references to source ids with gate ids
    for node in nodes:
        node.after = [gate_map.get(dep, dep) for dep in node.after]

    return nodes + gates


# ---------------------------------------------------------------------------
# Agency fences
# ---------------------------------------------------------------------------


def agency_fence(
    profile: str,
    *,
    preferred_runtime: str = "agency",
    fallback_runtime: str = "codexd",
) -> str:
    """Return a fenced ````agencydrift```` block for embedding in task descriptions."""
    return (
        "```agencydrift\n"
        "schema = 1\n"
        f'profile = "{profile}"\n'
        f'preferred_runtime = "{preferred_runtime}"\n'
        f'fallback_runtime = "{fallback_runtime}"\n'
        "```"
    )


def apply_agency_fences(
    nodes: list[PlannedNode],
    profile_map: dict[str, str],
) -> list[PlannedNode]:
    """Append an agency fence to each named node's description.

    Idempotent: skips nodes whose description already contains
    ``"agencydrift"``.  Nodes absent from *profile_map* are untouched.

    Returns the (mutated) list.
    """
    for node in nodes:
        if node.id in profile_map and "agencydrift" not in (node.description or ""):
            fence = agency_fence(profile_map[node.id])
            node.description = (node.description or "") + "\n\n" + fence
    return nodes
