# Dark Factory Feedback Loop Design

**Date:** 2026-03-20
**Status:** Approved — pending implementation plan
**Scope:** Complete feedback loop architecture between speedrift (macro judgment) and workgraph/agency (execution + micro judgment), including autonomous project creation and upstream dependency tracking

---

## North Star

The dark factory answers three questions, always:

1. **Are we operationally healthy?** (alive/dead/stale/moving)
2. **Are we improving?** (code, repos, and the factory itself)
3. **Are we drifting from intent?** (spec, code quality, UX, dead code — especially critical under autonomous operation)

It operates in three modes:

| Mode | Input | Output |
|------|-------|--------|
| **Maintain** | Existing repo | Drift checks, self-cleaning, conformance |
| **Improve** | Existing repo + identified gaps | Tasks, execution, convergence toward target state |
| **Create** | North star declaration | New repo → scaffold → code → converged |

The goal is a self-sustaining system that creates, improves, and maintains projects with minimal human intervention — surfacing only what genuinely requires human judgment.

---

## Architecture

No new layers. The existing stack gains wiring and a thin adapter:

```
Hub (8777)
  — visualizes the three questions, always live
  — conformance panel, upstream card, creation pipeline

Driftdriver
  — judges: drift checks, quality, conformance, governance
  — agency_adapter.py: thin bidirectional wire to workgraph/agency

Workgraph / Agency (graphwork/workgraph + agentbureau/agency)
  — executes: tasks carry desired outcomes, evaluated on completion
  — agency evaluations feed back into northstardrift

Factory Brain (Haiku -> Sonnet -> Opus tiers)
  — orchestrates: dispatches agents, escalates, learns from scores

Design Panel (Synthyra Panel mechanism)
  — activated for complex north star declarations
  — multi-specialist decomposition before attractor loop starts
```

**Architectural invariants:**
- Speedrift and agency never reference each other directly — `agency_adapter.py` is the only coupling point
- All new components are additive — no existing lane or system is modified
- Model never invents data — it interprets deterministic inputs and proposes bounded actions
- Erik/Vaughn upstream changes require only adapter updates, not architectural changes

---

## Component 1: Agency Adapter (Bidirectional Wire)

**File:** `driftdriver/agency_adapter.py` (~150 lines, no new lane)

### Outbound — Speedrift writes into Agency

When speedrift emits a task into workgraph, the adapter stamps three fields onto the task:

```
desired_outcome:  pulled from the repo's NORTH_STAR.md outcome target
tradeoff_config:  derived from the repo's drift-policy.toml constraints
lifecycle:        active / maintenance / experimental (from ecosystem.toml)
```

Agency reads these when composing the agent for that task. The executing agent is grounded in declared repo intent, not generic instructions.

**Example flow:**
```
northstardrift generates task: "remove dead code in paia-shell"
  desired_outcome = "lean, maintainable shell service at declared scope"
  tradeoff_config = drift-policy.toml -> yagni weight, test coverage floor
  -> workgraph task record carries these fields
  -> agency composes agent with this grounding
```

### Inbound — Agency writes into Speedrift

When a task completes, agency writes an evaluation score back to the task record. The adapter:
1. Reads completed task evaluations (polling the wg graph JSONL)
2. Aggregates scores per repo over a rolling window
3. Writes aggregated metrics to the hub snapshot as northstardrift inputs

These feed the **self-improvement axis** of northstardrift's 6-axis score:
- "Are the tasks speedrift generates actually producing good outcomes?"
- Rolling average of agency evaluation scores per repo
- Trend: improving / stable / degrading

### Mapping

| Speedrift concept | Agency primitive |
|-------------------|-----------------|
| North star outcome target | Desired outcome |
| drift-policy.toml | Trade-off config |
| Attractor convergence check | Agency evaluator |
| Factory brain evolver | Agency evolver |

---

## Component 2: Autonomous Project Creation

### Intake Mechanism

A new project is declared by dropping a `NORTH_STAR.md` into `/factory/intake/<project-name>/`. The factory picks this up on the next ecosystem cycle.

