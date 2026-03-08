# Attractor Loop Design — Three-Layer Convergence Engine

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Autonomous convergence engine that drives repos toward a declared target state (attractor) through iterative diagnose→plan→execute→re-diagnose cycles, with cross-repo dependency awareness and circuit breakers.

**Architecture:** Three layers — static bundles (reusable task graph fragments), a model-mediated planner (selects and composes bundles to close the gap between current state and attractor), and a reasoning loop (orchestrates passes, detects convergence/plateau, escalates). Triggered by factorydrift tick. Processes repos in ecosystem dependency order.

**Tech Stack:** Python 3.10+, TOML (bundle/attractor definitions), existing directive/ExecutorShim infrastructure, existing lane infrastructure for diagnosis.

---

## Motivation

The current workflow for driving a repo to quality is manual and repetitive:

1. Run drift lanes, read findings
2. Mentally plan which fixes to apply and in what order
3. Create workgraph tasks for the fixes
4. Execute the tasks (or dispatch workers)
5. Re-run drift lanes to check progress
6. Repeat until satisfied or give up

Every piece of this exists in driftdriver — lanes diagnose, directives create tasks, workers execute, outcomes track results. But nobody orchestrates the loop. The human is the planner and convergence detector. The attractor loop replaces the human in that role for known patterns, and escalates to the human for novel situations.

This is not only remediation. The same loop handles onboarding, hardening, migration, and goal execution — any scenario where "drive toward a target state" is the objective.

## Layer A: Bundles

A bundle is a reusable task graph fragment mapped to finding-kinds. Stored as TOML in `driftdriver/bundles/`.

### Bundle schema

```toml
# bundles/missing-intervening-tests.toml
[bundle]
id = "missing-intervening-tests"
finding_kinds = ["missing-intervening-tests", "missing-test-gate"]
description = "Add integration test gate downstream of implementation task"

[[tasks]]
id_template = "{finding_id}-write-test"
title_template = "Write integration test for {task_title}"
description_template = "Add test coverage verifying {evidence}"
tags = ["drift", "attractor", "testing"]

[[tasks]]
id_template = "{finding_id}-verify-gate"
title_template = "Verify test gate passes for {task_title}"
description_template = "Run test suite, confirm coverage threshold met"
tags = ["drift", "attractor", "verification"]
after = ["{finding_id}-write-test"]
verify = "pytest tests/ -x -q"
```

### Bundle fields

- **`finding_kinds`** — which lane finding categories trigger this bundle
- **`tasks`** — parameterized task templates with:
  - `id_template` — deterministic ID from finding context (enables dedup)
  - `title_template` / `description_template` — filled with `{finding_id}`, `{task_title}`, `{file}`, `{evidence}`, `{repo_name}`
  - `tags` — always includes `["drift", "attractor"]`
  - `after` — dependency edges within the bundle (references other task id_templates)
  - `verify` — optional verification command (hard gate on the final task)

### Bundle effectiveness tracking

Each time a bundle is applied:
- If the next pass shows the target finding resolved → record `bundle_resolved`
- If the finding persists → record `bundle_ineffective`

Per-bundle resolution rates accumulate in the outcome ledger (extension: `bundle_id` field on outcome records). The planner deprioritizes bundles with consistently low resolution rates and escalates those findings instead of wasting passes.

### Built-in bundles (ship with driftdriver)

| Bundle ID | Finding kinds | Tasks |
|-----------|--------------|-------|
| `missing-intervening-tests` | plandrift test gate findings | write test → verify gate |
| `missing-failure-loopback` | plandrift loopback findings | add recovery path → verify |
| `scope-drift` | coredrift scope findings | update contract → re-check |
| `spec-not-updated` | specdrift findings | update spec → verify alignment |
| `dependency-outdated` | depsdrift findings | update dep → run tests → verify |
| `security-finding` | secdrift findings | apply fix → security re-scan |
| `scaffold-workgraph` | no `.workgraph/` detected | wg init → driftdriver install → ensure contracts |
| `scaffold-tests` | no test directory detected | create test dir → write smoke test → verify |

Custom bundles can be added to `driftdriver/bundles/` or to a repo's `.workgraph/.driftdriver/bundles/` for repo-specific patterns.

## Layer B: Attractor Planner

One model call per planning pass. Pure function: current state + target state + bundles → convergence plan.

### Attractors

An attractor defines a target state for a repo. Stored as TOML in `driftdriver/attractors/`.

```toml
# attractors/production-ready.toml
[attractor]
id = "production-ready"
description = "Repo is test-covered, drift-clean, deps current, security scanned"

[[criteria]]
lane = "coredrift"
max_actionable_findings = 0

[[criteria]]
lane = "plandrift"
max_actionable_findings = 0
require = ["test-gates", "failure-loopbacks"]

[[criteria]]
lane = "depsdrift"
max_actionable_findings = 0

[[criteria]]
lane = "secdrift"
max_actionable_findings = 0

[[criteria]]
custom = "test_coverage_above"
threshold = 80
```

#### Attractor inheritance

Attractors compose via `extends`:

