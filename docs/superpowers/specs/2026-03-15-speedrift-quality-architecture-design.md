# Speedrift Quality Architecture — Design

**Date:** 2026-03-15
**Status:** Draft
**Scope:** Driftdriver ecosystem — Bridge, evolverdrift, NorthStarDrift v2, Planner
**Repo:** driftdriver

---

## 1. Problem Statement

Speedrift has drift lanes that judge completed work and a factory brain that monitors ecosystem health, but three critical gaps remain:

1. **Drift findings are dead ends.** Lanes produce findings, humans read them, maybe someone acts. The findings never feed back into the system that produced the work. WG's evolver has a powerful self-improvement loop (1,976 evaluations, 8 strategies, fan-out/fan-in), but it only sees its own internal evaluations — it's blind to Speedrift's quality judgments.

2. **NorthStarDrift checks operational health, not strategic alignment.** It scores continuity, autonomy, coordination — all measures of "is the factory running?" It doesn't ask "is the factory building the right thing?" The human manually checks whether work advances the North Star. That judgment should be in the system.

3. **No quality-aware planning.** The human manually instructs agents to add e2e test loops, UX eval gates, break/fix cycles, and directional checkpoints into every workgraph. This planning intelligence — both tactical (what quality gates does this work need?) and strategic (are we still heading toward the North Star?) — lives in the human's head, not in the system.

---

## 2. Architecture Overview

Four components form a closed loop:

```
Spec/brainstorm complete
        |
   +-----------+
   |  PLANNER   |  Structures workgraph with quality patterns
   +-----+-----+  (tactical gates + strategic checkpoints)
         |
   WG dispatches and agents execute
         |
   +---------------------+
   |  DRIFT LANES         |  Judge completed work
   |  (including NorthStar |  (operational + strategic alignment)
   |   v2 at checkpoints)  |
   +-----+---------------+
         |
   +-----------+
   |  BRIDGE    |  Writes findings as WG evaluations
   +-----+-----+  (we own the integration, zero Erik work)
         |
   WG Evolver mutates agents
         |
   +---------------+
   |  EVOLVERDRIFT   |  Monitors evolver output
   +-----+---------+  (liveness, impact, regression)
         |
   Better agents --> fewer drift findings --> cycle
```

### Boundary Principle

Speedrift never mutates agent identity (that's WG/evolver). WG never makes quality judgments (that's Speedrift). The bridge is the handoff point — we write, the evolver reads. evolverdrift watches what comes out the other side.

This boundary is established in the existing `2026-03-07-speedrift-wg-boundary-design.md` and extended here. The key addition: Speedrift now feeds evaluations *into* WG's agency system and monitors the evolver's *output*. Neither crosses the boundary — evaluations are data files, not execution commands. Monitoring is read-only.

### Strategic Context: Speedrift vs Workgraph