Required fields in the north star file:
```markdown
# North Star — <project-name>

One paragraph: what this is and what it exists to do.

## Outcome target
One concrete, measurable statement of what "done well" looks like.

## Current phase
`onboarded` (all new projects start here)

## Complexity hints (optional)
- domain_count: N
- has_external_integrations: true/false
- estimated_loc: N
```

### Routing by Complexity

The factory computes a `complexity_score` from the north star declaration:

```
complexity_score =
  0.4 * domain_count_normalized +
  0.3 * has_external_integrations +
  0.2 * estimated_loc_normalized +
  0.1 * dependency_count_normalized
```

**Simple path** (complexity_score < 0.5):
```
NORTH_STAR.md declared
  -> git init + scaffold (workgraph, drift-policy, basic structure)
  -> attractor task created: converge <project-name> -> production-ready
  -> attractor loop runs: diagnose -> plan -> execute -> re-diagnose
```

**Complex path** (complexity_score >= 0.5):
```
NORTH_STAR.md declared
  -> complexity_score computed
  -> Design Panel invoked (see below)
  -> panel produces decomposed_plan.md + pre-seeded wg tasks
  -> attractor loop executes against those tasks
```

### Design Panel

The Design Panel is the Synthyra Panel / Expert Panel mechanism applied to software development. It is invoked as a moderated multi-specialist session, not a new daemon.

**Specialist roles:**
- **Architect** — system design, component boundaries, integration patterns
- **UX Critic** — user experience quality, interaction patterns, surface area
- **Security Reviewer** — attack surface, auth patterns, data handling
- **Domain Expert** — business logic correctness, domain model fidelity
- **Contrarian** — challenges assumptions, identifies overbuilding and gaps

**Protocol:**
1. Each specialist receives the north star and writes their perspective to a transcript
2. Sonnet moderator validates quality (>100 words, specific analysis, identified risks)
3. Moderator synthesizes into `decomposed_plan.md` with workgraph tasks pre-loaded
4. Attractor loop executes against those tasks

**The seam** — the Design Panel is invoked as a wg task with a specialized executor. It does not require a new daemon or infrastructure. Output is a set of pre-seeded wg tasks in the new repo's workgraph.

### Hub Visibility

New projects appear in a "creation pipeline" section on the hub:

```
Status progression: intake -> scaffolded -> design-panel -> building -> converged
```

Same three questions apply to new projects as to existing ones.

---

## Component 3: Upstream Tracker

A wg cycle task — not a daemon, not a lane. Runs weekly by default; triggerable on-demand from the hub.

### Pass 1 — External Dependencies

Tracks repos we depend on but do not own:

| Repo | What to watch |
|------|---------------|
| `graphwork/workgraph` | All active branches: `fix-toctou-race`, `infra-fix-toctou`, `fix-auto-task-edges`, main |
| `agentbureau/agency` | main |

**Evaluation cycle:**
```
1. git fetch — no auto-merge
2. Diff vs last-known-good SHA in .driftdriver/upstream-pins.toml
3. Classify changes: schema, API surface, behavior, internals-only
4. Haiku triage: relevance score 0-1
5. If relevance > 0.3: Sonnet deep eval
   -> what changed, impact on agency_adapter.py and driftdriver integration
   -> value gained, risk introduced
   -> recommended action: adopt / watch / ignore
6. Route by risk_score:
   - < 0.4: auto-create adoption task in wg, update upstream-pins.toml
   - >= 0.4: alert Braydon via hub notification, attach full eval, await approval
7. If approved: create adoption task
8. If snoozed: record snooze + expiry in upstream-pins.toml
```

**Storage:** `.driftdriver/upstream-pins.toml`
```toml
[graphwork.workgraph]
main = "abc123"
fix-toctou-race = "def456"
infra-fix-toctou = "ghi789"

[agentbureau.agency]
main = "jkl012"

[snoozed]
"graphwork/workgraph:fix-before-edges" = { until = "2026-04-01", reason = "TUI-only, no impact" }
```

### Pass 2 — Internal Repos Drifting from Origin

Scans all enrolled repos for:
- Ahead of origin (uncommitted work not pushed)
- Dirty working trees with substantial changes