```toml
# attractors/hardened.toml
[attractor]
id = "hardened"
extends = "production-ready"
description = "Production-ready plus strict security and e2e coverage"

[[criteria]]
lane = "secdrift"
max_critical = 0
require_pentest_pass = true

[[criteria]]
custom = "e2e_coverage_above"
threshold = 90
```

`hardened` inherits all criteria from `production-ready` and adds its own. Natural progression: `onboarded` → `production-ready` → `hardened`.

#### Declaring a repo's attractor

In `drift-policy.toml`:

```toml
[attractor]
target = "production-ready"
```

If not declared, defaults to `onboarded` (minimal: has workgraph, has drift lanes, has at least one task).

### Planner input

| Input | Source |
|-------|--------|
| Current lane findings | `driftdriver check --json` (all lanes) |
| Current workgraph state | `graph.jsonl` (tasks, deps, statuses) |
| Attractor criteria | `drift-policy.toml` → attractor definition |
| Available bundles | `driftdriver/bundles/` + `.workgraph/.driftdriver/bundles/` |
| Outcome history | `drift-outcomes.jsonl` (finding resolution rates, bundle effectiveness) |
| Ecosystem dep graph | ecosystem hub snapshot (which repos depend on this one) |
| Previous passes this run | `current-run.json` (what was already tried) |

### Planner output

```python
@dataclass
class ConvergencePlan:
    attractor: str
    repo: str
    pass_number: int
    bundle_instances: list[BundleInstance]  # parameterized bundles
    cross_bundle_edges: list[tuple[str, str]]  # inter-bundle task deps
    budget_cost: int  # estimated CREATE_TASK directive count
    escalations: list[EscalationRecord]  # findings with no/low-confidence bundle match
    skipped: list[str]  # findings skipped due to low bundle effectiveness
```

### What the model does

1. **Selects** which bundles apply to which findings (dedup: multiple findings may map to one bundle)
2. **Parameterizes** each bundle with repo-specific context (file paths, task titles, evidence)
3. **Orders** instantiated bundles — if bundle X fixes the spec and bundle Y fixes implementation that depends on the spec, X goes first
4. **Annotates confidence** — "high" for exact finding-kind match, "low" for fuzzy. Low-confidence → escalation
5. **Skips** findings matching bundles with historically low resolution rates (from effectiveness tracking)

### What the model does NOT do

- Invent task graph structures (bundles define structure)
- Execute anything (loop handles execution)
- Override authority budgets (guard gates every directive)
- Decide when to stop (loop handles convergence)

## Layer C: Attractor Loop

Orchestrates the full cycle. Triggered by factorydrift tick.

### Loop lifecycle

```
factorydrift tick
  → load ecosystem dependency graph from hub
  → topological sort repos (leaves first, dependents after)
  → for each repo with a declared attractor:
      pass_number = 0
      previous_finding_count = infinity

      DIAGNOSE:
        → run all lanes for this repo
        → collect findings
        → evaluate attractor criteria
        → if all criteria met: repo is converged, done
        → if no actionable findings but criteria not met: escalate (custom criteria gap), done

      PLAN:
        → call Layer B planner (model call)
        → if plan is escalations-only: record escalation, done
        → if budget_cost exceeds remaining cycle budget: queue for next tick, done

      EXECUTE:
        → emit CREATE_TASK directives for all bundle instances
        → dispatch workers for ready tasks
        → wait for completion (or timeout)

      RE-DIAGNOSE:
        → run all lanes again
        → count actionable findings

      CONVERGE CHECK:
        → if actionable_findings == 0 and criteria met: converged, done
        → if actionable_findings >= previous_finding_count: plateau detected
            → escalate remaining findings, done
        → if pass_number >= max_passes: escalate, done
        → else: previous_finding_count = actionable_findings
                pass_number += 1
                goto PLAN
```

### Cross-repo sequencing

The ecosystem hub already maintains the dependency graph between repos. The loop processes repos in topological order:

1. Repos with no dependencies run first (leaves)
2. When repo A converges, repos depending on A become eligible
3. If repo A plateaus, dependents still run — they may have independent findings. But cross-repo findings (like "upstream dependency outdated") won't resolve until A is stable.

Example from today: SDK converges → lane repos become eligible → their depsdrift findings ("SDK outdated") auto-resolve → fewer passes needed → faster convergence.

### Circuit breakers

| Breaker | Default | Effect |
|---------|---------|--------|
| Max passes per repo per cycle | 3 | Escalate remaining findings |
| Max total tasks per cycle | 30 | Stop emitting, queue rest for next tick |
| Max dispatches per cycle | 10 | Queue remaining, resume next tick |
| Plateau detection | 2 consecutive passes, same or higher finding count | Escalate |
| Per-actor authority budget | Existing limits in drift_task_guard | Escalate |
| Timeout per pass | 30 minutes | Fail pass, escalate |

Circuit breaker defaults are intentionally conservative. Loosen as trust builds:

```toml
# drift-policy.toml
[attractor.circuit_breakers]
max_passes = 3
max_tasks_per_cycle = 30
max_dispatches_per_cycle = 10
plateau_threshold = 2
pass_timeout_seconds = 1800
```

