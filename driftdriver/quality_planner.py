# ABOUTME: Speedrift Quality Planner — structures workgraphs with quality intelligence.
# ABOUTME: Reads specs, applies quality patterns from repertoire, produces task graphs.
from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from driftdriver.planner_core import (
    BUILTIN_PATTERNS,
    PlannedNode,
    PolicyBundle,
    build_decompose_prompt as _core_build_decompose_prompt,
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
    evidence: str = "",
) -> str:
    """Build the LLM prompt that instructs the planner to produce a quality-aware task graph.

    Thin adapter over planner_core.build_decompose_prompt. Assembles a
    PolicyBundle with quality patterns, granularity bar, route requests, and
    any drift-policy summary, then delegates to the canonical builder.

    When *evidence* is non-empty it is appended as a "## Repository Evidence
    (verified on disk)" section via the bundle's extra_instructions so the
    planner grounds its plan in actual repo state.
    """
    extra_parts: list[str] = []
    if drift_policy_summary:
        extra_parts.append(f"## Drift Policy Summary\n{drift_policy_summary}")
    if evidence:
        extra_parts.append(f"## Repository Evidence (verified on disk)\n{evidence}")
    extra = "\n\n".join(extra_parts)
    bundle = PolicyBundle(
        name="quality-spec",
        mode="emit-json",
        patterns=repertoire,
        granularity_bar=True,
        request_routes=True,
        extra_instructions=extra,
    )
    return _core_build_decompose_prompt(
        "", spec_content=spec_content, north_star=north_star, bundle=bundle,
    )


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
    grounding: bool = True,
) -> PlannerOutput:
    """Read a spec file and produce a quality-aware workgraph task plan.

    In dry_run mode, prints a summary and returns an empty PlannerOutput
    without making any LLM calls.

    When *grounding* is True (default), existdrift runs both pre-plan
    (evidence bundle injected into the prompt) and post-materialize
    (advisory grounding scan printed to stderr). Planning never hard-fails
    if grounding fails — it degrades gracefully.
    """
    spec_content = spec_path.read_text(encoding="utf-8")
    north_star = _read_north_star(repo_path)
    repertoire = load_repertoire()

    # --- Pre-plan grounding: inject verified repo evidence into the prompt ---
    evidence = ""
    if grounding:
        try:
            from driftdriver.existdrift import build_evidence_bundle

            # Use the spec's first heading + first paragraph as hint text.
            hint = spec_content[:500]
            evidence = build_evidence_bundle(repo_path, hint_text=hint)
        except Exception as exc:
            _log.warning("existdrift evidence bundle failed: %s", exc)
            evidence = ""

    prompt = build_planner_prompt(
        spec_content=spec_content,
        north_star=north_star,
        repertoire=repertoire,
        evidence=evidence,
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
        post_commands=[["./.workgraph/coredrift", "ensure-contracts", "--apply"]],
    )

    # --- Post-materialize grounding scan (advisory, never blocks) ---
    if grounding:
        try:
            from driftdriver.existdrift import scan_grounding

            report = scan_grounding(repo_path)
            summary = report.get("summary", {})
            total = summary.get("findings_total", 0)
            if total > 0:
                print(
                    f"[existdrift] {total} grounding findings "
                    f"(high={summary.get('high', 0)}, "
                    f"medium={summary.get('medium', 0)}, "
                    f"low={summary.get('low', 0)}, "
                    f"info={summary.get('info', 0)})",
                    file=sys.stderr,
                )
                for finding in (report.get("top_findings") or [])[:3]:
                    rec = str(finding.get("recommendation") or "")
                    print(
                        f"  - [{finding.get('severity', '?')}] "
                        f"{finding.get('title', '?')}: {rec}",
                        file=sys.stderr,
                    )
            else:
                print("[existdrift] no grounding findings", file=sys.stderr)
        except Exception as exc:
            _log.warning("existdrift post-materialize scan failed: %s", exc)

    return output