Surfaces these as `unpushed-work` findings in the governancedrift conformance panel. These are not new infrastructure — they plug into the existing finding categories and hub display.

### Hub Surface

Small "upstream" card on the dashboard:
- Last checked timestamp
- Pending adoptions (with risk scores)
- Snoozed items (with expiry)
- Adoption history (last 10)
- Internal repos with unpushed work (count + link to conformance panel)

---

## Data Flow

```
NORTH_STAR.md (declared intent)
  |
  v
agency_adapter.py
  |-- outbound: stamp desired_outcome + tradeoff_config onto wg tasks
  |
  v
workgraph (execution)
  |-- agency evaluates each completed task
  |-- writes evaluation score to task record
  |
  v
agency_adapter.py
  |-- inbound: aggregate evaluation scores per repo
  |-- write to hub snapshot as self-improvement axis inputs
  |
  v
northstardrift
  |-- reads self-improvement axis inputs
  |-- updates 6-axis score
  |-- hub displays: are we improving?
  |
  v
Hub (8777)
  |-- three questions: healthy? improving? drifting?
  |-- creation pipeline: intake -> converged
  |-- upstream card: external deps + internal drift
  |-- conformance panel: violations + unpushed-work findings
```

---

## What Does Not Change

- workgraph internals (graph.jsonl, coordinator, executor, wg CLI)
- coredrift, specdrift, qadrift, secdrift, plandrift, governancedrift
- northstardrift formula (self-improvement axis inputs are added, not replaced)
- factory brain tier structure
- intelligence inbox structure
- Hub WebSocket and federation
- Existing 6-axis scoring (self-improvement axis gains new inputs, formula unchanged)

---

## Testing

TDD throughout. Tests written before implementation.

### agency_adapter.py — unit tests

- Outbound: given a task + north star file + drift-policy → assert correct desired_outcome and tradeoff_config stamped on task record
- Inbound: given completed task evaluation JSONL → assert correct score aggregation and hub snapshot update
- Edge cases: north star missing, drift-policy missing, no completed evaluations yet, evaluation score = 0

### Design Panel — integration tests

- complexity_score routing: low score → attractor directly (no panel invoked); high score → panel invoked
- Panel output: given north star + specialist transcripts → assert decomposed_plan.md produced + wg tasks pre-seeded
- Quality gate: specialist transcript < 100 words → moderator re-invokes before proceeding

### Upstream Tracker — unit tests

- Pass 1: given diff + upstream-pins.toml → assert correct classification (schema/API/behavior/internal)
- Risk routing: risk_score < 0.4 → adoption task created; risk_score >= 0.4 → alert emitted, no task
- Snooze: snoozed entry with future expiry → assert branch skipped in this cycle
- Pass 2: given repo states → assert correct `unpushed-work` findings emitted

### End-to-end scenario tests

1. **New project from north star (simple)**: drop NORTH_STAR.md with low complexity → scaffolded repo → attractor loop running → hub shows "building"
2. **New project from north star (complex)**: high complexity → design panel invoked → specialists produce transcripts → decomposed plan → attractor loop → hub shows progression
3. **Agency evaluation feedback**: task completes with high evaluation score → score aggregated → northstardrift self-improvement axis improves → hub reflects improvement
4. **Upstream adoption**: external repo has low-risk change → auto-adoption task created → upstream-pins.toml updated → hub adoption history updated
5. **Upstream alert**: high-risk change → Braydon notified → snooze option → adoption deferred

---

## Implementation Sequence

1. `agency_adapter.py` — outbound wire (stamp desired outcomes on tasks)
2. `agency_adapter.py` — inbound wire (read evaluations, feed self-improvement axis)
3. Upstream tracker — Pass 1 (external deps, model-mediated eval, risk routing)
4. Upstream tracker — Pass 2 (internal repos, unpushed-work findings)
5. Project creation — intake mechanism + complexity routing + simple attractor path
6. Design Panel — Synthyra Panel mechanism wired to complex creation path
7. Hub surface — creation pipeline section + upstream card updates

Each step is independently testable and deployable.