### Escalation

When the loop can't converge, it produces an escalation record:

```python
@dataclass
class EscalationRecord:
    repo: str
    attractor: str
    reason: str  # "plateau", "budget_exhausted", "no_matching_bundle", "timeout", "low_confidence"
    remaining_findings: list[LaneFinding]
    passes_completed: int
    bundles_applied: list[str]
    bundle_outcomes: dict[str, str]  # bundle_id → "resolved" | "ineffective"
    suggested_action: str  # human-readable next step
    suggested_prompt: str  # copy-pasteable Claude prompt for manual intervention
```

Escalations write to `escalations.jsonl` and surface in the ecosystem hub dashboard action queue.

### State persistence

Each attractor run writes to `.workgraph/service/attractor/`:
- `current-run.json` — active run state (pass number, findings snapshots, plans, outcomes)
- `history/<timestamp>.json` — completed runs with full trace
- Central register mirror when `reporting.central_repo` is configured

### Autonomy gating

The attractor loop respects existing speedriftd control modes:
- `observe`/`manual`: no attractor loop runs
- `supervise`: attractor loop runs but escalates all executions for approval
- `autonomous`: full autonomous operation with circuit breakers

## Data Types

```python
@dataclass
class AttractorCriterion:
    lane: str = ""  # empty for custom criteria
    custom: str = ""  # custom check function name
    max_actionable_findings: int = 0
    require: list[str] = field(default_factory=list)
    threshold: float = 0.0
    # additional fields for lane-specific requirements

@dataclass
class Attractor:
    id: str
    description: str
    extends: str = ""  # parent attractor ID
    criteria: list[AttractorCriterion] = field(default_factory=list)

@dataclass
class TaskTemplate:
    id_template: str
    title_template: str
    description_template: str = ""
    tags: list[str] = field(default_factory=list)
    after: list[str] = field(default_factory=list)
    verify: str = ""

@dataclass
class Bundle:
    id: str
    finding_kinds: list[str]
    description: str
    tasks: list[TaskTemplate]

@dataclass
class BundleInstance:
    bundle_id: str
    finding_id: str
    tasks: list[dict]  # parameterized task dicts ready for CREATE_TASK directives
    confidence: str  # "high" or "low"

@dataclass
class ConvergencePlan:
    attractor: str
    repo: str
    pass_number: int
    bundle_instances: list[BundleInstance]
    cross_bundle_edges: list[tuple[str, str]]
    budget_cost: int
    escalations: list[EscalationRecord]
    skipped: list[str]

@dataclass
class PassResult:
    pass_number: int
    findings_before: int
    findings_after: int
    plan: ConvergencePlan
    bundles_applied: list[str]
    bundle_outcomes: dict[str, str]
    duration_seconds: float

@dataclass
class AttractorRun:
    repo: str
    attractor: str
    started_at: str
    passes: list[PassResult]
    status: str  # "converged", "plateau", "escalated", "budget_exhausted", "timeout", "queued"
    remaining_findings: list[dict]

@dataclass
class EscalationRecord:
    repo: str
    attractor: str
    reason: str
    remaining_findings: list[dict]
    passes_completed: int
    bundles_applied: list[str]
    bundle_outcomes: dict[str, str]
    suggested_action: str
    suggested_prompt: str
```

## Integration with existing systems

| Existing system | Role in attractor loop | Changes needed |
|----------------|----------------------|----------------|
| factorydrift | Triggers the loop each tick | Add attractor loop call in factory cycle |
| All drift lanes | Finding sources (diagnose step) | None — lanes produce findings as today |
| drift_task_guard | Gates every CREATE_TASK directive | None — authority budgets enforced as today |
| directives/ExecutorShim | Executes all actions | None — directives flow through existing path |
| outcome.py | Records pass results, feeds back to planner | Extend with `bundle_id` field |
| ecosystem hub | Provides cross-repo dep graph for ordering | None — hub already has dep graph |
| drift-policy.toml | Declares attractor target + circuit breaker config | New `[attractor]` section |
| speedriftd | Mode gates loop execution | None — existing mode checks apply |

## What's NOT in scope

- New directive actions (existing 14 are sufficient)
- Changes to lane code (lanes produce findings, unchanged)
- Changes to ExecutorShim (it executes directives, unchanged)
- UI changes to ecosystem hub (escalations already surface in action queue)
- Custom criteria evaluation functions (ship `test_coverage_above` as the only custom check initially; extensible later)

## New modules

| Module | Responsibility |
|--------|---------------|
| `driftdriver/bundles.py` | Load, validate, parameterize bundle TOML files |
| `driftdriver/attractors.py` | Load attractor definitions, resolve inheritance, evaluate criteria |
| `driftdriver/attractor_planner.py` | Model-mediated planning: findings + attractor gap → convergence plan |
| `driftdriver/attractor_loop.py` | Orchestrate diagnose→plan→execute→re-diagnose→converge cycle |
| `driftdriver/bundles/*.toml` | Built-in bundle definitions |
| `driftdriver/attractors/*.toml` | Built-in attractor definitions |