**WG = "What gets done and who does it"** — execution engine (Erik's domain):
- Task graphs, dependencies, agent spawning, coordination, messaging
- Agency system: roles, components, evaluations, evolution
- The evolver: self-improving agent capabilities via fan-out/fan-in pipeline

**Speedrift = "Is the work good, and is it staying on track"** — opinionated judgment engine (our domain):
- Drift lanes: pluggable, domain-specific quality opinions
- Factory brain: ecosystem-wide health monitoring + healing
- Attractor loop: convergence toward declared target states
- Planner: quality-intelligent graph structuring
- Continuation intent + decision queue: human escalation

WG's scope will grow (and should — better execution infrastructure benefits everyone). Speedrift's moat is domain-specific judgment that WG will never have opinions about: security posture, UX model compliance, mobile-first planning, strategic alignment. Each new drift lane deepens the moat.

---

## 3. Component: Drift-to-Evaluation Bridge

### Purpose

Translate Speedrift drift lane findings into WG evaluation records, written directly into `.workgraph/agency/evaluations/`. The evolver consumes them automatically on its next run — no API, no registration, no changes to WG.

### How It Works

After every drift check cycle (per-repo), the bridge runs:

**Step 1 — Collect attributable findings.** Not every finding maps to an agent. The bridge only creates evaluations for findings that can be attributed to a specific agent's work on a specific task. Attribution chain:

```
LaneFinding (has file path or task reference in tags)
    --> task_id (from finding tags, or inferred from file path)
    --> assignment YAML (.workgraph/agency/assignments/{task_id}.yaml)
    --> agent_id / composition_id / role_id
```

Findings that can't be attributed (e.g., "repo has no North Star", "graph has orphan deps") remain as drift findings for human review or factory brain action. They don't become evaluations.

**Step 2 — Map findings to evaluation dimensions.** Each drift lane maps to WG evaluation dimensions:

| Lane | Primary Dimension | Secondary |
|------|------------------|-----------|
| coredrift | `correctness`, `completeness` | `blocking_impact` |
| qadrift | `style_adherence`, `correctness` | `efficiency` |
| plandrift | `completeness`, `downstream_usability` | `coordination_overhead` |
| secdrift | `correctness` | `blocking_impact` |
| northstardrift v2 | `strategic_alignment` (new) | `downstream_usability` |
| uxdrift (external) | `style_adherence` | `downstream_usability` |
| yagnidrift (external) | `efficiency` | `completeness` |

Severity maps to score: critical=0.0, high=0.2, medium=0.5, low=0.8, no finding=1.0.

**Step 3 — Write evaluation JSON.** One file per attributable finding:

```json
{
  "id": "eval-drift-coredrift-{task_id}-{timestamp}",
  "task_id": "fix-auth-flow",
  "role_id": "abc123",
  "tradeoff_id": "unknown",
  "score": 0.2,
  "dimensions": {
    "correctness": 0.2,
    "completeness": 0.5,
    "style_adherence": 1.0,
    "downstream_usability": 1.0,
    "coordination_overhead": 1.0,
    "blocking_impact": 0.3,
    "efficiency": 1.0
  },
  "notes": "coredrift: Task implementation diverges from contract scope. Finding: auth flow adds OAuth provider selection not specified in task description.",
  "evaluator": "speedrift:coredrift",
  "timestamp": "2026-03-15T14:30:00Z",
  "source": "drift"
}
```

Key fields: `evaluator` is always `speedrift:<lane_name>`, `source` is always `"drift"` (distinct from `"llm"` which is WG's internal evaluations). This lets the evolver distinguish our signal if it ever wants to weight differently.

### What the Bridge Does Not Do

- **No aggregation** — one finding, one evaluation. The evolver handles aggregation.
- **No duplicate suppression** — if the same finding fires on consecutive cycles, it writes consecutive evaluations. Persistent drift = lower running average. That's signal, not noise.
- **No direct communication with the evolver** — we write files, it reads them on its schedule.

### Module

`driftdriver/wg_eval_bridge.py` with:
```python
def bridge_findings_to_evaluations(
    repo_path: Path,
    lane_results: list[LaneResult],
) -> BridgeReport
```

Called by factorydrift after lane execution completes.

---

## 4. Component: NorthStarDrift v2 — Strategic Alignment

### What Changes

NorthStarDrift v1 is an ecosystem health scorecard (continuity, autonomy, quality, coordination, self_improvement axes with weighted scoring, daily/weekly rollups, trend detection, operator prompts, review task emission). It stays — that's valuable operational intelligence. v2 adds a **strategic alignment layer** that asks whether work is advancing the project's declared purpose.

### North Star Declaration

Each repo declares its North Star in `drift-policy.toml`:

```toml
[northstar]
statement = "The system understands your relationships the way you would if you had perfect memory and unlimited attention"
keywords = ["relationships", "context", "memory", "presence", "genuine", "texture"]
anti_patterns = ["pipeline", "funnel", "deal stages", "conversion rates", "lead scoring"]
last_reviewed = "2026-03-06"
review_interval_days = 30
```

If no declaration exists, the lane checks `README.md`, `CLAUDE.md`, and existing `repo_north_star` data (v1 already does presence detection). Missing declarations become a finding with `human_approval_required = true`.

### Alignment Check (per-repo, per-cycle)

**Inputs:**
- The North Star declaration
- Recent completed tasks from workgraph (last N tasks or last 7 days)
- Recent git commits (last 7 days, commit messages + changed file paths)

**Analysis (LLM-driven, Haiku):**

The lane sends a focused prompt:

> Given this North Star: "{statement}"
> And these keywords: {keywords}
> And these anti-patterns: {anti_patterns}
>
> Rate the alignment of each recently completed task (0.0-1.0) and provide a one-sentence rationale. Then give an overall alignment score and identify any implicit drift.

**Outputs:**
- Per-task alignment scores (0.0-1.0) with rationale
- Overall alignment ratio: (aligned tasks) / (total completed tasks)
- Implicit drift detection: "Recent work clusters around {theme} which is not in the declared North Star"
- Stale North Star flag: `last_reviewed` older than `review_interval_days`

### As a Graph Checkpoint

When the Planner inserts NorthStar checkpoints into a workgraph, those checkpoints are tasks that invoke NorthStarDrift v2's alignment check scoped to *this graph's completed work* rather than the whole repo. The checkpoint task:

1. Reads the North Star
2. Reviews tasks completed so far in this execution
3. Scores alignment
4. If aligned (score > 0.7): marks itself done, execution continues
5. If drifting (score 0.4-0.7): emits a finding, adds a log message to downstream tasks with the drift warning, continues
6. If lost (score < 0.4): writes `intent: "needs_human"` to the decision queue with context and options, pauses remaining graph until human responds
7. If the Planner is available (runtime mode): the Planner evaluates whether remaining tasks need restructuring based on the checkpoint's findings

### Bridge Integration

Attributable alignment findings flow through the bridge. When an agent completes a task that NorthStarDrift scores as misaligned, the bridge writes an evaluation with a low `strategic_alignment` dimension. The evolver sees "this role keeps doing off-target work" and applies evolutionary pressure — the agent literally gets better at staying on mission.

### What Doesn't Change

v1's operational health scoring (5 axes, trends, daily/weekly rollups, operator prompts, review task emission) stays exactly as-is. v2 adds a new `alignment` section to the northstardrift output alongside the existing `axes` and `repo_scores`.

---

## 5. Component: evolverdrift — Closing the Loop

### Purpose

Monitor WG's evolver to verify that Speedrift's quality signals are being consumed and that evolution is actually improving things. Without this, we're feeding evaluations into a black box.

### What It Checks

Five checks, in order of importance:

**1. Liveness — Is the evolver running?**

Look for evolve-run directories in `.workgraph/evolve-runs/`. If the most recent run is older than a configurable threshold (default 7 days) and there are unprocessed drift evaluations (files with `source: "drift"` newer than the last run), emit a finding: "Evolver has not run in {N} days. {M} drift evaluations await processing."

Severity: `warning` if 7-14 days, `high` if 14+ days.

**2. Consumption — Are our evaluations being picked up?**

Read the most recent evolve-run's slice data files (`*-slice.json`). Search for evaluations with `evaluator: "speedrift:*"`. If we've written drift evaluations but none appear in any slice, our signal isn't reaching the evolver's partitioner.

Severity: `warning`. Finding includes count of unprocessed drift evaluations.

**3. Impact — Did evolution improve drift rates?**

After a role mutation triggered (at least partly) by drift evaluations, compare drift finding rates for that role's agents before and after the mutation. This requires:
- Reading `evolve-runs/{run_id}/synthesis-result.json` to find which roles were mutated
- Reading `evolve-runs/{run_id}/apply-results.json` to confirm operations applied
- Comparing drift evaluation scores for those roles across a time window (pre-mutation vs post-mutation)

If drift scores improved (fewer/lower severity findings post-mutation): emit `info` finding. If no change: `info` noting the mutation had no observable effect. If worse: `warning` noting regression.

**4. Regression — Did evolution make things worse?**

After a role mutation, did drift finding severity *increase* for that role's agents? This is a subset of impact tracking but elevated to its own check because it's actionable — the evolver made a bad call and the next round of evaluations should push back.

Severity: `high` if post-mutation drift scores are 0.2+ lower than pre-mutation average.

**5. Deferred Queue — Are there unanswered decisions?**

The evolver defers self-mutations (changes to its own role) for human approval in `.workgraph/agency/deferred/`. If deferred operations are sitting unanswered for more than 48 hours, surface them.

Severity: `warning`. Integrates with the decision queue from continuation intent — deferred evolver operations become decisions in our queue.

### Lane Contract

evolverdrift implements `run_as_lane(project_dir) -> LaneResult` like every other lane. Registered in factorydrift's lane roster, runs as part of the standard drift check cycle.

### WG Failure Mode Workarounds

evolverdrift also absorbs graph health checks identified during ecosystem-wide investigation (March 15, 2026). These workaround detections pair with factory brain directives for mitigation:

- **Orphaned in-progress tasks**: detect tasks stuck in-progress with no alive agent. Filed upstream as [graphwork/workgraph#5](https://github.com/graphwork/workgraph/issues/5). Emit finding; factory brain can unclaim via `wg` CLI as workaround.
- **Deadlocked daemon**: detect daemon socket unresponsive. Filed upstream as [graphwork/workgraph#6](https://github.com/graphwork/workgraph/issues/6). Emit `critical` finding; factory brain can kill and restart.
- **Graph corruption**: detect duplicate node IDs, orphan dependency refs. Filed upstream as [graphwork/workgraph#7](https://github.com/graphwork/workgraph/issues/7). Emit findings with counts.

These workarounds naturally retire as Erik ships fixes. The detection checks remain as regression monitors.

---

## 6. Component: Speedrift Planner

### Purpose

Take a spec or implementation plan and produce a workgraph structured with quality intelligence — tactical quality gates and strategic checkpoints — so that agents don't need to be manually told to test, evaluate, and course-correct.

### Entry Points

**Plan-time (human-triggered):**
```bash
driftdriver plan <spec-or-plan-file> --repo <path> [--dry-run]
```

Reads the spec, produces a workgraph with quality patterns woven in, writes tasks via `wg add`. The `--dry-run` flag shows the graph structure without creating anything.

**Runtime (brain-triggered):**

When a NorthStar checkpoint fires and determines the remaining graph needs restructuring, the factory brain invokes the Planner with the checkpoint's findings and the remaining undispatched tasks. The Planner produces graph modifications (add/remove/reorder tasks) issued as directives through the existing directive interface.

### How It Works

The Planner is a single LLM call (Sonnet) with a structured prompt. It is not a template stamper — it uses judgment.

**Context provided:**
- The spec/plan (what we're building and why)
- The repo's North Star declaration (from `drift-policy.toml`)
- The repo's drift policy (which lanes are enabled, what severity thresholds matter)
- The Planner's repertoire (the quality patterns it knows about)

**Judgment the Planner makes:**
- What are the implementation phases? Where are the natural boundaries?
- What kind of work is each task? (code, UI, data, API, infrastructure, config)
- What's the risk profile of each task? (destructive migration vs additive feature, user-facing vs internal)
- Which quality patterns apply, and how? (a trivial config change doesn't need a full break/fix loop; a destructive data migration needs extra validation)
- Where should NorthStar checkpoints go? (phase boundaries, after significant directional decisions, not after every task)

**Output:** Structured JSON that maps to `wg add` commands:

```json
{
  "tasks": [
    {
      "id": "implement-auth-flow",
      "title": "Implement OAuth auth flow",
      "after": [],
      "type": "code",
      "risk": "medium"
    },
    {
      "id": "e2e-auth-flow",
      "title": "E2E test: auth flow break/fix",
      "after": ["implement-auth-flow"],
      "type": "quality-gate",
      "pattern": "e2e-breakfix",
      "max_iterations": 3,
      "description": "Run auth flow E2E tests. If failures, diagnose and fix. Loop until green or max iterations."
    },
    {
      "id": "northstar-check-phase-1",
      "title": "NorthStar alignment: phase 1 complete",
      "after": ["e2e-auth-flow", "e2e-dashboard"],
      "type": "northstar-checkpoint",
      "description": "Review phase 1 work against North Star. Assess alignment. If drifting, restructure phase 2."
    }
  ]
}
```

### The Repertoire

The Planner's prompt includes its repertoire — quality patterns it can draw from. These are not templates but documented patterns with guidance on when they're appropriate. The Planner uses judgment about when and how to apply them.

**e2e-breakfix** — Run end-to-end tests, diagnose failures, fix, retest. Use for: any code that has testable behavior. Structure: implement -> test -> [fail? -> fix -> retest, max N] -> proceed. The fix task gets the test output as context.

**ux-eval** — Evaluate UI against UX criteria (accessibility, responsiveness, interaction patterns). Use for: user-facing changes. Structure: implement -> UX eval -> [issues? -> fix -> re-eval, max N] -> proceed. Can use Playwright for automated checks.

**data-eval** — Validate data model changes against integrity constraints, migration safety, rollback capability. Use for: schema changes, migrations, data pipeline changes. Structure: implement -> validate schema + run migration dry-run -> [issues? -> fix -> re-validate] -> proceed.

**contract-test** — Verify API contracts match spec. Use for: API endpoints, inter-service communication. Structure: implement -> contract test -> [drift? -> fix -> retest] -> proceed.

**northstar-checkpoint** — Invoke NorthStarDrift v2 alignment check scoped to this graph's completed work. Use for: phase boundaries, after significant directional decisions. Structure: assess alignment -> [aligned? proceed | drifting? warn + continue | lost? pause + escalate to decision queue | restructure needed? invoke Planner runtime mode].

**The repertoire is extensible.** When new drift lanes ship (securitydrift, mobiledrift, uxmodeldrift), their companion quality patterns are added to the repertoire. The Planner automatically has them available on its next invocation.

### What the Planner Is Not

- **Not a product thinker** — the spec/brainstorm happened before the Planner runs
- **Not a WG coordinator** — it doesn't dispatch tasks, it structures the graph
- **Not deterministic** — it uses judgment about which patterns to apply and where
- **Not a replacement for `wg add`** — manual task creation still works. The Planner is for when you want quality intelligence baked in automatically

---

## 7. Integration & Data Flow

### End-to-End Example

A concrete walkthrough. The human brainstorms and specs a feature for LFW: "Add relationship health indicators to the actor detail page."

**1. Planner (plan-time):**

```
driftdriver plan docs/specs/relationship-health-indicators.md --repo /path/to/lfw
```

Planner reads the spec, reads LFW's North Star ("understands your relationships the way you would if you had perfect memory"), reads the drift policy. Produces:

```
implement-health-score-model
    -> data-eval-health-schema          (data-eval: validate migration)
    -> implement-health-api-endpoint
    -> contract-test-health-api         (contract-test)
    -> implement-health-ui-component
    -> e2e-health-ui                    (e2e-breakfix)
    -> ux-eval-health-indicators        (ux-eval)
    -> northstar-check-phase-1          (checkpoint: phase 1 complete)
    -> implement-health-notifications
    -> e2e-notifications
    -> northstar-check-final
```

Tasks written to workgraph via `wg add`. Service starts dispatching.

**2. Agents execute, hit quality gates:**

Agent implements `health-score-model`. Next task is `data-eval-health-schema` — agent runs the migration dry-run, finds a missing index. Break/fix loop: creates the index, re-validates, passes.

Agent implements the UI component. `e2e-health-ui` runs Playwright tests, finds the indicator doesn't render on mobile viewports. Fix task runs, agent adjusts the CSS. Retest passes.

**3. NorthStar checkpoint fires:**

`northstar-check-phase-1` runs NorthStarDrift v2's alignment check scoped to this graph. Reads the North Star, reviews the 6 completed tasks. Score: 0.85 — "Health indicators directly serve the North Star by making relationship quality visible." Proceeds.

**4. Drift lanes run:**

After tasks complete, the regular drift cycle runs. qadrift finds a style violation in the API endpoint. coredrift finds the notification implementation added a feature not in the spec (scope creep). These produce `LaneFinding` objects.

**5. Bridge translates to evaluations:**

Bridge looks up `implement-health-notifications` -> assignment YAML -> `agent_id: abc123` -> `role_id: xyz789`. Writes evaluation with `evaluator: "speedrift:coredrift"`, `source: "drift"`, `score: 0.3`, `dimensions.correctness: 0.3`.

**6. Evolver picks up on next run:**

The evolver's partition step loads all evaluations including drift-sourced ones. The mutation analyzer sees role `xyz789` has low correctness scores from coredrift. Proposes a wording mutation to strengthen the role's scope-adherence instructions. Synthesizer accepts. Role gets updated.

**7. evolverdrift monitors:**

Next drift cycle, evolverdrift checks: did the mutation help? Compares pre/post drift finding rates for agents using role `xyz789`. If coredrift violations decreased: emit info finding. If not: emit warning, and the next bridge evaluation continues applying evolutionary pressure.

### Shared Contracts

| Interface | Format | Producer | Consumer |
|-----------|--------|----------|----------|
| Lane findings | `LaneResult` (speedrift-lane-sdk) | All drift lanes | Bridge, factorydrift, factory brain |
| WG evaluations | JSON in `.workgraph/agency/evaluations/` | Bridge (+ WG's own evaluator) | WG evolver |
| Evolve run artifacts | JSON in `.workgraph/evolve-runs/` | WG evolver | evolverdrift |
| Assignments | YAML in `.workgraph/agency/assignments/` | WG coordinator | Bridge (for attribution) |
| North Star declaration | TOML in `drift-policy.toml` | Human | NorthStarDrift v2, Planner |
| Planner output | JSON task list | Planner | `wg add` (via CLI) |
| Directives | JSON (existing schema) | Factory brain, Planner (runtime) | WG (via CLI) |
| Decision queue | JSONL (continuation intent) | NorthStar checkpoints, evolverdrift | Factory brain, Telegram, dashboard |

### Ownership Boundary

| Concern | Owner | Boundary |
|---------|-------|----------|
| Quality judgment | Speedrift | Drift lanes produce findings |
| Graph structure intelligence | Speedrift (Planner) | Planner writes tasks via `wg add` |
| Evaluation production | Speedrift (Bridge) | Bridge writes JSON files |
| Evolver monitoring | Speedrift (evolverdrift) | Reads evolver output, writes findings |
| Task execution | WG | Coordinator dispatches, agents execute |
| Agent identity and evolution | WG | Evolver reads evaluations, mutates roles |
| Graph operations | WG | `wg` CLI is the execution interface |

---

## 8. Phasing & Configuration

### Build Order

The components have dependencies. Ship in this order:

**Phase 1 — Bridge** (foundation, immediate value)
- `driftdriver/wg_eval_bridge.py`: finding attribution, dimension mapping, evaluation writing
- No dependencies on other new components
- Immediate payoff: every existing drift lane's findings start feeding the evolver
- Test: run drift check, verify evaluation files appear in `.workgraph/agency/evaluations/`

**Phase 2 — evolverdrift** (close the monitoring loop)
- Depends on: Bridge (needs drift evaluations flowing to have something to monitor)
- Implements `run_as_lane()`, registered in factorydrift
- Includes WG failure mode workarounds (orphaned tasks, deadlocked daemons, graph corruption)
- Test: mock evolve-run artifacts, verify findings for liveness/consumption/impact/regression

**Phase 3 — NorthStarDrift v2** (strategic alignment)
- Depends on: Bridge (alignment findings need to flow as evaluations)
- Extends existing `northstardrift.py` with alignment layer
- Adds `[northstar]` section to drift-policy.toml schema
- Adds LLM-driven alignment scoring (Haiku)
- Test: repo with declared North Star + completed tasks, verify alignment scores

**Phase 4 — Planner** (quality-aware graph structuring)
- Depends on: NorthStarDrift v2 (for meaningful checkpoints), Bridge (so quality gates produce evaluations)
- `driftdriver/planner.py`: spec reader, repertoire, LLM-driven graph structuring
- Runtime mode: brain integration for graph evolution at checkpoints
- Test: give it a spec, verify output graph has appropriate quality patterns

### Configuration

All new configuration lives in `drift-policy.toml` (per-repo), extending the existing schema. All new sections are optional — existing repos continue working with sensible defaults.

```toml
# Existing lane configuration
[lanes]
coredrift = true
qadrift = true
secdrift = true

# New: Bridge
[bridge]
enabled = true
attribution_strategy = "assignment"  # "assignment" | "git-blame" | "both"
min_severity = "medium"              # don't bridge low/info findings

# New: evolverdrift
[lanes.evolverdrift]
enabled = true
evolver_stale_days = 7               # warn if evolver hasn't run
impact_window_days = 14              # compare drift rates in this window
regression_threshold = 0.2           # score drop that triggers regression finding

# New: North Star alignment (extends existing northstar section)
[northstar]
statement = "..."
keywords = ["..."]
anti_patterns = ["..."]
last_reviewed = "2026-03-06"
review_interval_days = 30
alignment_model = "haiku"            # LLM for alignment scoring
alignment_threshold_proceed = 0.7    # checkpoint proceeds
alignment_threshold_pause = 0.4     # checkpoint escalates to decision queue

# New: Planner
[planner]
enabled = true
model = "sonnet"                     # LLM for graph structuring
breakfix_max_iterations = 3          # default cap on break/fix loops
checkpoint_interval = 5              # NorthStar checkpoint every N implementation tasks
```

### Factory Brain Integration

The brain's tick loop gains two new capabilities, both using the existing directive interface:

- **WG workaround directives**: when evolverdrift detects orphaned tasks or deadlocked daemons, the brain issues `unclaim_task` or `restart_service` directives as workarounds. These retire naturally when Erik ships fixes for [#5](https://github.com/graphwork/workgraph/issues/5), [#6](https://github.com/graphwork/workgraph/issues/6), [#7](https://github.com/graphwork/workgraph/issues/7).
- **Planner runtime invocation**: when a NorthStar checkpoint escalates (score < 0.4 or requests graph restructuring), the brain invokes the Planner in runtime mode. The Planner produces graph modifications, the brain issues them as directives.

---

## 9. Future Lanes

The architecture is designed to grow. Each new drift lane automatically:
- Produces findings that flow through the Bridge as evaluations
- Gets a companion quality pattern in the Planner's repertoire
- Deepens Speedrift's quality moat in domains WG will never touch

Planned lanes (not in scope for this design):
- **securitydrift / pendrift**: OWASP Top 10, vulnerability scanning, pen-testing posture
- **uxmodeldrift**: standard UX pattern compliance (accessibility, interaction patterns)
- **mobiledrift**: mobile-first planning, responsive layout quality, thumb zone compliance
