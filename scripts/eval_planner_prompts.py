#!/usr/bin/env python3
"""Repeatable eval harness comparing decomposition prompt surfaces.

Runs 3 prompt surfaces x 2 real goals x 2 runs through zai/glm-5.2 via
headless pi, parses with driftdriver.planner_core, and computes structural
metrics (DAG validity, verify/wg-contract/touch coverage, pattern use,
run-to-run similarity).

Run via: uv run python scripts/eval_planner_prompts.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from driftdriver.planner_core import (
    BUNDLE_DECOMPOSE_CLI,
    BUNDLE_QUALITY_SPEC,
    BUILTIN_PATTERNS,
    build_decompose_prompt,
    parse_plan_output,
)

GOALS = {
    "g1-acceptance-gate": (
        "Enforce acceptance_criteria at speedrift task completion: a workgraph "
        "task may only be marked Done when its acceptance_criteria evaluate true "
        "against produced artifacts. Add a per-repo degrade ceiling so waived "
        "gates do not accumulate silently across many tasks, with a CLI to "
        "inspect current degrade counts."
    ),
    "g2-heartbeat": (
        "Add a GET /api/agent/heartbeat endpoint to the four paia agent services "
        "(samantha, derek, ingrid, caroline) built on the shared paia-agent-runtime "
        "library. Include a shared schema doc, per-agent domain-specific event "
        "scanning, and a smoke test that verifies all four endpoints."
    ),
}

# Spec-shaped inputs for the live quality path (s4).
SPECS = {
    "g1-acceptance-gate": (
        "# Spec: acceptance-criteria completion gate\n\n"
        "Workgraph tasks may only be marked Done when their acceptance_criteria "
        "evaluate true against produced artifacts. Per-repo degrade ceiling with "
        "CLI inspection of degrade counts.",
        "Drift gates are deterministic at the task boundary; advisory findings "
        "must not silently accumulate.",
    ),
    "g2-heartbeat": (
        "# Spec: agent heartbeat endpoints\n\n"
        "GET /api/agent/heartbeat for samantha, derek, ingrid, caroline on the "
        "shared paia-agent-runtime. Shared schema doc, per-agent domain event "
        "scanning, smoke test over all four.",
        "Agents expose cheap liveness and domain signal without cloud calls.",
    ),
}

# S1: the original decompose.py thin prompt (pre-consolidation), verbatim.
THIN_TEMPLATE = (
    "Decompose this goal into 3-8 concrete, dependency-ordered tasks "
    "for a workgraph. Return JSON array of objects with id, title, "
    "description, after (list of dependency ids).\n\n"
    "Goal: {goal}\n\nContext: \n"
)

def _s4_prompt(goal_name: str) -> str:
    from driftdriver.quality_planner import build_planner_prompt, load_repertoire

    spec, north_star = SPECS[goal_name]
    return build_planner_prompt(
        spec_content=spec, north_star=north_star, repertoire=load_repertoire()
    )


SURFACES = {
    "s1-thin": lambda name: THIN_TEMPLATE.format(goal=GOALS[name]),
    "s2-canonical": lambda name: build_decompose_prompt(GOALS[name], bundle=BUNDLE_DECOMPOSE_CLI),
    "s3-patterned": lambda name: build_decompose_prompt(GOALS[name], bundle=BUNDLE_QUALITY_SPEC),
    "s4-live-quality": _s4_prompt,
}

RUNS_PER_CELL = 2


def call_glm(prompt: str) -> str:
    try:
        result = subprocess.run(
            ["pi", "--print", "--model", "zai/glm-5.2", prompt],
            capture_output=True, text=True, timeout=420,
        )
        if result.returncode != 0:
            return f"__ERROR__: exit {result.returncode}: {result.stderr[:200]}"
        return result.stdout
    except Exception as e:
        return f"__ERROR__: {e}"


def has_cycle(nodes: dict[str, dict]) -> bool:
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n: WHITE for n in nodes}

    def visit(n: str) -> bool:
        color[n] = GRAY
        for dep in nodes[n]["after"]:
            if dep not in nodes:
                continue
            if color[dep] == GRAY:
                return True
            if color[dep] == WHITE and visit(dep):
                return True
        color[n] = BLACK
        return False

    return any(color[n] == WHITE and visit(n) for n in nodes)


def metrics(raw: str) -> dict:
    m: dict = {"parse_ok": False}
    if raw.startswith("__ERROR__"):
        m["error"] = raw[:120]
        return m
    parsed = parse_plan_output(raw)
    if not parsed:
        return m
    m["parse_ok"] = True
    m["nodes"] = len(parsed)
    ids = [n.id for n in parsed]
    m["unique_ids"] = len(set(ids)) == len(ids)
    node_map = {n.id: {"after": n.after} for n in parsed}
    m["dangling_edges"] = sum(1 for n in parsed for d in n.after if d not in node_map)
    m["has_cycle"] = has_cycle(node_map)
    m["flat_no_edges"] = all(not n.after for n in parsed)
    m["verify_coverage"] = round(sum(1 for n in parsed if n.verify) / len(parsed), 2)
    m["wgcontract_coverage"] = round(
        sum(1 for n in parsed if "wg-contract" in n.description) / len(parsed), 2
    )
    m["touch_coverage"] = round(sum(1 for n in parsed if n.touch) / len(parsed), 2)
    # touch overlap between siblings that could run in parallel (no path between them)
    overlaps = 0
    for i, a in enumerate(parsed):
        for b in parsed[i + 1:]:
            if set(a.touch) & set(b.touch):
                overlaps += 1
    m["touch_overlap_pairs"] = overlaps
    # pattern reproduction: nodes tagged with a known pattern
    m["pattern_nodes"] = sum(1 for n in parsed if n.pattern in BUILTIN_PATTERNS)
    # route assignments: nodes carrying a model or route_tier, and premium discipline
    m["route_coverage"] = round(
        sum(1 for n in parsed if n.model or n.route_tier) / len(parsed), 2
    )
    premium = [n for n in parsed if n.route_tier == "premium"]
    m["premium_nodes"] = len(premium)
    m["premium_with_reason"] = sum(1 for n in premium if n.escalation_reason)
    m["ids"] = sorted(ids)
    return m


def jaccard(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    return round(len(sa & sb) / len(sa | sb), 2) if sa or sb else 1.0


def run_cell(args):
    goal_name, surface_name, run_idx = args
    prompt = SURFACES[surface_name](goal_name)
    raw = call_glm(prompt)
    return goal_name, surface_name, run_idx, metrics(raw), len(prompt)


def main() -> None:
    cells = [
        (g, s, r)
        for g in GOALS
        for s in SURFACES
        for r in range(RUNS_PER_CELL)
    ]
    results: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for goal_name, surface_name, run_idx, m, plen in pool.map(run_cell, cells):
            results.setdefault(goal_name, {}).setdefault(surface_name, []).append(m)
            status = "ok" if m.get("parse_ok") else m.get("error", "parse-fail")
            print(f"  [{goal_name} / {surface_name} / run{run_idx}] {status}", flush=True)

    print("\n=== STRUCTURAL METRICS (mean over runs) ===")
    header = f"{'goal':<20}{'surface':<16}{'parse':<6}{'nodes':<6}{'dangle':<7}{'cycle':<6}{'flat':<5}{'verify':<7}{'wg-c':<6}{'touch':<6}{'overlap':<8}{'pat':<4}{'route':<6}{'prem':<5}{'run-sim':<7}"
    print(header)
    for g, surfaces in results.items():
        for s, runs in surfaces.items():
            ok_runs = [r for r in runs if r.get("parse_ok")]
            if not ok_runs:
                print(f"{g:<20}{s:<16}0/2 parse failures")
                continue
            n = len(ok_runs)
            avg = lambda k: round(sum(r[k] for r in ok_runs) / n, 2)
            sim = jaccard(runs[0].get("ids", []), runs[1].get("ids", [])) if len(runs) == 2 else "-"
            print(
                f"{g:<20}{s:<16}{n}/{len(runs)}  {avg('nodes'):<6}{avg('dangling_edges'):<7}"
                f"{avg('has_cycle')!s:<6}{avg('flat_no_edges')!s:<5}{avg('verify_coverage'):<7}"
                f"{avg('wgcontract_coverage'):<6}{avg('touch_coverage'):<6}{avg('touch_overlap_pairs'):<8}"
                f"{avg('pattern_nodes'):<4}{avg('route_coverage'):<6}{avg('premium_nodes'):<5}{sim!s:<7}"
            )

    out = Path("/tmp/planner_eval_results.json")
    out.write_text(json.dumps(results, indent=2, default=str))
    print(f"\nfull results: {out}")


if __name__ == "__main__":
    main()
